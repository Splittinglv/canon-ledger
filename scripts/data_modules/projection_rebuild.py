#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministically rebuild every derived read model from canonical commits.

The chapter commit files are the write-side source of truth.  A projection
rebuild is deliberately corpus-wide: state, entity current values, lifecycle
memory and relationship snapshots all depend on the ordered prefix of
accepted commits, so replaying one chapter in isolation cannot remove facts
from a replaced revision safely.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock

from .chapter_content_binding import verify_commit_content_binding
from .commit_artifacts import extraction_list, extraction_text
from .commit_lineage import (
    VALIDATION_NEEDS_REVALIDATION,
    canonical_snapshot_hash,
    stamp_and_partition_commits,
)
from .config import DataModulesConfig
from .event_log_store import EventLogStore
from .event_projection_router import EventProjectionRouter
from .override_ledger_service import (
    AmendProposalTrigger,
    ensure_override_ledger_columns,
    persist_amend_proposals,
)
from .projection_log import append_projection_run
from .story_contracts import read_json_if_exists, write_json


MANIFEST_SCHEMA = "canon-ledger-projection-manifest/v1"
REBUILD_SCHEMA = "canon-ledger-projection-rebuild/v1"
PROJECTION_MANIFEST_REL = Path(".canon-ledger") / "projection_manifest.json"
REBUILD_STATUS_REL = Path(".canon-ledger") / "projection_rebuild.json"
PROJECTION_STATUS = {
    "state": "pending",
    "index": "pending",
    "summary": "pending",
    "memory": "pending",
    "vector": "pending",
}


class ProjectionRebuildError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = str(code or "projection_rebuild_failed")
        self.detail = str(detail or "")
        super().__init__(f"{self.code}:{self.detail}" if self.detail else self.code)


def projection_manifest_path(project_root: str | Path) -> Path:
    return Path(project_root) / PROJECTION_MANIFEST_REL


def _commit_dir(project_root: Path) -> Path:
    return project_root / ".story-system" / "commits"


def _chapter_from_filename(path: Path) -> int:
    stem = path.name
    prefix = "chapter_"
    suffix = ".commit.json"
    if not stem.startswith(prefix) or not stem.endswith(suffix):
        return 0
    try:
        return int(stem[len(prefix) : -len(suffix)])
    except (TypeError, ValueError):
        return 0


def load_canonical_commits(
    project_root: str | Path,
    *,
    validate_bindings: bool = True,
) -> list[dict[str, Any]]:
    """Load the one current canonical commit for every chapter, in order.

    The filename and ``meta.chapter`` must agree.  Callers that use facts for
    writing should keep binding validation enabled so a commit for edited
    prose is never treated as authoritative.
    """
    root = Path(project_root).expanduser().resolve()
    commits: list[dict[str, Any]] = []
    seen: set[int] = set()
    for path in sorted(_commit_dir(root).glob("chapter_*.commit.json")):
        filename_chapter = _chapter_from_filename(path)
        if filename_chapter <= 0:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectionRebuildError("invalid_commit_json", f"{path.name}:{exc}") from exc
        if not isinstance(payload, dict):
            raise ProjectionRebuildError("commit_not_object", path.name)
        try:
            meta_chapter = int((payload.get("meta") or {}).get("chapter") or 0)
        except (AttributeError, TypeError, ValueError):
            meta_chapter = 0
        if meta_chapter != filename_chapter:
            raise ProjectionRebuildError("commit_chapter_mismatch", path.name)
        if meta_chapter in seen:
            raise ProjectionRebuildError("duplicate_canonical_commit", str(meta_chapter))
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            raise ProjectionRebuildError("invalid_commit_status", f"{meta_chapter}:{status}")
        if validate_bindings:
            ok, code = verify_commit_content_binding(root, meta_chapter, payload)
            if not ok:
                raise ProjectionRebuildError(code, str(meta_chapter))
        seen.add(meta_chapter)
        commits.append(payload)
    commits.sort(key=lambda item: int((item.get("meta") or {}).get("chapter") or 0))
    return commits


def _manifest_entries(commits: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(int((payload.get("meta") or {}).get("chapter") or 0)): canonical_snapshot_hash(payload)
        for payload in commits
    }


def projection_coverage_gaps(
    project_root: str | Path,
    *,
    before_chapter: int | None = None,
) -> list[dict[str, Any]]:
    """Accepted commits whose facts are not reflected by installed read models.

    A crash or writer failure can leave an accepted commit on disk while the
    projection manifest never records it.  Canonical history would then include
    facts that every derived read model is missing.  This check makes that
    fracture detectable (write gates) and repairable (rebuild triggers).
    """
    root = Path(project_root).expanduser().resolve()
    manifest = read_json_if_exists(projection_manifest_path(root))
    chapters: dict[str, Any] = {}
    if isinstance(manifest, dict) and manifest.get("schema_version") == MANIFEST_SCHEMA:
        raw_chapters = manifest.get("chapters")
        if isinstance(raw_chapters, dict):
            chapters = raw_chapters
    gaps: list[dict[str, Any]] = []
    for path in sorted(_commit_dir(root).glob("chapter_*.commit.json")):
        chapter = _chapter_from_filename(path)
        if chapter <= 0:
            continue
        if before_chapter is not None and chapter >= int(before_chapter):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            gaps.append({"chapter": chapter, "reason": "invalid_commit_json"})
            continue
        if not isinstance(payload, dict):
            gaps.append({"chapter": chapter, "reason": "commit_not_object"})
            continue
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        if str(meta.get("status") or "") != "accepted":
            continue
        if str(meta.get("validation_status") or "") == VALIDATION_NEEDS_REVALIDATION:
            # A rewritten-prefix chapter is handled by the revalidation gate,
            # not by projection retries.
            continue
        recorded = str(chapters.get(str(chapter)) or "")
        if not recorded:
            gaps.append({"chapter": chapter, "reason": "projection_not_recorded"})
        elif recorded != canonical_snapshot_hash(payload):
            gaps.append({"chapter": chapter, "reason": "projection_snapshot_mismatch"})
    return gaps


def projection_snapshot_requires_rebuild(
    project_root: str | Path,
    payload: dict[str, Any],
) -> bool:
    """Return true for an untracked corpus or a replaced chapter snapshot."""
    path = projection_manifest_path(project_root)
    manifest = read_json_if_exists(path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA:
        root = Path(project_root)
        # A genuinely fresh first chapter has no stale aggregate state to
        # remove, so it may use the normal append path and create the first
        # manifest entry afterwards.  Any pre-existing projection artifact or
        # multi-commit corpus is an upgraded project with unknown provenance;
        # rebuild it once instead of blessing possibly stale data.
        commit_count = len(list(_commit_dir(root).glob("chapter_*.commit.json")))
        canon_ledger = root / ".canon-ledger"
        story_events = root / ".story-system" / "events"
        has_projection_files = any(
            (
                (canon_ledger / "index.db").is_file(),
                (canon_ledger / "vectors.db").is_file(),
                (canon_ledger / "memory_scratchpad.json").is_file(),
                any((canon_ledger / "summaries").glob("ch*.md")),
                any(story_events.glob("chapter_*.events.json")),
            )
        )
        state = read_json_if_exists(canon_ledger / "state.json") or {}
        has_projected_state = bool(
            isinstance(state, dict)
            and (
                state.get("entity_state")
                or state.get("_projection_state_versions")
                or (state.get("progress") or {}).get("chapter_status")
            )
        )
        return commit_count > 1 or has_projection_files or has_projected_state
    chapters = manifest.get("chapters")
    if not isinstance(chapters, dict):
        return True
    chapter = str(int((payload.get("meta") or {}).get("chapter") or 0))
    # An earlier accepted commit that never reached the read models leaves a
    # hole in the ordered prefix; appending on top would stamp facts derived
    # from an incomplete canon, so rebuild first.
    if projection_coverage_gaps(project_root, before_chapter=int(chapter)):
        return True
    previous = str(chapters.get(chapter) or "")
    if previous:
        return previous != canonical_snapshot_hash(payload)
    try:
        prior_head = max((int(key) for key in chapters), default=0)
    except (TypeError, ValueError):
        return True
    # Only a chapter beyond the recorded head is a true append.  Filling a
    # historical gap changes the ordered prefix and therefore needs rebuild.
    return int(chapter) <= prior_head


def record_projection_snapshot(project_root: str | Path, payload: dict[str, Any]) -> None:
    path = projection_manifest_path(project_root)
    manifest = read_json_if_exists(path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA:
        manifest = {"schema_version": MANIFEST_SCHEMA, "chapters": {}}
    chapters = manifest.get("chapters")
    if not isinstance(chapters, dict):
        chapters = {}
        manifest["chapters"] = chapters
    chapter = int((payload.get("meta") or {}).get("chapter") or 0)
    if chapter > 0:
        chapters[str(chapter)] = canonical_snapshot_hash(payload)
    write_json(path, manifest)


def projection_read_models_missing(
    project_root: str | Path,
    payload: dict[str, Any],
    *,
    include_vector: bool = True,
) -> bool:
    """Detect read-model loss that a stale ``done`` flag cannot reveal."""
    root = Path(project_root)
    config = DataModulesConfig.from_project_root(root)
    meta = payload.get("meta") if isinstance(payload, dict) else {}
    chapter = int((meta or {}).get("chapter") or 0) if isinstance(meta, dict) else 0
    status = str((meta or {}).get("status") or "") if isinstance(meta, dict) else ""
    if chapter <= 0:
        return True
    if not config.state_file.is_file() or not config.index_db.is_file():
        return True
    if not projection_manifest_path(root).is_file():
        return True

    expected_events = EventLogStore(root).normalize_events(
        chapter,
        extraction_list(payload, "accepted_events") if status == "accepted" else [],
    )
    event_path = root / ".story-system" / "events" / f"chapter_{chapter:03d}.events.json"
    if status == "accepted":
        actual_events = read_json_if_exists(event_path)
        if actual_events != expected_events:
            return True
    elif event_path.exists():
        return True

    summary_path = config.canon_ledger_dir / "summaries" / f"ch{chapter:04d}.md"
    if bool(extraction_text(payload, "summary_text")) != summary_path.is_file():
        return True
    required = set(EventProjectionRouter().required_writers(payload))
    if "memory" in required and not config.scratchpad_file.is_file():
        return True

    try:
        with sqlite3.connect(str(config.index_db)) as conn:
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "chapters" not in tables or "story_events" not in tables:
                return True
            chapter_row = conn.execute(
                "SELECT 1 FROM chapters WHERE chapter = ?",
                (chapter,),
            ).fetchone()
            if status == "accepted" and chapter_row is None:
                return True
            if status == "rejected" and chapter_row is not None:
                return True
            mirrored_event_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM story_events WHERE chapter = ?",
                    (chapter,),
                ).fetchone()[0]
            )
            if mirrored_event_count != len(expected_events):
                return True
    except sqlite3.Error:
        return True

    if not include_vector:
        return False

    from .projections import _vector_snapshot_stale

    return _vector_snapshot_stale(root, payload)


def _delete_path(target: dict[str, Any], dotted: str) -> None:
    parts = [part for part in str(dotted or "").split(".") if part]
    if not parts:
        return
    cursor: Any = target
    parents: list[tuple[dict[str, Any], str]] = []
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or not isinstance(cursor.get(part), dict):
            return
        parents.append((cursor, part))
        cursor = cursor[part]
    if isinstance(cursor, dict):
        cursor.pop(parts[-1], None)
    for parent, key in reversed(parents):
        value = parent.get(key)
        if isinstance(value, dict) and not value:
            parent.pop(key, None)


def _state_baseline(root: Path, commits: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep init/user metadata while removing fields owned by projections."""
    state = copy.deepcopy(read_json_if_exists(root / ".canon-ledger" / "state.json") or {})
    if not isinstance(state, dict):
        state = {}
    versions = state.get("_projection_state_versions")
    protagonist_versions = (
        versions.get("protagonist_state")
        if isinstance(versions, dict)
        else {}
    )
    protagonist = state.get("protagonist_state")
    if not isinstance(protagonist, dict):
        protagonist = {}
        state["protagonist_state"] = protagonist
    projected_fields: set[str] = set()
    if isinstance(protagonist_versions, dict):
        for fields in protagonist_versions.values():
            if isinstance(fields, dict):
                projected_fields.update(str(field) for field in fields)

    protagonist_name = str(protagonist.get("name") or "").strip()
    protagonist_ids = {str(protagonist.get("entity_id") or "").strip()} - {""}
    for payload in commits:
        for delta in extraction_list(payload, "entity_deltas"):
            if not isinstance(delta, dict):
                continue
            entity_id = str(delta.get("entity_id") or delta.get("id") or "").strip()
            canonical = str(
                delta.get("canonical_name")
                or (delta.get("payload") or {}).get("name")
                or ""
            ).strip()
            if entity_id and (
                bool(delta.get("is_protagonist"))
                or str(delta.get("tier") or "").strip() == "主角"
                or bool(protagonist_name and canonical == protagonist_name)
            ):
                protagonist_ids.add(entity_id)
        for delta in extraction_list(payload, "state_deltas"):
            if not isinstance(delta, dict):
                continue
            if str(delta.get("entity_id") or "").strip() in protagonist_ids:
                field = str(delta.get("field") or delta.get("field_path") or "").strip()
                if field:
                    projected_fields.add(field)
    for field in projected_fields:
        _delete_path(protagonist, field)

    state.pop("entity_state", None)
    state.pop("_projection_state_versions", None)
    progress = state.get("progress")
    if not isinstance(progress, dict):
        progress = {}
        state["progress"] = progress
    progress.pop("chapter_status", None)
    progress["current_chapter"] = 0
    progress["total_words"] = 0
    state["strand_tracker"] = {
        "last_quest_chapter": 0,
        "last_fire_chapter": 0,
        "last_constellation_chapter": 0,
        "current_dominant": "quest",
        "chapters_since_switch": 0,
        "history": [],
    }
    return state


def _sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        return
    with sqlite3.connect(str(source)) as source_conn, sqlite3.connect(str(target)) as target_conn:
        source_conn.backup(target_conn)


def _clear_projection_tables(stage_root: Path) -> None:
    from .index_manager import IndexManager

    manager = IndexManager(DataModulesConfig.from_project_root(stage_root))
    projection_tables = (
        "story_events",
        "relationships",
        "state_changes",
        "aliases",
        "entities",
        "appearances",
        "scenes",
        "chapters",
    )
    with manager._get_conn() as conn:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in projection_tables:
            if table in tables:
                # Table names come only from the constant tuple above.
                conn.execute(f'DELETE FROM "{table}"')
        if "sqlite_sequence" in tables:
            conn.executemany(
                "DELETE FROM sqlite_sequence WHERE name = ?",
                [(table,) for table in projection_tables],
            )
        if "override_contracts" in tables:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(override_contracts)")
            }
            if "record_type" in columns:
                conn.execute("DELETE FROM override_contracts WHERE record_type = 'amend_proposal'")
        conn.commit()


def _copy_bound_manuscripts(root: Path, stage_root: Path, commits: list[dict[str, Any]]) -> None:
    for payload in commits:
        binding = payload.get("chapter_binding") or {}
        relative = Path(str(binding.get("path") or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ProjectionRebuildError("artifact_path_mismatch", str(relative))
        source = root / relative
        target = stage_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _prepare_stage(root: Path, commits: list[dict[str, Any]]) -> Path:
    work_parent = root / ".canon-ledger"
    work_parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".projection-stage-", dir=work_parent))
    (stage_root / ".canon-ledger" / "summaries").mkdir(parents=True, exist_ok=True)
    (stage_root / ".story-system" / "events").mkdir(parents=True, exist_ok=True)
    (stage_root / ".story-system" / "commits").mkdir(parents=True, exist_ok=True)
    _copy_bound_manuscripts(root, stage_root, commits)
    write_json(stage_root / ".canon-ledger" / "state.json", _state_baseline(root, commits))
    _sqlite_backup(root / ".canon-ledger" / "index.db", stage_root / ".canon-ledger" / "index.db")
    _clear_projection_tables(stage_root)
    for payload in commits:
        chapter = int((payload.get("meta") or {}).get("chapter") or 0)
        write_json(
            stage_root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json",
            payload,
        )
    return stage_root


def _apply_stage_events(stage_root: Path, payload: dict[str, Any]) -> None:
    status = str((payload.get("meta") or {}).get("status") or "")
    chapter = int((payload.get("meta") or {}).get("chapter") or 0)
    if status != "accepted":
        return
    events = EventLogStore(stage_root).normalize_events(
        chapter,
        extraction_list(payload, "accepted_events"),
    )
    extraction = payload.setdefault("extraction_result", {})
    if not isinstance(extraction, dict):
        extraction = {}
        payload["extraction_result"] = extraction
    extraction["accepted_events"] = events
    EventLogStore(stage_root).write_events(chapter, events)
    proposals = AmendProposalTrigger().check(chapter, events)
    if proposals:
        from .index_manager import IndexManager

        manager = IndexManager(DataModulesConfig.from_project_root(stage_root))
        with manager._get_conn() as conn:
            ensure_override_ledger_columns(conn)
            persist_amend_proposals(conn, chapter, proposals)
            conn.commit()


def _validate_stage(stage_root: Path, projected: list[dict[str, Any]]) -> None:
    from .projections import _vector_snapshot_stale

    config = DataModulesConfig.from_project_root(stage_root)
    if not config.state_file.is_file() or not config.index_db.is_file():
        raise ProjectionRebuildError("stage_read_model_missing", "state_or_index")
    accepted_chapters = {
        int((payload.get("meta") or {}).get("chapter") or 0)
        for payload in projected
        if str((payload.get("meta") or {}).get("status") or "") == "accepted"
    }
    try:
        with sqlite3.connect(str(config.index_db)) as conn:
            actual_chapters = {
                int(row[0]) for row in conn.execute("SELECT chapter FROM chapters")
            }
    except sqlite3.Error as exc:
        raise ProjectionRebuildError("stage_index_invalid", str(exc)) from exc
    if actual_chapters != accepted_chapters:
        raise ProjectionRebuildError(
            "stage_index_chapter_mismatch",
            f"expected={sorted(accepted_chapters)},actual={sorted(actual_chapters)}",
        )

    expected_event_files: set[str] = set()
    expected_summary_files: set[str] = set()
    for payload in projected:
        chapter = int((payload.get("meta") or {}).get("chapter") or 0)
        status = str((payload.get("meta") or {}).get("status") or "")
        required = set(EventProjectionRouter().required_writers(payload))
        statuses = payload.get("projection_status") or {}
        for writer in required:
            writer_status = str(statuses.get(writer) or "")
            if writer_status not in {"done", "skipped"}:
                raise ProjectionRebuildError(
                    "stage_writer_failed",
                    f"chapter={chapter},writer={writer},status={writer_status}",
                )
        if status == "accepted":
            expected_event_files.add(f"chapter_{chapter:03d}.events.json")
            expected_events = EventLogStore(stage_root).normalize_events(
                chapter,
                extraction_list(payload, "accepted_events"),
            )
            if EventLogStore(stage_root).read_events(chapter) != expected_events:
                raise ProjectionRebuildError(
                    "stage_event_content_mismatch",
                    str(chapter),
                )
        if status == "accepted" and extraction_text(payload, "summary_text"):
            expected_summary_files.add(f"ch{chapter:04d}.md")
        if _vector_snapshot_stale(stage_root, payload):
            raise ProjectionRebuildError("stage_vector_snapshot_mismatch", str(chapter))
    actual_event_files = {
        path.name for path in (stage_root / ".story-system" / "events").glob("chapter_*.events.json")
    }
    actual_summary_files = {
        path.name for path in (stage_root / ".canon-ledger" / "summaries").glob("ch*.md")
    }
    if actual_event_files != expected_event_files:
        raise ProjectionRebuildError("stage_event_set_mismatch")
    if actual_summary_files != expected_summary_files:
        raise ProjectionRebuildError("stage_summary_set_mismatch")


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _install_stage(
    root: Path,
    stage_root: Path,
    projected: list[dict[str, Any]],
    writer_results: dict[int, dict[str, dict[str, Any]]],
    *,
    commit_payloads: list[dict[str, Any]] | None = None,
) -> None:
    stage_log = stage_root / ".canon-ledger" / "projection_log.jsonl"
    current_log = root / ".canon-ledger" / "projection_log.jsonl"
    if current_log.is_file():
        stage_log.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current_log, stage_log)
    for payload in projected:
        chapter = int((payload.get("meta") or {}).get("chapter") or 0)
        append_projection_run(
            stage_root,
            payload,
            writer_results.get(chapter, {}),
            commit_path=root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json",
        )

    backup_root = Path(tempfile.mkdtemp(prefix=".projection-backup-", dir=root / ".canon-ledger"))
    pairs: list[tuple[Path, Path | None, Path]] = []

    def add(relative: Path, stage_relative: Path | None = None) -> None:
        source = stage_root / (stage_relative or relative)
        pairs.append((root / relative, source if source.exists() else None, backup_root / relative))

    add(Path(".canon-ledger/state.json"))
    add(Path(".canon-ledger/index.db"))
    add(Path(".canon-ledger/index.db-wal"))
    add(Path(".canon-ledger/index.db-shm"))
    add(Path(".canon-ledger/vectors.db"))
    add(Path(".canon-ledger/vectors.db-wal"))
    add(Path(".canon-ledger/vectors.db-shm"))
    add(Path(".canon-ledger/memory_scratchpad.json"))
    add(Path(".canon-ledger/projection_log.jsonl"))
    add(Path(".canon-ledger/summaries"))
    add(Path(".story-system/events"))
    install_commits = commit_payloads if commit_payloads is not None else projected
    for payload in install_commits:
        chapter = int((payload.get("meta") or {}).get("chapter") or 0)
        relative = Path(".story-system") / "commits" / f"chapter_{chapter:03d}.commit.json"
        add(relative)

    installed: list[tuple[Path, Path]] = []
    try:
        for target, source, backup in pairs:
            backup.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                os.replace(target, backup)
            # Register the target immediately after its old value moves.  If
            # installing the staged source itself fails, rollback must still
            # restore that just-backed-up value.
            installed.append((target, backup))
            target.parent.mkdir(parents=True, exist_ok=True)
            if source is not None:
                os.replace(source, target)
    except Exception as install_error:
        rollback_errors: list[str] = []
        for target, backup in reversed(installed):
            try:
                _remove_path(target)
                if backup.exists() or backup.is_symlink():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(backup, target)
            except Exception as rollback_error:  # pragma: no cover - filesystem failure
                rollback_errors.append(f"{target}:{rollback_error}")
        if rollback_errors:
            # Preserve the backup directory for a manual recovery instead of
            # deleting the last intact copy after a filesystem-level failure.
            raise ProjectionRebuildError(
                "projection_install_rollback_failed",
                f"backup={backup_root};errors={'|'.join(rollback_errors)}",
            ) from install_error
        shutil.rmtree(backup_root, ignore_errors=True)
        raise
    else:
        shutil.rmtree(backup_root, ignore_errors=True)


def rebuild_all_projections(
    project_root: str | Path,
    *,
    reason: str = "explicit_replay",
) -> dict[str, Any]:
    """Build in isolation, validate, then install all projection artifacts."""
    root = Path(project_root).expanduser().resolve()
    lock_path = root / ".canon-ledger" / "projection_rebuild.lock"
    status_path = root / REBUILD_STATUS_REL
    root.joinpath(".canon-ledger").mkdir(parents=True, exist_ok=True)
    stage_root: Path | None = None
    with FileLock(str(lock_path), timeout=30):
        try:
            commits = load_canonical_commits(root)
            source_hashes = _manifest_entries(commits)
            write_json(
                status_path,
                {
                    "schema_version": REBUILD_SCHEMA,
                    "status": "building",
                    "reason": reason,
                    "chapters": sorted(int(chapter) for chapter in source_hashes),
                },
            )
            previous_manifest = read_json_if_exists(projection_manifest_path(root)) or {}
            previous_chapters = (
                previous_manifest.get("chapters")
                if isinstance(previous_manifest, dict)
                else {}
            )
            if not isinstance(previous_chapters, dict):
                previous_chapters = {}
            replayable, stamped, stale_chapters = stamp_and_partition_commits(
                commits,
                previous_manifest=previous_chapters,
            )
            stage_root = _prepare_stage(root, commits)
            from .chapter_commit_service import ChapterCommitService

            service = ChapterCommitService(stage_root)
            for payload in stamped:
                chapter = int((payload.get("meta") or {}).get("chapter") or 0)
                if str((payload.get("meta") or {}).get("validation_status") or "") == (
                    "needs_revalidation"
                ):
                    payload["projection_status"] = {
                        key: "skipped" for key in PROJECTION_STATUS
                    }
                write_json(
                    stage_root
                    / ".story-system"
                    / "commits"
                    / f"chapter_{chapter:03d}.commit.json",
                    payload,
                )
            projected: list[dict[str, Any]] = []
            writer_results: dict[int, dict[str, dict[str, Any]]] = {}
            for original in replayable:
                payload = copy.deepcopy(original)
                payload["projection_status"] = dict(PROJECTION_STATUS)
                _apply_stage_events(stage_root, payload)
                chapter = int((payload.get("meta") or {}).get("chapter") or 0)
                captured: dict[str, dict[str, Any]] = {}
                payload = service.apply_projection_writers(
                    payload,
                    persist_run=False,
                    writer_results_out=captured,
                )
                service.persist_commit(payload)
                writer_results[chapter] = captured
                projected.append(payload)
            _validate_stage(stage_root, projected)

            # Abort if another process replaced a canonical source while the
            # isolated build was running.  Installing that stale build would
            # violate the commit/read-model equality this operation promises.
            current = load_canonical_commits(root)
            if _manifest_entries(current) != source_hashes:
                raise ProjectionRebuildError("canonical_commit_changed_during_rebuild")

            install_commits = list(stamped)
            projected_by_chapter = {
                int((payload.get("meta") or {}).get("chapter") or 0): payload
                for payload in projected
            }
            for index, payload in enumerate(install_commits):
                chapter = int((payload.get("meta") or {}).get("chapter") or 0)
                if chapter in projected_by_chapter:
                    install_commits[index] = projected_by_chapter[chapter]

            for payload in install_commits:
                chapter = int((payload.get("meta") or {}).get("chapter") or 0)
                write_json(
                    stage_root
                    / ".story-system"
                    / "commits"
                    / f"chapter_{chapter:03d}.commit.json",
                    payload,
                )
            write_json(status_path, {"schema_version": REBUILD_SCHEMA, "status": "installing", "reason": reason})
            _install_stage(
                root,
                stage_root,
                projected,
                writer_results,
                commit_payloads=install_commits,
            )
            manifest = {
                "schema_version": MANIFEST_SCHEMA,
                "chapters": _manifest_entries(install_commits),
            }
            write_json(projection_manifest_path(root), manifest)
            report = {
                "schema_version": REBUILD_SCHEMA,
                "status": "complete",
                "ok": True,
                "reason": reason,
                "chapters": [
                    int((payload.get("meta") or {}).get("chapter") or 0)
                    for payload in install_commits
                ],
                "replayed_chapters": [
                    int((payload.get("meta") or {}).get("chapter") or 0)
                    for payload in projected
                ],
                "needs_revalidation": list(stale_chapters),
                "projection_status": {
                    str(int((payload.get("meta") or {}).get("chapter") or 0)): dict(
                        payload.get("projection_status") or {}
                    )
                    for payload in install_commits
                },
            }
            write_json(status_path, report)
            return {**report, "payloads": projected}
        except Exception as exc:
            code = exc.code if isinstance(exc, ProjectionRebuildError) else "projection_rebuild_failed"
            report = {
                "schema_version": REBUILD_SCHEMA,
                "status": "failed",
                "ok": False,
                "reason": reason,
                "error": code,
                "detail": exc.detail if isinstance(exc, ProjectionRebuildError) else str(exc),
                "chapters": [],
                "projection_status": {},
                "payloads": [],
            }
            write_json(status_path, {key: value for key, value in report.items() if key != "payloads"})
            return report
        finally:
            if stage_root is not None:
                shutil.rmtree(stage_root, ignore_errors=True)
