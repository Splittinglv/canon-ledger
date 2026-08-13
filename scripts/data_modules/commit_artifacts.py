#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from typing import Any


RETRIEVAL_SCHEMA_VERSION = "fact-only-v3"


EXTRACTION_FIELDS = (
    "accepted_events",
    "state_deltas",
    "entity_deltas",
    "entities_appeared",
    "scenes",
    "timeline_events",
    "chapter_meta",
    "dominant_strand",
    "summary_text",
)


def extraction_result_from_commit(commit_payload: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical extraction artifact from a commit.

    New commits store the extraction snapshot under ``extraction_result``.
    Older commits stored these fields at top level, so this helper keeps
    projections readable without preserving two write shapes. If the
    canonical nested artifact exists, it is the only source of truth.
    """
    nested = commit_payload.get("extraction_result")
    if isinstance(nested, dict):
        return dict(nested)

    result: dict[str, Any] = {}
    for field in EXTRACTION_FIELDS:
        if field in commit_payload:
            result[field] = commit_payload.get(field)
    return result


def extraction_list(commit_payload: dict[str, Any], field: str) -> list[Any]:
    value = extraction_result_from_commit(commit_payload).get(field)
    return value if isinstance(value, list) else []


def extraction_dict(commit_payload: dict[str, Any], field: str) -> dict[str, Any]:
    value = extraction_result_from_commit(commit_payload).get(field)
    return value if isinstance(value, dict) else {}


def extraction_text(commit_payload: dict[str, Any], field: str) -> str:
    value = extraction_result_from_commit(commit_payload).get(field)
    return str(value or "").strip()


def retrieval_snapshot_hash(commit_payload: dict[str, Any]) -> str:
    """Hash only the canonical fields that produce retrieval chunks."""
    meta = commit_payload.get("meta") if isinstance(commit_payload, dict) else {}
    snapshot = {
        "retrieval_schema": RETRIEVAL_SCHEMA_VERSION,
        "chapter": int((meta or {}).get("chapter") or 0) if isinstance(meta, dict) else 0,
        "accepted_events": extraction_list(commit_payload, "accepted_events"),
        "state_deltas": extraction_list(commit_payload, "state_deltas"),
        "entity_deltas": extraction_list(commit_payload, "entity_deltas"),
    }
    raw = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def retrieval_source_marker(commit_payload: dict[str, Any]) -> str:
    meta = commit_payload.get("meta") if isinstance(commit_payload, dict) else {}
    chapter = int((meta or {}).get("chapter") or 0) if isinstance(meta, dict) else 0
    return f"commit:chapter_{chapter:03d}:{retrieval_snapshot_hash(commit_payload)}"
