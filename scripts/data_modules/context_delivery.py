"""Read-only, lossless pagination of one exact writing-context snapshot.

Paging changes delivery, never Canon admission or factual completeness. A
cursor binds all source content and authority metadata, so a reader cannot
accidentally concatenate pages from different HEADs, chapters or plans.
"""
from __future__ import annotations

import re
from typing import Any, Iterator

from .canon_v3.schema import canonical_digest
from .memory_contract import ContextPack
from .memory_contract_adapter import _estimate_tokens


CONTEXT_PAGE_SCHEMA = "canon-ledger-context-page/v1"
_CURSOR_RE = re.compile(r"([0-9a-f]{64}):(0|[1-9][0-9]{0,11})")


class ContextPageError(ValueError):
    pass


def _entries(value: Any, path: str, limit: int) -> Iterator[dict[str, Any]]:
    entry = {"path": path, "value": value}
    # Preserve complete fact records when they fit; split larger containers
    # by JSON Pointer. Even oversized scalar values remain intact and visible.
    if not value or not isinstance(value, (dict, list)) or _estimate_tokens(entry) <= limit:
        yield entry
        return
    items = sorted(value.items()) if isinstance(value, dict) else enumerate(value)
    for key, child in items:
        token = str(key).replace("~", "~0").replace("/", "~1")
        yield from _entries(child, path + "/" + token, limit)


def compact_context(pack: ContextPack) -> ContextPack:
    """Deduplicate complete fact records; keep their semantics and digest.

    Proof metadata remains in Canon and the full reviewer snapshot. This view
    is only a writing brief, not a new evidence source.
    """
    from dataclasses import replace

    catalog: dict[str, dict[str, Any]] = {}
    proof_fields = {
        "source_digests", "support_map", "candidate_digest", "effect_id",
        "commit_hash", "prior_fact_digest", "prior_effect_id", "inherited_fields",
    }

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        axiom = bool(value.get("axiom_key") and value.get("record_digest"))
        digest = value.get("fact_digest") or (value.get("record_digest") if axiom else None)
        if digest:
            record = {
                key: child for key, child in value.items()
                if key not in proof_fields and not (axiom and key == "source")
            }
            if digest in catalog and catalog[digest] != record:
                raise ContextPageError("context_fact_digest_collision")
            catalog[digest] = record
            return {"fact_ref": digest}
        return {key: visit(child) for key, child in value.items()}

    sections = visit(pack.sections)
    sections["fact_catalog"] = catalog
    return replace(
        pack, sections=sections, schema_version="canon-ledger-context-pack/compact-v1"
    )


class ContextPager:
    """Prepare one immutable context once, then slice it without reloading."""

    def __init__(self, pack: ContextPack, *, budget_tokens: int = 4000) -> None:
        if isinstance(budget_tokens, bool) or budget_tokens < 1:
            raise ContextPageError("context_page_budget_must_be_positive")
        self.source = pack.to_dict()
        self.budget_tokens = budget_tokens
        self.digest = canonical_digest({
            "schema_version": CONTEXT_PAGE_SCHEMA,
            "source": self.source,
            "page_budget_tokens": budget_tokens,
        })
        self.entries = list(_entries(
            self.source["sections"], "/sections", max(1, budget_tokens // 3)
        ))
        workflow = self.source["sections"].get("runtime_status", {}).get("workflow_snapshot", {})
        self.binding = {
            name: workflow.get(name)
            for name in ("head_hash", "generation", "workflow_digest", "author_axiom_digest")
        }

    def _render(self, start: int, end: int) -> dict[str, Any]:
        result = {
            "schema_version": CONTEXT_PAGE_SCHEMA,
            "context_schema_version": self.source["schema_version"],
            "chapter": self.source["chapter"],
            "as_of_chapter": max(0, self.source["chapter"] - 1),
            "context_digest": self.digest,
            "binding": self.binding,
            "source_completeness": self.source["completeness"],
            "delivery": {
                "start": start,
                "end": end,
                "total_entries": len(self.entries),
                "has_more": end < len(self.entries),
                "next_cursor": f"{self.digest}:{end}" if end < len(self.entries) else None,
                "contains_entire_context": start == 0 and end == len(self.entries),
                "budget_tokens": self.budget_tokens,
                "estimated_tokens": 0,
                "over_budget": False,
            },
            "entries": self.entries[start:end],
        }
        for _ in range(8):
            used = _estimate_tokens(result)
            if result["delivery"]["estimated_tokens"] == used:
                break
            result["delivery"]["estimated_tokens"] = used
        # false is the longer JSON spelling, so this remains conservative.
        result["delivery"]["over_budget"] = used > self.budget_tokens
        return result

    def page(self, cursor: str | None = None) -> dict[str, Any]:
        start = 0
        if cursor is not None:
            match = _CURSOR_RE.fullmatch(cursor)
            if match is None:
                raise ContextPageError("context_page_cursor_invalid")
            if match[1] != self.digest:
                raise ContextPageError("context_page_cursor_stale_restart_from_first_page")
            start = int(match[2])
        if start >= len(self.entries):
            raise ContextPageError("context_page_cursor_out_of_range")
        end = start + 1
        result = self._render(start, end)
        while end < len(self.entries):
            trial = self._render(start, end + 1)
            if trial["delivery"]["over_budget"]:
                break
            end += 1
            result = trial
        return result

    def all_pages(self) -> dict[str, Any]:
        pages = []
        cursor = None
        while True:
            page = self.page(cursor)
            pages.append(page)
            cursor = page["delivery"]["next_cursor"]
            if cursor is None:
                break
        return {
            "schema_version": "canon-ledger-context-pages/v1",
            "chapter": self.source["chapter"],
            "context_digest": self.digest,
            "binding": self.binding,
            "source_completeness": self.source["completeness"],
            "page_count": len(pages),
            "pages": pages,
        }


def paginate_context(
    pack: ContextPack, *, budget_tokens: int = 4000, cursor: str | None = None
) -> dict[str, Any]:
    """Compatibility interface for one page. Bulk readers should reuse a pager."""
    return ContextPager(pack, budget_tokens=budget_tokens).page(cursor)
