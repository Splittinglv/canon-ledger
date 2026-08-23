#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded planning-only contract refreshes.

The public planning workflow writes outlines first.  This facade turns one
already-persisted chapter outline into disposable Story System contracts while
keeping Canon v3 and the author-owned setting files outside the write set.

``persist_runtime_contracts`` currently synchronizes ``MASTER_SETTING`` as a
side effect.  To reuse its normalization without granting that side effect on
the real project, the function is run in a detached temporary project.  Only
the three explicitly allow-listed planning JSON payloads are then published.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from chapter_outline_loader import (
        _extract_directive_section,
        parse_chapter_execution_directive,
    )
    from chapter_paths import volume_num_for_chapter
    from security_utils import atomic_write_json, resolve_inside_project
except ImportError:  # pragma: no cover - package-style imports in adapters.
    from scripts.chapter_outline_loader import (
        _extract_directive_section,
        parse_chapter_execution_directive,
    )
    from scripts.chapter_paths import volume_num_for_chapter
    from scripts.security_utils import atomic_write_json, resolve_inside_project

from .runtime_contract_builder import RuntimeContractBuilder
from .story_contract_schema import ChapterBrief
from .story_contracts import (
    StoryContractPaths,
    persist_runtime_contracts,
    read_json_if_exists,
)
from .workflow_authority import WorkflowAuthority


PLANNING_REFRESH_SCHEMA = "canon-v3/planning-contract-refresh/v1"
PLANNING_INPUT_SCHEMA = "canon-v3/planning-contract-input/v1"
_DETAIL_OUTLINE_RE = re.compile(
    r"^第(?P<volume>\d+)卷(?:\s*-\s*|\s+)详细大纲\.md$"
)
_CHAPTER_RANGE_RE = re.compile(r"^\s*(?P<start>\d+)\s*-\s*(?P<end>\d+)\s*$")


class PlanningContractRefreshError(RuntimeError):
    """A planning refresh could not preserve its closed write contract."""

    def __init__(self, code: str, **details: Any) -> None:
        self.code = str(code)
        self.details = dict(details)
        suffix = (
            ":" + json.dumps(self.details, ensure_ascii=False, sort_keys=True)
            if self.details
            else ""
        )
        super().__init__(f"{self.code}{suffix}")


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    relative_path: str
    sha256: str
    size: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.relative_path,
            "sha256": self.sha256,
            "bytes": self.size,
        }


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256(raw)


def _safe_file(root: Path, path: Path) -> Path:
    try:
        resolved = resolve_inside_project(root, path, reject_leaf_symlink=True)
    except ValueError as exc:
        raise PlanningContractRefreshError(
            "planning_source_path_unsafe",
            path=str(path),
        ) from exc
    if not resolved.is_file():
        raise PlanningContractRefreshError(
            "planning_source_not_file",
            path=resolved.relative_to(root).as_posix(),
        )
    return resolved


def _snapshot_file_with_bytes(
    root: Path,
    path: Path,
) -> tuple[_FileSnapshot, bytes]:
    safe = _safe_file(root, path)
    try:
        raw = safe.read_bytes()
    except OSError as exc:
        raise PlanningContractRefreshError(
            "planning_source_unreadable",
            path=safe.relative_to(root).as_posix(),
        ) from exc
    return (
        _FileSnapshot(
            relative_path=safe.relative_to(root).as_posix(),
            sha256=_sha256(raw),
            size=len(raw),
        ),
        raw,
    )


def _first_split_outline(root: Path, chapter: int) -> Path | None:
    outline_dir = root / "大纲"
    found: set[Path] = set()
    for pattern in (
        f"第{chapter}章*.md",
        f"第{chapter:02d}章*.md",
        f"第{chapter:03d}章*.md",
        f"第{chapter:04d}章*.md",
    ):
        found.update(outline_dir.glob(pattern))
    matches = sorted(found)
    if len(matches) > 1:
        raise PlanningContractRefreshError(
            "planning_chapter_outline_ambiguous",
            chapter=chapter,
            paths=[item.relative_to(root).as_posix() for item in matches],
        )
    return matches[0] if matches else None


def _markdown_cells(line: str) -> list[str]:
    return [item.strip() for item in line.strip().strip("|").split("|")]


def _master_outline_volume(root: Path, chapter: int) -> tuple[int | None, bool]:
    """Resolve explicit planning metadata without consulting legacy state."""

    path = root / "大纲" / "总纲.md"
    if not path.exists():
        return None, False
    safe = _safe_file(root, path)
    try:
        text = safe.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PlanningContractRefreshError(
            "planning_master_outline_unreadable"
        ) from exc
    return _master_outline_volume_from_text(text, chapter)


def _master_outline_volume_from_text(
    text: str,
    chapter: int,
) -> tuple[int | None, bool]:
    lines = str(text).splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip().startswith("|")
            and "卷号" in _markdown_cells(line)
            and "章节范围" in _markdown_cells(line)
        ),
        None,
    )
    if header_index is None:
        return None, False
    header = _markdown_cells(lines[header_index])
    volume_column = header.index("卷号")
    range_column = header.index("章节范围")
    recognized_rows = 0
    matches: set[int] = set()
    for line in lines[header_index + 1 :]:
        if not line.strip().startswith("|"):
            if line.strip():
                break
            continue
        cells = _markdown_cells(line)
        if max(volume_column, range_column) >= len(cells):
            continue
        try:
            volume = int(cells[volume_column])
        except (TypeError, ValueError):
            continue
        parsed = _CHAPTER_RANGE_RE.match(cells[range_column])
        if volume <= 0 or parsed is None:
            continue
        start = int(parsed.group("start"))
        end = int(parsed.group("end"))
        if start <= 0 or end < start:
            continue
        recognized_rows += 1
        if start <= chapter <= end:
            matches.add(volume)
    if len(matches) > 1:
        raise PlanningContractRefreshError(
            "planning_volume_mapping_ambiguous",
            chapter=chapter,
            volumes=sorted(matches),
        )
    return (next(iter(matches)) if matches else None), bool(recognized_rows)


def _captured_outline(
    root: Path,
    *,
    chapter: int,
    chapter_source: Path,
    expected_volume: int,
    raw_by_path: Mapping[str, bytes],
) -> dict[str, Any]:
    source_key = chapter_source.relative_to(root).as_posix()
    source_raw = raw_by_path.get(source_key)
    if source_raw is None:
        raise PlanningContractRefreshError("planning_source_snapshot_missing")
    try:
        source_text = source_raw.decode("utf-8")
        master_raw = raw_by_path.get("大纲/总纲.md")
        master_text = master_raw.decode("utf-8") if master_raw is not None else ""
    except UnicodeDecodeError as exc:
        raise PlanningContractRefreshError(
            "planning_chapter_outline_unreadable",
            path=source_key,
        ) from exc
    mapped_volume, has_volume_table = (
        _master_outline_volume_from_text(master_text, chapter)
        if master_raw is not None
        else (None, False)
    )
    detail = _DETAIL_OUTLINE_RE.match(chapter_source.name)
    if detail is None:
        if mapped_volume is None and has_volume_table:
            raise PlanningContractRefreshError(
                "planning_volume_mapping_missing", chapter=chapter
            )
        captured_volume = mapped_volume or volume_num_for_chapter(chapter)
        directive = parse_chapter_execution_directive(source_text)
    else:
        captured_volume = int(detail.group("volume"))
        if mapped_volume is not None and mapped_volume != captured_volume:
            raise PlanningContractRefreshError(
                "planning_volume_mapping_mismatch",
                chapter=chapter,
                master_volume=mapped_volume,
                outline_volume=captured_volume,
            )
        section = _extract_directive_section(source_text, chapter)
        directive = parse_chapter_execution_directive(section or "")
    if captured_volume != expected_volume:
        raise PlanningContractRefreshError(
            "planning_outline_selection_changed_during_refresh"
        )
    return directive


def _detailed_outline_matches(
    root: Path,
    chapter: int,
) -> tuple[tuple[int, Path, str], ...]:
    matches: list[tuple[int, Path, str]] = []
    outline_dir = root / "大纲"
    for path in sorted(outline_dir.glob("第*卷*详细大纲.md")):
        filename = _DETAIL_OUTLINE_RE.match(path.name)
        if filename is None:
            continue
        safe = _safe_file(root, path)
        try:
            content = safe.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise PlanningContractRefreshError(
                "planning_chapter_outline_unreadable",
                path=safe.relative_to(root).as_posix(),
            ) from exc
        section = _extract_directive_section(content, chapter)
        if section:
            matches.append((int(filename.group("volume")), safe, section))
    return tuple(matches)


def _planning_outline(
    root: Path,
    chapter: int,
) -> tuple[int, Path, dict[str, Any]]:
    mapped_volume, has_volume_table = _master_outline_volume(root, chapter)
    split = _first_split_outline(root, chapter)
    if split is not None:
        safe = _safe_file(root, split)
        try:
            content = safe.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise PlanningContractRefreshError(
                "planning_chapter_outline_unreadable",
                path=safe.relative_to(root).as_posix(),
            ) from exc
        if mapped_volume is None and has_volume_table:
            raise PlanningContractRefreshError(
                "planning_volume_mapping_missing", chapter=chapter
            )
        volume = mapped_volume or volume_num_for_chapter(chapter)
        return volume, safe, parse_chapter_execution_directive(content)

    detailed = _detailed_outline_matches(root, chapter)
    if not detailed:
        raise PlanningContractRefreshError(
            "planning_chapter_outline_missing",
            chapter=int(chapter),
        )
    if len(detailed) > 1:
        raise PlanningContractRefreshError(
            "planning_chapter_outline_ambiguous",
            chapter=chapter,
            paths=[item[1].relative_to(root).as_posix() for item in detailed],
        )
    volume, path, section = detailed[0]
    if mapped_volume is not None and mapped_volume != volume:
        raise PlanningContractRefreshError(
            "planning_volume_mapping_mismatch",
            chapter=chapter,
            master_volume=mapped_volume,
            outline_volume=volume,
        )
    return volume, path, parse_chapter_execution_directive(section)


def _outline_sources(root: Path, chapter_source: Path, volume: int) -> tuple[Path, ...]:
    paths: list[Path] = [chapter_source]
    for optional in (
        root / "大纲" / "总纲.md",
        root / "大纲" / f"第{volume}卷-节拍表.md",
    ):
        if optional.exists() and optional not in paths:
            paths.append(optional)
    return tuple(paths)


def _capture_source_bundle(
    root: Path,
    paths: Iterable[Path],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    captured = tuple(_snapshot_file_with_bytes(root, path) for path in paths)
    snapshots = tuple(item[0] for item in captured)
    payload = {
        "schema_version": PLANNING_INPUT_SCHEMA,
        "files": [item.as_dict() for item in snapshots],
    }
    return (
        {**payload, "digest": _canonical_digest(payload)},
        {item.relative_path: raw for item, raw in captured},
    )


def _source_bundle(root: Path, paths: Iterable[Path]) -> dict[str, Any]:
    bundle, _raw = _capture_source_bundle(root, paths)
    return bundle


def _capture_optional_input_bundle(
    root: Path,
    outline: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Bind non-outline inputs that also affect RuntimeContractBuilder output."""

    captured: list[tuple[_FileSnapshot, bytes]] = []
    for path in (
        root / ".story-system" / "MASTER_SETTING.json",
        root / ".story-system" / "anti_patterns.json",
    ):
        if path.exists():
            captured.append(_snapshot_file_with_bytes(root, path))
    inputs = [item[0] for item in captured]
    payload = {
        "schema_version": PLANNING_INPUT_SCHEMA,
        "source_outline_digest": str(outline["digest"]),
        "files": [item.as_dict() for item in inputs],
    }
    return (
        {**payload, "digest": _canonical_digest(payload)},
        {item.relative_path: raw for item, raw in captured},
    )


def _optional_input_bundle(root: Path, outline: Mapping[str, Any]) -> dict[str, Any]:
    bundle, _raw = _capture_optional_input_bundle(root, outline)
    return bundle


def _chapter_contract(chapter: int, directive: Mapping[str, Any]) -> dict[str, Any]:
    payload = ChapterBrief.model_validate(
        {
            "meta": {
                "schema_version": "story-system/v1",
                "contract_type": "CHAPTER_BRIEF",
                "generator_version": "planning-refresh-v1",
                "chapter": int(chapter),
            },
            "override_allowed": {
                "chapter_focus": str(directive.get("goal") or "").strip(),
            },
            "chapter_directive": dict(directive),
            "dynamic_context": [],
            "source_trace": [],
        }
    ).model_dump()
    payload["meta"]["chapter"] = int(chapter)
    # Planning contracts guide a draft; they never assert that an outline beat
    # already happened and never become a factual review gate.
    payload["dynamic_context"] = []
    payload["source_trace"] = []
    return payload


class _PlanningRuntimeContractBuilder(RuntimeContractBuilder):
    """Use exact planning inputs while retaining the existing builder schema."""

    def __init__(
        self,
        project_root: Path,
        *,
        volume: int,
        directive: Mapping[str, Any],
    ) -> None:
        super().__init__(project_root)
        self._planning_volume = int(volume)
        self._planning_directive = dict(directive)

    def _resolve_volume(self, chapter: int) -> int:
        del chapter
        return self._planning_volume

    def _load_plot_structure(self, chapter: int) -> dict[str, Any]:
        del chapter
        return {
            "must_cover_nodes": list(
                self._planning_directive.get("must_cover_nodes") or ()
            ),
            "forbidden_zones": list(
                self._planning_directive.get("forbidden_zones") or ()
            ),
            "cpns": list(self._planning_directive.get("cpns") or ()),
        }


def _write_shadow_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _detached_contracts(
    *,
    chapter: int,
    volume: int,
    chapter_contract: Mapping[str, Any],
    directive: Mapping[str, Any],
    captured_files: Mapping[str, bytes],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the legacy persister without expanding the real-project write set."""

    with tempfile.TemporaryDirectory(prefix="canon-planning-refresh-") as raw_tmp:
        shadow = Path(raw_tmp) / "project"
        shadow_paths = StoryContractPaths.from_project_root(shadow)
        for relative, raw in captured_files.items():
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise PlanningContractRefreshError(
                    "planning_captured_path_invalid", path=relative
                )
            destination = shadow / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)

        master = read_json_if_exists(shadow_paths.master_json)
        if not isinstance(master, dict):
            raise PlanningContractRefreshError("planning_master_contract_missing")
        anti_patterns = read_json_if_exists(shadow_paths.anti_patterns_json)
        if anti_patterns is not None and not isinstance(anti_patterns, list):
            raise PlanningContractRefreshError("planning_anti_patterns_invalid")

        if anti_patterns is None:
            _write_shadow_json(shadow_paths.anti_patterns_json, [])
        _write_shadow_json(shadow_paths.chapter_json(chapter), dict(chapter_contract))
        # The legacy persister resolves its output path through state.json.
        # Supply a detached planning-only mapping so the real legacy state is
        # neither read nor granted authority over the refreshed contracts.
        _write_shadow_json(
            shadow / ".canon-ledger" / "state.json",
            {
                "progress": {
                    "volumes_planned": [
                        {
                            "volume": int(volume),
                            "chapters_range": f"{int(chapter)}-{int(chapter)}",
                        }
                    ]
                }
            },
        )

        volume_contract, review_contract = _PlanningRuntimeContractBuilder(
            shadow,
            volume=volume,
            directive=directive,
        ).build_for_chapter(chapter)
        persist_runtime_contracts(
            shadow,
            chapter,
            dict(volume_contract),
            dict(review_contract),
        )
        normalized_volume = read_json_if_exists(shadow_paths.volume_json(volume))
        normalized_review = read_json_if_exists(shadow_paths.review_json(chapter))
        if not isinstance(normalized_volume, dict) or not isinstance(
            normalized_review, dict
        ):
            raise PlanningContractRefreshError(
                "planning_detached_contract_persistence_failed"
            )

        # Outline fulfillment and author-selected forbidden beats stay in the
        # CHAPTER_BRIEF as planning advice.  They are not factual checks.
        normalized_review["must_check"] = []
        normalized_review["blocking_rules"] = []
        normalized_review["genre_specific_risks"] = []
        normalized_review["anti_patterns"] = []
        normalized_review["system_constraints"] = []
        normalized_review["review_thresholds"] = {"blocking_count": 0}
        normalized_volume.setdefault("meta", {})["volume"] = int(volume)
        normalized_review.setdefault("meta", {})["chapter"] = int(chapter)
        return normalized_volume, normalized_review


def _bind_contract_batch(
    *,
    chapter: int,
    volume: int,
    head_hash: str,
    source_outline_digest: str,
    contracts: Mapping[str, dict[str, Any]],
) -> str:
    """Embed one common identity so mixed crash remnants are detectable."""

    batch_payload = {
        "schema_version": "canon-v3/planning-contract-batch/v1",
        "chapter": int(chapter),
        "volume": int(volume),
        "head_hash": head_hash,
        "source_outline_digest": source_outline_digest,
        "contract_digests": {
            name: _canonical_digest(payload)
            for name, payload in sorted(contracts.items())
        },
    }
    batch_digest = _canonical_digest(batch_payload)
    for payload in contracts.values():
        meta = payload.setdefault("meta", {})
        meta["planning_authority"] = "planning_only"
        meta["planning_head_hash"] = head_hash
        meta["source_outline_digest"] = source_outline_digest
        meta["planning_batch_digest"] = batch_digest
    return batch_digest


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _file_change(root: Path, path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = _json_bytes(payload)
    before = path.read_bytes() if path.is_file() else None
    return {
        "path": path.relative_to(root).as_posix(),
        "before_sha256": _sha256(before) if before is not None else None,
        "after_sha256": _sha256(raw),
        "would_change": before != raw,
    }


def _require_exact_output_path(root: Path, path: Path) -> Path:
    try:
        resolved = resolve_inside_project(root, path, reject_leaf_symlink=True)
    except ValueError as exc:
        raise PlanningContractRefreshError(
            "planning_output_path_unsafe",
            path=str(path),
        ) from exc
    # Even a project-internal symlink would make the documented three-path
    # allow-list misleading, so planning output rejects every redirected path.
    if resolved != path.absolute():
        raise PlanningContractRefreshError(
            "planning_output_path_redirected",
            path=path.relative_to(root).as_posix(),
        )
    return path


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_files(backups: Mapping[Path, bytes | None]) -> None:
    for path, raw in backups.items():
        if raw is None:
            path.unlink(missing_ok=True)
            continue
        _atomic_write_bytes(path, raw)


def _assert_inputs_stable(
    root: Path,
    *,
    chapter: int,
    expected_volume: int,
    expected_chapter_source: Path,
    expected_outline_digest: str,
    expected_input_digest: str,
) -> None:
    volume, chapter_source, _directive = _planning_outline(root, chapter)
    if (
        volume != expected_volume
        or chapter_source.resolve() != expected_chapter_source.resolve()
    ):
        raise PlanningContractRefreshError(
            "planning_outline_selection_changed_during_refresh"
        )
    source_paths = _outline_sources(root, chapter_source, volume)
    outline = _source_bundle(root, source_paths)
    inputs = _optional_input_bundle(root, outline)
    if outline["digest"] != expected_outline_digest:
        raise PlanningContractRefreshError("planning_outline_changed_during_refresh")
    if inputs["digest"] != expected_input_digest:
        raise PlanningContractRefreshError("planning_inputs_changed_during_refresh")


def refresh_contracts(
    project_root: str | Path,
    chapter: int,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Refresh one chapter's planning contracts without mutating Canon.

    The operation is allowed only while Canon is ``ready`` and its projection
    is fresh.  A dry run builds the exact same payloads but writes nothing.
    """

    root = Path(project_root).expanduser().resolve()
    try:
        chapter_no = int(chapter)
    except (TypeError, ValueError) as exc:
        raise PlanningContractRefreshError(
            "planning_chapter_invalid", chapter=chapter
        ) from exc
    if chapter_no <= 0:
        raise PlanningContractRefreshError(
            "planning_chapter_invalid", chapter=chapter_no
        )

    authority = WorkflowAuthority(root)
    workflow, _projection = authority.require_fresh_projection()
    if workflow.get("state") != "ready":
        raise PlanningContractRefreshError(
            "planning_workflow_not_ready",
            state=str(workflow.get("state") or "invalid"),
            action=str((workflow.get("primary_action") or {}).get("id") or ""),
        )
    head_before = str(workflow.get("head_hash") or "")
    generation_before = int(workflow.get("generation") or 0)
    workflow_digest = str(workflow.get("workflow_digest") or "")
    if not head_before:
        raise PlanningContractRefreshError("planning_head_missing")

    volume, chapter_source, _initial_directive = _planning_outline(
        root, chapter_no
    )
    source_paths = _outline_sources(root, chapter_source, volume)
    source_outline, outline_files = _capture_source_bundle(root, source_paths)
    source_inputs, input_files = _capture_optional_input_bundle(
        root, source_outline
    )
    directive = _captured_outline(
        root,
        chapter=chapter_no,
        chapter_source=chapter_source,
        expected_volume=volume,
        raw_by_path=outline_files,
    )
    if not directive:
        raise PlanningContractRefreshError(
            "planning_chapter_outline_empty", chapter=chapter_no
        )

    chapter_contract = _chapter_contract(chapter_no, directive)
    volume_contract, review_contract = _detached_contracts(
        chapter=chapter_no,
        volume=volume,
        chapter_contract=chapter_contract,
        directive=directive,
        captured_files={**outline_files, **input_files},
    )
    contract_payloads = {
        "chapter": chapter_contract,
        "volume": volume_contract,
        "review": review_contract,
    }
    planning_batch_digest = _bind_contract_batch(
        chapter=chapter_no,
        volume=volume,
        head_hash=head_before,
        source_outline_digest=str(source_outline["digest"]),
        contracts=contract_payloads,
    )

    paths = StoryContractPaths.from_project_root(root)
    outputs: dict[Path, dict[str, Any]] = {
        paths.chapter_json(chapter_no): contract_payloads["chapter"],
        paths.volume_json(volume): contract_payloads["volume"],
        paths.review_json(chapter_no): contract_payloads["review"],
    }
    outputs = {
        _require_exact_output_path(root, path): payload
        for path, payload in outputs.items()
    }
    changes = [_file_change(root, path, payload) for path, payload in outputs.items()]
    base_result: dict[str, Any] = {
        "schema_version": PLANNING_REFRESH_SCHEMA,
        "mode": "dry_run" if dry_run else "apply",
        "authority": "planning_only",
        "chapter": chapter_no,
        "volume": volume,
        "head_before": head_before,
        "head_after": head_before,
        "generation": generation_before,
        "workflow_digest": workflow_digest,
        "source_outline": source_outline,
        "source_inputs": source_inputs,
        "planning_batch_digest": planning_batch_digest,
        "changes": changes,
        "contracts": {
            "chapter": chapter_contract,
            "volume": volume_contract,
            "review": review_contract,
        },
        "applied": False,
    }

    _assert_inputs_stable(
        root,
        chapter=chapter_no,
        expected_volume=volume,
        expected_chapter_source=chapter_source,
        expected_outline_digest=str(source_outline["digest"]),
        expected_input_digest=str(source_inputs["digest"]),
    )
    current = authority.snapshot()
    if (
        current.get("state") != "ready"
        or not current.get("projection_fresh")
        or current.get("head_hash") != head_before
        or int(current.get("generation") or 0) != generation_before
        or current.get("workflow_digest") != workflow_digest
    ):
        raise PlanningContractRefreshError("planning_authority_changed_during_refresh")
    if dry_run:
        return base_result

    backups = {
        path: path.read_bytes() if path.is_file() else None for path in outputs
    }
    try:
        for path, payload in outputs.items():
            atomic_write_json(
                path,
                payload,
                use_lock=False,
                backup=False,
                indent=2,
            )
        _assert_inputs_stable(
            root,
            chapter=chapter_no,
            expected_volume=volume,
            expected_chapter_source=chapter_source,
            expected_outline_digest=str(source_outline["digest"]),
            expected_input_digest=str(source_inputs["digest"]),
        )
        final = authority.snapshot()
        if (
            final.get("state") != "ready"
            or not final.get("projection_fresh")
            or final.get("head_hash") != head_before
            or int(final.get("generation") or 0) != generation_before
            or final.get("workflow_digest") != workflow_digest
        ):
            raise PlanningContractRefreshError(
                "planning_authority_changed_during_publish"
            )
    except Exception:
        _restore_files(backups)
        raise

    return {
        **base_result,
        "head_after": head_before,
        "applied": True,
    }


__all__ = [
    "PLANNING_INPUT_SCHEMA",
    "PLANNING_REFRESH_SCHEMA",
    "PlanningContractRefreshError",
    "refresh_contracts",
]
