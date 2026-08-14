#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from .chapter_commit_service import ChapterCommitService
from .chapter_content_binding import verify_commit_content_binding
from .config import DataModulesConfig
from .projection_log import commit_hash, latest_projection_run
from .projection_rebuild import (
    load_canonical_commits,
    projection_read_models_missing,
    projection_snapshot_requires_rebuild,
    rebuild_all_projections,
)
from .vector_projection_writer import VectorProjectionWriter


SCHEMA_VERSION = "canon-ledger-projections/v1"
DEFAULT_PROJECTION_STATUS = {
    "state": "pending",
    "index": "pending",
    "summary": "pending",
    "memory": "pending",
    "vector": "pending",
}


def _commit_path(project_root: Path, chapter: int) -> Path:
    return project_root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"


def _read_commit(path: Path, *, expected_chapter: int | None = None) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        return {}, "missing_commit"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, f"invalid_json:{exc}"
    except OSError as exc:
        return {}, f"read_error:{exc}"
    if not isinstance(payload, dict):
        return {}, "commit_not_object"
    if expected_chapter is not None:
        meta = payload.get("meta")
        try:
            payload_chapter = int((meta or {}).get("chapter") or 0)
        except (AttributeError, TypeError, ValueError):
            payload_chapter = 0
        if payload_chapter != int(expected_chapter):
            return {}, "commit_chapter_mismatch"
    payload.setdefault("projection_status", dict(DEFAULT_PROJECTION_STATUS))
    for key, value in DEFAULT_PROJECTION_STATUS.items():
        payload["projection_status"].setdefault(key, value)
    return payload, ""


def _projection_failed(payload: dict[str, Any]) -> bool:
    projection_status = payload.get("projection_status") or {}
    if not isinstance(projection_status, dict):
        return True
    return any(
        str(value) in {"failed", "pending"} or str(value).startswith("failed:")
        for value in projection_status.values()
    )


def _vector_backfill_ready(
    project_root: Path,
    payload: dict[str, Any],
    latest_run: dict[str, Any] | None,
) -> bool:
    """Allow an explicit retry to enrich a prior BM25-only projection.

    The original chapter remains accepted and usable while credentials are
    absent.  Once an embedding key is configured, ``projections retry`` can
    safely select only the retrieval writer and backfill vectors without
    replaying state, index, or memory writers from an old chapter.
    """
    projection_status = payload.get("projection_status") or {}
    if str(projection_status.get("vector") or "") != "skipped":
        return False
    if not DataModulesConfig.from_project_root(project_root).embedding_enabled:
        return False
    if not isinstance(latest_run, dict):
        return False
    if str(latest_run.get("commit_hash") or "") != commit_hash(payload):
        return False
    writers = latest_run.get("writers") or {}
    vector = writers.get("vector") if isinstance(writers, dict) else None
    result = vector.get("result") if isinstance(vector, dict) else None
    reason = str((result or {}).get("reason") or "") if isinstance(result, dict) else ""
    return reason in {"bm25_only", "embedding_partial"}


def _vector_snapshot_stale(project_root: Path, payload: dict[str, Any]) -> bool:
    """Detect missing, legacy, or extra rows for an explicit retry/replay."""
    meta = payload.get("meta") if isinstance(payload, dict) else {}
    chapter = int((meta or {}).get("chapter") or 0) if isinstance(meta, dict) else 0
    if chapter <= 0:
        return False
    status = str((meta or {}).get("status") or "")
    expected_chunks = (
        VectorProjectionWriter(project_root)._collect_chunks(payload)
        if status == "accepted"
        else []
    )
    expected = {
        (str(chunk.get("chunk_id") or ""), str(chunk.get("source_file") or ""))
        for chunk in expected_chunks
    }
    db_path = DataModulesConfig.from_project_root(project_root).vector_db
    if not db_path.is_file():
        return bool(expected)
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "vectors" not in tables:
                return bool(expected)
            actual = {
                (str(row[0] or ""), str(row[1] or ""))
                for row in conn.execute(
                    "SELECT chunk_id, source_file FROM vectors WHERE chapter = ?",
                    (chapter,),
                ).fetchall()
            }
    except (OSError, sqlite3.Error):
        return True
    return actual != expected


def retry_projection(project_root: str | Path, *, chapter: int) -> dict[str, Any]:
    root = Path(project_root)
    path = _commit_path(root, chapter)
    payload, error = _read_commit(path, expected_chapter=chapter)
    if error:
        return {
            "schema_version": SCHEMA_VERSION,
            "action": "retry",
            "ok": False,
            "project_root": str(root),
            "chapter": chapter,
            "error": error,
            "commit_path": str(path),
            "projection_status": {},
            "latest_projection_run": None,
        }

    binding_ok, binding_error = verify_commit_content_binding(
        root,
        chapter,
        payload,
    )
    if not binding_ok:
        return {
            "schema_version": SCHEMA_VERSION,
            "action": "retry",
            "ok": False,
            "project_root": str(root),
            "chapter": chapter,
            "error": binding_error,
            "commit_path": str(path),
            "projection_status": dict(payload.get("projection_status") or {}),
            "latest_projection_run": latest_projection_run(root, chapter=chapter),
        }

    projection_status = payload.get("projection_status") or {}
    previous_run = latest_projection_run(root, chapter=chapter)
    failed_or_pending = {
        name
        for name, value in projection_status.items()
        if str(value) in {"pending", "failed"} or str(value).startswith("failed:")
    }
    needs_rebuild = (
        projection_snapshot_requires_rebuild(root, payload)
        or projection_read_models_missing(root, payload, include_vector=False)
        or bool(failed_or_pending - {"vector"})
    )
    if needs_rebuild:
        rebuilt = rebuild_all_projections(root, reason=f"retry_chapter_{chapter}")
        statuses = (rebuilt.get("projection_status") or {}).get(str(chapter), {})
        return {
            "schema_version": SCHEMA_VERSION,
            "action": "retry",
            "ok": bool(rebuilt.get("ok")),
            "project_root": str(root),
            "chapter": chapter,
            "error": str(rebuilt.get("error") or ""),
            "commit_path": str(path),
            "projection_status": dict(statuses or {}),
            "latest_projection_run": latest_projection_run(root, chapter=chapter),
            "rebuilt_chapters": list(rebuilt.get("chapters") or []),
        }

    retry_writers = {
        name
        for name in DEFAULT_PROJECTION_STATUS
        if str(projection_status.get(name) or "") == "pending"
        or str(projection_status.get(name) or "") == "failed"
        or str(projection_status.get(name) or "").startswith("failed:")
    }
    if _vector_backfill_ready(root, payload, previous_run):
        retry_writers.add("vector")
    if _vector_snapshot_stale(root, payload):
        retry_writers.add("vector")
    if retry_writers:
        projected = ChapterCommitService(root).apply_projection_writers(
            payload,
            only_writers=retry_writers,
        )
    else:
        # A successful retry command is intentionally a no-op once every
        # writer has completed.  Re-running all writers could replay an old
        # chapter and regress a derived read model.
        projected = payload
    latest_run = latest_projection_run(root, chapter=chapter)
    return {
        "schema_version": SCHEMA_VERSION,
        "action": "retry",
        "ok": not _projection_failed(projected),
        "project_root": str(root),
        "chapter": chapter,
        "error": "",
        "commit_path": str(path),
        "projection_status": dict(projected.get("projection_status") or {}),
        "latest_projection_run": latest_run,
    }


def replay_projections(project_root: str | Path, *, start_chapter: int, end_chapter: int) -> dict[str, Any]:
    root = Path(project_root)
    if start_chapter <= 0 or end_chapter <= 0 or start_chapter > end_chapter:
        return {
            "schema_version": SCHEMA_VERSION,
            "action": "replay",
            "ok": False,
            "project_root": str(root),
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
            "error": "invalid_chapter_range",
            "results": [],
        }
    try:
        commits = load_canonical_commits(root)
    except Exception as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "action": "replay",
            "ok": False,
            "project_root": str(root),
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
            "error": getattr(exc, "code", str(exc)),
            "results": [],
        }
    available = {
        int((payload.get("meta") or {}).get("chapter") or 0)
        for payload in commits
    }
    requested = list(range(start_chapter, end_chapter + 1))
    missing = [chapter for chapter in requested if chapter not in available]
    if missing:
        return {
            "schema_version": SCHEMA_VERSION,
            "action": "replay",
            "ok": False,
            "project_root": str(root),
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
            "error": f"missing_commit:{missing[0]}",
            "results": [],
        }
    rebuilt = rebuild_all_projections(
        root,
        reason=f"explicit_replay_{start_chapter}_{end_chapter}",
    )
    statuses = rebuilt.get("projection_status") or {}
    results = [
        {
            "schema_version": SCHEMA_VERSION,
            "action": "retry",
            "ok": bool(rebuilt.get("ok")),
            "project_root": str(root),
            "chapter": chapter,
            "error": str(rebuilt.get("error") or ""),
            "commit_path": str(_commit_path(root, chapter)),
            "projection_status": dict(statuses.get(str(chapter)) or {}),
            "latest_projection_run": latest_projection_run(root, chapter=chapter),
        }
        for chapter in requested
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "action": "replay",
        "ok": bool(rebuilt.get("ok")) and all(item.get("ok") for item in results),
        "project_root": str(root),
        "start_chapter": start_chapter,
        "end_chapter": end_chapter,
        "error": str(rebuilt.get("error") or ""),
        "results": results,
        "rebuilt_chapters": list(rebuilt.get("chapters") or []),
    }


def format_projection_report(report: dict[str, Any], output_format: str = "json") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    status = "OK" if report.get("ok") else "ERROR"
    if report.get("action") == "retry":
        return "\n".join(
            [
                f"{status} projections retry",
                f"chapter: {report.get('chapter')}",
                f"commit_path: {report.get('commit_path')}",
                f"projection_status: {report.get('projection_status')}",
                f"error: {report.get('error') or ''}",
            ]
        )
    lines = [
        f"{status} projections replay",
        f"range: {report.get('start_chapter')}-{report.get('end_chapter')}",
        f"error: {report.get('error') or ''}",
    ]
    for item in report.get("results") or []:
        lines.append(f"- chapter {item.get('chapter')}: {'OK' if item.get('ok') else 'ERROR'} {item.get('projection_status') or item.get('error')}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="从已有提交重试或重放 CanonLedger 投影")
    parser.add_argument("--project-root", required=True)
    sub = parser.add_subparsers(dest="action", required=True)

    retry = sub.add_parser("retry")
    retry.add_argument("--chapter", type=int, required=True)
    retry.add_argument("--format", choices=["json", "text"], default="json")

    replay = sub.add_parser("replay")
    replay.add_argument("--from-chapter", type=int, required=True)
    replay.add_argument("--to-chapter", type=int, required=True)
    replay.add_argument("--format", choices=["json", "text"], default="json")

    args = parser.parse_args()
    if args.action == "retry":
        report = retry_projection(args.project_root, chapter=args.chapter)
        print(format_projection_report(report, args.format))
        raise SystemExit(0 if report.get("ok") else 1)
    report = replay_projections(
        args.project_root,
        start_chapter=args.from_chapter,
        end_chapter=args.to_chapter,
    )
    print(format_projection_report(report, args.format))
    raise SystemExit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    main()
