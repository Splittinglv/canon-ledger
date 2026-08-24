#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure serializers for the public, active Canon read surface.

The immutable projection keeps genesis/cutover records separate from chapter
effects.  Public consumers must not mistake that storage split for an
authority split: admitted genesis facts, active chapter facts and active
author axioms all belong to the same exact HEAD.  This module builds that
union from the already-sanitized as-of snapshot; it never reads raw legacy
stores, STAGING, style memory or mutable setting files.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any


ACTIVE_FACT_SCHEMA = "canon-v3/public-active-fact/v1"


class PublicReadError(ValueError):
    """A sanitized public snapshot could not form an unambiguous fact view."""


def _rows(value: Any, *, field: str) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PublicReadError(f"canon_v3_public_{field}_not_list")
    result: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise PublicReadError(f"canon_v3_public_{field}_row_not_mapping")
        result.append(item)
    return result


def _active_axiom_records(value: Any) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Mapping):
        raise PublicReadError("canon_v3_public_author_axioms_not_mapping")
    return _rows(value.get("records"), field="author_axioms_records")


def _fact_view(raw: Mapping[str, Any]) -> dict[str, Any]:
    digest = str(raw.get("fact_digest") or "").strip()
    if not digest:
        raise PublicReadError("canon_v3_public_active_fact_digest_missing")
    row = copy.deepcopy(dict(raw))
    source_chapter = int(row.get("source_chapter") or row.get("chapter") or 0)
    payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
    category = str(row.get("category") or payload.get("kind") or "story_fact")
    if source_chapter == 0:
        origin = "genesis"
    elif str(row.get("commit_hash") or row.get("effect_id") or "").strip():
        origin = "chapter_commit"
    else:
        # A cutover keeps the fact's original chapter for chronology, but the
        # admitted row is authoritative because it lives in the v3 genesis
        # snapshot—not because the old commit/event store remains readable.
        origin = "legacy_cutover"
    row.update(
        {
            "schema_version": ACTIVE_FACT_SCHEMA,
            "authority_layer": "active_canon",
            "authority_state": "active",
            "origin": origin,
            "category": category,
            "source_chapter": source_chapter,
        }
    )
    return row


def _axiom_view(raw: Mapping[str, Any]) -> dict[str, Any]:
    digest = str(raw.get("record_digest") or "").strip()
    key = str(raw.get("axiom_key") or "").strip()
    category = str(raw.get("category") or "").strip()
    if not digest or not key or not category:
        raise PublicReadError("canon_v3_public_active_author_axiom_invalid")
    return {
        "schema_version": ACTIVE_FACT_SCHEMA,
        "authority_layer": "active_canon",
        "authority_state": "active",
        "origin": "author_axiom",
        "record_type": "author_axiom",
        "id": f"author-axiom:{key}",
        "fact_digest": digest,
        "category": category,
        "subject": "author_axiom",
        "field": key,
        "value": copy.deepcopy(raw.get("value")),
        "payload": {
            "kind": "author_axiom",
            "axiom_key": key,
            "category": category,
            "value": copy.deepcopy(raw.get("value")),
        },
        "status": "active",
        "source_chapter": 0,
        "record_digest": digest,
        "source": copy.deepcopy(raw.get("source") or {}),
    }


def active_fact_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return every active HEAD fact exactly once from a sanitized snapshot.

    ``canonical_facts`` and ``hard_constraints`` are compatibility channels
    and can contain the same admitted record.  Digest-based de-duplication is
    therefore required.  A digest collision with different content fails
    closed instead of choosing an arbitrary channel.
    """

    if not isinstance(snapshot, Mapping):
        raise PublicReadError("canon_v3_public_snapshot_not_mapping")
    canonical = _rows(snapshot.get("canonical_facts"), field="canonical_facts")
    hard = _rows(snapshot.get("hard_constraints"), field="hard_constraints")
    axioms = _active_axiom_records(snapshot.get("author_axioms"))

    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    raw_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in (*canonical, *hard):
        view = _fact_view(raw)
        key = ("canon_fact", str(view["fact_digest"]))
        unsigned = copy.deepcopy(dict(raw))
        previous = raw_by_key.get(key)
        if previous is not None and previous != unsigned:
            raise PublicReadError("canon_v3_public_active_fact_digest_collision")
        raw_by_key[key] = unsigned
        indexed[key] = view
    for raw in axioms:
        view = _axiom_view(raw)
        key = ("author_axiom", str(view["fact_digest"]))
        unsigned = copy.deepcopy(dict(raw))
        previous = raw_by_key.get(key)
        if previous is not None and previous != unsigned:
            raise PublicReadError("canon_v3_public_active_axiom_digest_collision")
        raw_by_key[key] = unsigned
        indexed[key] = view

    origin_order = {
        "genesis": 0,
        "legacy_cutover": 1,
        "author_axiom": 2,
        "chapter_commit": 3,
    }
    return sorted(
        indexed.values(),
        key=lambda row: (
            origin_order.get(str(row.get("origin") or ""), 9),
            int(row.get("source_chapter") or 0),
            str(row.get("category") or ""),
            str(row.get("subject") or ""),
            str(row.get("field") or ""),
            str(row.get("fact_digest") or ""),
        ),
    )


__all__ = [
    "ACTIVE_FACT_SCHEMA",
    "PublicReadError",
    "active_fact_rows",
]
