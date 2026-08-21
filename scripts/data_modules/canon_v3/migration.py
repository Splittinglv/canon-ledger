#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed cutover from the v2 accepted-commit prefix to Canon v3.

The migration does not reinterpret v2 model output.  It asks the existing
canonical-history reader for the fact-only view that v2 currently considers
trusted, binds that view to the exact v2 commit bytes and manuscript bindings,
and stores the complete material in the immutable v3 genesis manifest.

No wall-clock values are recorded.  Identical source material therefore
produces identical genesis metadata and the same content-addressed HEAD.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..canonical_history import history_to_asof_snapshot, load_canonical_history
from ..canon_evidence import (
    LegacyCutoverEvidenceError,
    bind_legacy_event_quote_span,
    cutover_commit_linked_records,
    validate_legacy_cutover_event,
)
from ..chapter_content_binding import verify_commit_content_binding
from ..commit_lineage import VALIDATION_NEEDS_REVALIDATION
from ..fact_text import bound_chapter_text_for_commit, normalize_author_text
from ..human_review import (
    human_decision_receipt_sha256,
    verified_event_content_sha256,
)
from .projection import projection_is_fresh, rebuild_projection
from .repository import (
    CanonIntegrityError,
    CanonRepositoryError,
    CanonV3Repository,
    RECERTIFIED_SUFFIX_TRANSACTION_SCHEMA,
    canonical_json_bytes,
    content_hash,
)
from .review import ReviewAction, decision_from_dict


LEGACY_GENESIS_SCHEMA_V1 = "canon-v3/legacy-genesis/v1"
LEGACY_GENESIS_SCHEMA = "canon-v3/legacy-genesis/v2"
LEGACY_COMMIT_REF_SCHEMA = "canon-v3/legacy-v2-commit-ref/v2"
LEGACY_SNAPSHOT_SCHEMA = "canon-v3/legacy-fact-snapshot/v2"
LEGACY_ADMISSION_SCHEMA = "canon-v3/legacy-fact-admission/v2"
LEGACY_STATUS_SCHEMA = "canon-v3/legacy-prefix-status/v2"
LEGACY_MIGRATION_RESULT_SCHEMA = "canon-v3/legacy-migration-result/v2"
LEGACY_CUTOVER_AUDIT_SCHEMA = "canon-v3/legacy-cutover-audit/v2"
LEGACY_REPAIR_DRY_RUN_SCHEMA = "canon-v3/legacy-repair-dry-run/v2"
LEGACY_RECERTIFICATION_CASE_SCHEMA = "canon-v3/legacy-recertification-case/v1"
LEGACY_RECERTIFICATION_MATERIAL_SCHEMA = (
    "canon-v3/legacy-recertification-review-material/v1"
)
LEGACY_RECERTIFICATION_DECISION_SCHEMA = (
    "canon-v3/legacy-recertification-decision/v1"
)
LEGACY_RECERTIFICATION_PUBLISH_REQUEST_SCHEMA = (
    "canon-v3/legacy-recertification-publish-request/v1"
)
LEGACY_RECERTIFICATION_RECEIPT_SCHEMA = (
    "canon-v3/legacy-recertification-receipt/v1"
)
LEGACY_RECERTIFICATION_RESULT_SCHEMA = (
    "canon-v3/legacy-recertification-result/v1"
)
LEGACY_RECERTIFIED_SUFFIX_TRANSACTION_SCHEMA = (
    RECERTIFIED_SUFFIX_TRANSACTION_SCHEMA
)

_NEGATIVE_REVIEW_ACTIONS = frozenset(
    {ReviewAction.OMIT, ReviewAction.REWRITE, ReviewAction.CORRECT}
)
_POSITIVE_REVIEW_ACTIONS = frozenset(
    {ReviewAction.APPROVE, ReviewAction.NO_CONFLICT}
)
_TARGET_EVENT_TYPES = frozenset(
    {
        "relationship_changed",
        "world_rule_broken",
        "knowledge_state_changed",
        "presence_observed",
        "custody_changed",
        "open_loop_closed",
        "promise_paid_off",
    }
)

_COMMIT_NAME = re.compile(r"^chapter_(\d+)\.commit\.json$")
_LONG_TERM_EVENT_TYPES = frozenset(
    {
        "character_state_changed",
        "relationship_changed",
        "world_rule_revealed",
        "world_rule_broken",
        "power_breakthrough",
        "artifact_obtained",
        "entity_observed",
        "timeline_observed",
        "knowledge_state_changed",
        "presence_observed",
        "custody_changed",
        "promise_created",
        "promise_paid_off",
        "open_loop_created",
        "open_loop_closed",
    }
)


class LegacyMigrationError(RuntimeError):
    """A v2 prefix cannot be proved safe enough to become the v3 base."""

    def __init__(self, code: str, *details: str) -> None:
        self.code = str(code)
        self.details = tuple(str(item) for item in details if str(item))
        suffix = f":{':'.join(self.details)}" if self.details else ""
        super().__init__(f"{self.code}{suffix}")


@dataclass(frozen=True)
class _LegacyMaterial:
    source: str
    cutover_chapter: int
    commits: tuple[dict[str, Any], ...]
    snapshot: dict[str, Any]
    snapshot_sha256: str

    def genesis_metadata(
        self,
        *,
        recertification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = {
            "schema_version": LEGACY_GENESIS_SCHEMA,
            "source": self.source,
            "cutover_chapter": self.cutover_chapter,
            "v2_commits": [dict(item) for item in self.commits],
            "legacy_snapshot": self.snapshot,
            "legacy_snapshot_sha256": self.snapshot_sha256,
        }
        if recertification is not None:
            metadata["recertification"] = copy.deepcopy(dict(recertification))
        return metadata


def _root(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve()


def _commit_paths(project_root: Path) -> list[tuple[int, Path]]:
    commits_dir = project_root / ".story-system" / "commits"
    if not commits_dir.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    seen: dict[int, Path] = {}
    for path in sorted(commits_dir.iterdir(), key=lambda item: item.name):
        match = _COMMIT_NAME.fullmatch(path.name)
        if not match or not path.is_file():
            continue
        chapter = int(match.group(1))
        if chapter <= 0:
            raise LegacyMigrationError("legacy_commit_chapter_invalid", path.name)
        prior = seen.get(chapter)
        if prior is not None:
            raise LegacyMigrationError(
                "legacy_commit_duplicate_chapter",
                str(chapter),
                prior.name,
                path.name,
            )
        seen[chapter] = path
        found.append((chapter, path))
    return found


def _read_commit(path: Path, chapter: int) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LegacyMigrationError(
            "legacy_commit_unreadable", str(chapter), path.name
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LegacyMigrationError(
            "legacy_commit_invalid_json", str(chapter), path.name
        ) from exc
    if not isinstance(payload, dict):
        raise LegacyMigrationError(
            "legacy_commit_root_invalid", str(chapter), path.name
        )
    return raw, payload


def _accepted_status(payload: Mapping[str, Any]) -> bool:
    meta = payload.get("meta")
    return bool(
        isinstance(meta, Mapping)
        and str(meta.get("status") or "") == "accepted"
    )


def _discover_default_cutover(project_root: Path) -> int:
    """Return the highest accepted chapter, validating readable candidates."""
    accepted: list[int] = []
    for chapter, path in _commit_paths(project_root):
        _raw, payload = _read_commit(path, chapter)
        if _accepted_status(payload):
            accepted.append(chapter)
    return max(accepted, default=0)


def _normalize_cutover(value: Any) -> int:
    if isinstance(value, bool):
        raise LegacyMigrationError("legacy_cutover_chapter_invalid", repr(value))
    try:
        chapter = int(value)
    except (TypeError, ValueError) as exc:
        raise LegacyMigrationError(
            "legacy_cutover_chapter_invalid", repr(value)
        ) from exc
    if chapter < 0:
        raise LegacyMigrationError("legacy_cutover_chapter_invalid", str(chapter))
    return chapter


def _accepted_commit_refs(
    project_root: Path,
    cutover_chapter: int,
) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = []
    accepted_chapters: list[int] = []
    for chapter, path in _commit_paths(project_root):
        if chapter > cutover_chapter:
            continue
        raw, payload = _read_commit(path, chapter)
        if not _accepted_status(payload):
            continue
        meta = payload.get("meta")
        declared_chapter = meta.get("chapter") if isinstance(meta, Mapping) else None
        if declared_chapter != chapter:
            raise LegacyMigrationError(
                "legacy_commit_chapter_mismatch",
                str(chapter),
                repr(declared_chapter),
            )
        if (
            isinstance(meta, Mapping)
            and str(meta.get("validation_status") or "")
            == VALIDATION_NEEDS_REVALIDATION
        ):
            raise LegacyMigrationError(
                "legacy_commit_needs_revalidation", str(chapter)
            )
        binding_ok, binding_code = verify_commit_content_binding(
            project_root, chapter, payload
        )
        if not binding_ok:
            raise LegacyMigrationError(
                "legacy_commit_binding_invalid", str(chapter), binding_code
            )
        binding = payload.get("chapter_binding")
        if not isinstance(binding, dict):  # The verifier already rejects this.
            raise LegacyMigrationError(
                "legacy_commit_binding_invalid", str(chapter), "missing"
            )
        accepted_chapters.append(chapter)
        refs.append(
            {
                "schema_version": LEGACY_COMMIT_REF_SCHEMA,
                "chapter": chapter,
                "path": path.relative_to(project_root).as_posix(),
                "content_sha256": hashlib.sha256(raw).hexdigest(),
                "manuscript_binding": json.loads(
                    canonical_json_bytes(binding).decode("utf-8")
                ),
            }
        )

    expected = list(range(1, cutover_chapter + 1))
    if accepted_chapters != expected:
        missing = sorted(set(expected) - set(accepted_chapters))
        raise LegacyMigrationError(
            "legacy_commit_prefix_not_contiguous",
            ",".join(str(item) for item in missing) or "unknown",
        )
    return tuple(refs)


def _load_v2_human_decisions(project_root: Path) -> list[dict[str, Any]]:
    path = project_root / ".canon-ledger" / "human-review" / "decisions.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("decisions") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _exact_human_event_admission(
    commit: Mapping[str, Any],
    chapter: int,
    event: Mapping[str, Any],
    decisions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return proof only when commit, ledger and exact event all agree."""

    if str(event.get("verification") or "").strip().lower() != "verified":
        return None
    event_id = str(event.get("event_id") or "").strip()
    binding = commit.get("chapter_binding")
    chapter_sha256 = (
        str(binding.get("sha256") or "").strip()
        if isinstance(binding, Mapping)
        else ""
    )
    provenance = commit.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    human = provenance.get("human_review")
    human = human if isinstance(human, Mapping) else {}
    resolved_ids = {
        str(value).strip()
        for value in human.get("resolved_decision_ids") or []
        if str(value).strip()
    }
    verified_ids = {
        str(value).strip()
        for value in human.get("verified_event_ids") or []
        if str(value).strip()
    }
    receipts: dict[str, str] = {}
    for row in human.get("decision_receipts") or []:
        if not isinstance(row, Mapping):
            continue
        decision_id = str(row.get("decision_id") or "").strip()
        digest = str(row.get("decision_sha256") or "").strip()
        if decision_id and digest:
            receipts[decision_id] = digest
    if not event_id or event_id not in verified_ids or not chapter_sha256:
        return None
    event_digest = verified_event_content_sha256(chapter, dict(event))
    for decision in sorted(
        decisions,
        key=lambda row: str(row.get("decision_id") or ""),
    ):
        decision_id = str(decision.get("decision_id") or "").strip()
        if (
            decision_id not in resolved_ids
            or str(decision.get("action") or "").strip() not in {"confirm", "replace"}
            or int(decision.get("chapter") or 0) != chapter
            or str(decision.get("chapter_sha256") or "").strip() != chapter_sha256
            or str(decision.get("verified_event_id") or "").strip() != event_id
            or str(decision.get("verified_event_sha256") or "").strip()
            != event_digest
        ):
            continue
        receipt = human_decision_receipt_sha256(decision)
        if receipts.get(decision_id) != receipt:
            continue
        return {
            "mode": "exact_human_decision",
            "event_digest": content_hash(event),
            "chapter_sha256": chapter_sha256,
            "decision_id": decision_id,
            "decision_receipt_sha256": receipt,
            "verified_event_sha256": event_digest,
        }
    return None


def _normalized_contains_text(haystack: Any, needle: Any) -> bool:
    left = " ".join(
        unicodedata.normalize("NFKC", str(haystack or "")).casefold().split()
    )
    right = " ".join(
        unicodedata.normalize("NFKC", str(needle or "")).casefold().split()
    )
    return bool(right) and right in left


def _payload_first(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = normalize_author_text(payload.get(key), max_chars=600)
        if value:
            return value
    return ""


def _opaque_semantic_key(*parts: Any) -> str:
    return content_hash(
        [normalize_author_text(part, max_chars=600) for part in parts]
    )


def _register_opaque_id(
    registry: dict[tuple[str, str], tuple[str, str]],
    *,
    family: str,
    opaque_id: str,
    semantic_key: str,
    description: str,
) -> None:
    if not opaque_id:
        return
    key = (family, opaque_id)
    previous = registry.get(key)
    if previous is not None and previous[0] != semantic_key:
        raise LegacyMigrationError(
            "legacy_opaque_id_semantic_conflict",
            f"{family}={opaque_id}",
            f"first={previous[1]}",
            f"second={description}",
        )
    registry[key] = (semantic_key, description)


def _validate_opaque_event_transition(
    event: Mapping[str, Any],
    *,
    human_approved: bool,
    registry: dict[tuple[str, str], tuple[str, str]],
    lifecycle_targets: dict[tuple[str, str], tuple[str, str]],
) -> None:
    """Prove opaque legacy identifiers do not select fact semantics.

    IDs remain provenance aliases only.  A terminal transition must identify
    one exact prior creation and the current quote must name that prior's
    semantic content unless an exact human decision approved the whole event.
    """

    event_type = str(event.get("event_type") or "").strip()
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    event_id = str(event.get("event_id") or "").strip()
    quote = normalize_author_text(payload.get("evidence_quote"), max_chars=600)
    if event_type == "world_rule_revealed":
        opaque_id = str(payload.get("rule_id") or "").strip()
        domain = _payload_first(payload, "domain")
        field = _payload_first(payload, "field")
        content = _payload_first(payload, "rule_content", "content", "rule", "value")
        semantic = _opaque_semantic_key(domain, field)
        _register_opaque_id(
            registry,
            family="rule_id",
            opaque_id=opaque_id,
            semantic_key=semantic,
            description=f"{domain}|{field}",
        )
        if opaque_id:
            lifecycle_targets[("rule", opaque_id)] = (semantic, content)
    elif event_type == "world_rule_broken":
        opaque_id = str(
            payload.get("rule_id") or payload.get("target_rule_id") or ""
        ).strip()
        if opaque_id:
            prior = lifecycle_targets.get(("rule", opaque_id))
            if prior is None:
                raise LegacyMigrationError(
                    "legacy_terminal_target_not_found",
                    f"event_id={event_id}",
                    f"rule_id={opaque_id}",
                )
            declared_domain = _payload_first(payload, "domain")
            declared_field = _payload_first(payload, "field")
            if declared_domain or declared_field:
                declared = _opaque_semantic_key(declared_domain, declared_field)
                if declared != prior[0]:
                    raise LegacyMigrationError(
                        "legacy_terminal_target_not_supported",
                        f"event_id={event_id}",
                        f"rule_id={opaque_id}",
                        "descriptor_mismatch",
                    )
            prior_content = prior[1]
            base_value = _payload_first(payload, "base_value")
            prior_named = _normalized_contains_text(quote, prior_content)
            base_names_prior = (
                bool(base_value)
                and base_value == prior_content
                and _normalized_contains_text(quote, base_value)
            )
            if not human_approved and not (prior_named or base_names_prior):
                raise LegacyMigrationError(
                    "legacy_terminal_target_not_supported",
                    f"event_id={event_id}",
                    f"rule_id={opaque_id}",
                    f"prior={prior_content}",
                )
    elif event_type == "timeline_observed":
        opaque_id = str(payload.get("timeline_id") or "").strip()
        event_text = _payload_first(payload, "event", "content") or str(
            event.get("subject") or ""
        ).strip()
        anchor = _payload_first(payload, "time_anchor", "time_hint", "time")
        semantic = _opaque_semantic_key(event_text, anchor)
        _register_opaque_id(
            registry,
            family="timeline_id",
            opaque_id=opaque_id,
            semantic_key=semantic,
            description=f"{event_text}|{anchor}",
        )
    elif event_type == "knowledge_state_changed":
        opaque_id = str(payload.get("information_id") or "").strip()
        claim = _payload_first(payload, "canonical_claim", "content")
        semantic = _opaque_semantic_key(claim)
        _register_opaque_id(
            registry,
            family="information_id",
            opaque_id=opaque_id,
            semantic_key=semantic,
            description=claim,
        )
    elif event_type in {"open_loop_created", "promise_created"}:
        family = "loop" if event_type == "open_loop_created" else "promise"
        keys = (
            ("loop_id", "open_loop_id")
            if family == "loop"
            else ("promise_id",)
        )
        opaque_id = next(
            (
                str(payload.get(key) or "").strip()
                for key in keys
                if str(payload.get(key) or "").strip()
            ),
            event_id,
        )
        content = _payload_first(
            payload,
            "content",
            "unanswered_question",
            "description",
        ) or str(event.get("subject") or "").strip()
        semantic = _opaque_semantic_key(family, content)
        key = (family, opaque_id)
        if key in lifecycle_targets:
            raise LegacyMigrationError(
                "legacy_opaque_id_semantic_conflict",
                f"{family}_id={opaque_id}",
                "lifecycle_creation_reused",
            )
        lifecycle_targets[key] = (semantic, content)
        _register_opaque_id(
            registry,
            family=f"{family}_id",
            opaque_id=opaque_id,
            semantic_key=semantic,
            description=content,
        )
    elif event_type in {"open_loop_closed", "promise_paid_off"}:
        family = "loop" if event_type == "open_loop_closed" else "promise"
        keys = (
            ("loop_id", "target_loop_id", "open_loop_id", "target_id", "resolves_event_id")
            if family == "loop"
            else ("promise_id", "target_promise_id", "target_id", "resolves_event_id")
        )
        opaque_id = next(
            (
                str(payload.get(key) or "").strip()
                for key in keys
                if str(payload.get(key) or "").strip()
            ),
            "",
        )
        prior = lifecycle_targets.get((family, opaque_id))
        if prior is None:
            raise LegacyMigrationError(
                "legacy_terminal_target_not_found",
                f"event_id={event_id}",
                f"{family}_id={opaque_id or '<missing>'}",
            )
        prior_content = prior[1]
        if not human_approved and not _normalized_contains_text(quote, prior_content):
            raise LegacyMigrationError(
                "legacy_terminal_target_not_supported",
                f"event_id={event_id}",
                f"{family}_id={opaque_id}",
                f"prior={prior_content}",
            )
        lifecycle_targets.pop((family, opaque_id), None)


_LEGACY_LIST_FACT_CHANNELS = (
    "canonical_facts",
    "hard_constraints",
    "rules",
    "obligations",
    "lifecycle_history",
    "state_changes",
    "timeline",
    "presence_history",
    "custody_history",
)
_LEGACY_MAP_FACT_CHANNELS = ("information", "presence", "custody")
_NAMESPACE_TYPE = {"actor": "角色", "item": "物品", "location": "地点"}


def _identity_token(value: Any) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).split()
    ).strip()


def _legacy_entity_namespace(entity: Mapping[str, Any]) -> str:
    explicit = str(entity.get("namespace") or "").strip().lower()
    type_text = str(entity.get("type") or entity.get("entity_type") or "").lower()
    inferred = (
        "location"
        if any(marker in type_text for marker in ("地点", "场所", "location"))
        else "item"
        if any(marker in type_text for marker in ("物品", "法宝", "道具", "item"))
        else "actor"
        if any(marker in type_text for marker in ("角色", "人物", "actor", "person"))
        else ""
    )
    if explicit and explicit not in _NAMESPACE_TYPE:
        raise LegacyMigrationError(
            "legacy_identity_namespace_invalid", explicit
        )
    if explicit and inferred and explicit != inferred:
        raise LegacyMigrationError(
            "legacy_identity_namespace_type_conflict",
            f"namespace={explicit}",
            f"type={type_text}",
        )
    return explicit or inferred or "actor"


def _normalize_cutover_identity_graph(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize aliases before any state-like legacy slot is folded."""

    facts = copy.deepcopy(payload)
    raw_entities = facts.get("entities") or {}
    if not isinstance(raw_entities, dict):
        raise LegacyMigrationError("legacy_entities_invalid")
    token_targets: dict[tuple[str, str], str] = {}
    canonical_entities: dict[str, dict[str, Any]] = {}
    receipts: list[dict[str, Any]] = []
    initial_canon = facts.get("initial_canon")
    protagonist = (
        initial_canon.get("protagonist")
        if isinstance(initial_canon, Mapping)
        and isinstance(initial_canon.get("protagonist"), Mapping)
        else {}
    )
    protagonist_name = _identity_token(protagonist.get("name"))

    def row_order(item: tuple[str, Any]) -> tuple[int, int, str]:
        key, raw = item
        row = raw if isinstance(raw, Mapping) else {}
        first = int(row.get("first_appearance") or 0)
        source = int(row.get("source_chapter") or 0)
        return (
            first or source or 2**31 - 1,
            source or 2**31 - 1,
            str(key),
        )

    for legacy_key, raw in sorted(raw_entities.items(), key=row_order):
        if not isinstance(raw, Mapping):
            raise LegacyMigrationError(
                "legacy_entity_invalid", str(legacy_key)
            )
        entity = copy.deepcopy(dict(raw))
        namespace = _legacy_entity_namespace(entity)
        raw_id = _identity_token(entity.get("id") or legacy_key)
        if raw_id.startswith(f"{namespace}:"):
            raw_id = raw_id.split(":", 1)[1]
        if not raw_id:
            raise LegacyMigrationError("legacy_identity_id_missing", str(legacy_key))
        aliases = entity.get("aliases") or []
        if not isinstance(aliases, list):
            raise LegacyMigrationError(
                "legacy_identity_aliases_invalid", raw_id
            )
        display = _identity_token(entity.get("name") or raw_id)
        tokens = [raw_id, display, *(_identity_token(item) for item in aliases)]
        tokens = [token for token in dict.fromkeys(tokens) if token]
        direct = token_targets.get((namespace, raw_id))
        collided = {
            token_targets[(namespace, token)]
            for token in tokens
            if (namespace, token) in token_targets
        }
        if len(collided) > 1:
            raise LegacyMigrationError(
                "legacy_identity_alias_ambiguous",
                f"namespace={namespace}",
                f"entity={raw_id}",
                "targets=" + ",".join(sorted(collided)),
            )
        if direct:
            canonical = direct
        elif collided:
            # A later raw ID that is already an approved alias is the legacy
            # form of the same instance (item-bell -> 铜铃). A display-name-only
            # collision between two distinct raw IDs is ambiguous and blocks.
            canonical = next(iter(collided))
            if (namespace, raw_id) not in token_targets:
                raise LegacyMigrationError(
                    "legacy_identity_alias_ambiguous",
                    f"namespace={namespace}",
                    f"entity={raw_id}",
                    f"candidate={canonical}",
                )
        else:
            canonical = raw_id

        existing = canonical_entities.get(canonical)
        if existing is None:
            merged = entity
            merged["id"] = canonical
            merged["namespace"] = namespace
            merged["type"] = _NAMESPACE_TYPE[namespace]
            merged["name"] = display or canonical
            merged["aliases"] = []
            canonical_entities[canonical] = merged
        else:
            if str(existing.get("namespace") or "") != namespace:
                raise LegacyMigrationError(
                    "legacy_identity_namespace_conflict", canonical
                )
            merged = existing
            merged["first_appearance"] = min(
                int(merged.get("first_appearance") or 0),
                int(entity.get("first_appearance") or 0),
            )
            merged["last_appearance"] = max(
                int(merged.get("last_appearance") or 0),
                int(entity.get("last_appearance") or 0),
            )
            if int(entity.get("source_chapter") or 0) >= int(
                merged.get("source_chapter") or 0
            ):
                for key in ("source_chapter", "source_event_id"):
                    if entity.get(key) not in (None, ""):
                        merged[key] = entity[key]
        merged_aliases = {
            _identity_token(item)
            for item in merged.get("aliases") or []
            if _identity_token(item)
        }
        merged_aliases.update(tokens)
        merged_aliases.discard(str(merged.get("name") or ""))
        merged_aliases.discard(canonical)
        merged["aliases"] = sorted(merged_aliases)
        # Reader-only defaults such as ``tier=核心`` and the duplicated
        # ``attributes`` map are not independent admitted facts. State facts
        # remain in their typed channels; identity records retain only fields
        # that are evidence-bound or deterministically derived from namespace.
        merged.pop("attributes", None)
        if str(merged.get("tier") or "") == "核心":
            merged.pop("tier", None)
        for token in tokens:
            key = (namespace, token)
            previous = token_targets.get(key)
            if previous is not None and previous != canonical:
                raise LegacyMigrationError(
                    "legacy_identity_alias_ambiguous",
                    f"namespace={namespace}",
                    f"alias={token}",
                )
            token_targets[key] = canonical
        receipts.append(
            {
                "namespace": namespace,
                "legacy_entity_id": raw_id,
                "canonical_entity_id": canonical,
                "tokens": sorted(tokens),
            }
        )

    def resolve(namespace: str, value: Any) -> str:
        token = _identity_token(value)
        if not token:
            return ""
        if token.startswith(f"{namespace}:"):
            token = token.split(":", 1)[1]
        return token_targets.get((namespace, token), token)

    def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        category = str(row.get("category") or "")
        if category in {
            "character_state",
            "character_state_changed",
            "power_breakthrough",
        }:
            raw_subject = row.get("subject")
            if protagonist_name and str(raw_subject or "") == "protagonist":
                raw_subject = protagonist_name
            row["subject"] = resolve("actor", raw_subject)
        elif category == "relationship":
            row["subject"] = resolve("actor", row.get("subject"))
            row["field"] = resolve("actor", row.get("field"))
        elif category in {"artifact_obtained", "custody"}:
            row["subject"] = resolve("item", row.get("subject"))
        for key in ("entity_id", "subject"):
            if key in row and category in {"presence", "knowledge"}:
                row[key] = resolve("actor", row.get(key))
        if "artifact_id" in row:
            row["artifact_id"] = resolve("item", row.get("artifact_id"))
        for key in ("from_holder", "to_holder", "holder_id", "prior_holder", "source_entity"):
            if key in row and row.get(key) not in (None, ""):
                row[key] = resolve("actor", row.get(key))
        if "location_id" in row and row.get("location_id") not in (None, ""):
            row["location_id"] = resolve("location", row.get("location_id"))
        if "entity_id" in row and category not in {"presence", "knowledge"}:
            row["entity_id"] = resolve("actor", row.get("entity_id"))
        nested = row.get("payload")
        if isinstance(nested, dict):
            for key in ("artifact", "item"):
                if key in nested:
                    nested[key] = resolve("item", nested.get(key))
            for key in ("owner", "from_holder", "to_holder", "source_entity"):
                if key in nested and nested.get(key) not in (None, ""):
                    nested[key] = resolve("actor", nested.get(key))
            if "location_id" in nested and nested.get("location_id") not in (None, ""):
                nested["location_id"] = resolve("location", nested.get("location_id"))
        return row

    for channel in _LEGACY_LIST_FACT_CHANNELS:
        rows = facts.get(channel) or []
        if not isinstance(rows, list):
            raise LegacyMigrationError(f"legacy_{channel}_invalid")
        facts[channel] = [normalize_row(dict(row)) for row in rows if isinstance(row, Mapping)]
    for channel in _LEGACY_MAP_FACT_CHANNELS:
        rows = facts.get(channel) or {}
        if not isinstance(rows, dict):
            raise LegacyMigrationError(f"legacy_{channel}_invalid")
        normalized_map: dict[str, dict[str, Any]] = {}
        for legacy_key, raw in sorted(rows.items()):
            if not isinstance(raw, Mapping):
                raise LegacyMigrationError(f"legacy_{channel}_invalid")
            row = normalize_row(dict(raw))
            key = str(legacy_key)
            if channel == "presence":
                key = resolve("actor", row.get("entity_id") or key)
            elif channel == "custody":
                key = resolve("item", row.get("artifact_id") or key)
            normalized_map[key] = row
        facts[channel] = normalized_map

    knowledge = facts.get("knowledge_by_entity") or {}
    if not isinstance(knowledge, dict):
        raise LegacyMigrationError("legacy_knowledge_by_entity_invalid")
    normalized_knowledge: dict[str, dict[str, Any]] = {}
    for actor, rows in sorted(knowledge.items()):
        if not isinstance(rows, dict):
            raise LegacyMigrationError("legacy_knowledge_by_entity_invalid")
        canonical_actor = resolve("actor", actor)
        target = normalized_knowledge.setdefault(canonical_actor, {})
        for key, raw in sorted(rows.items()):
            if isinstance(raw, Mapping):
                target[str(key)] = normalize_row(dict(raw))
    facts["knowledge_by_entity"] = normalized_knowledge

    audits = facts.get("long_term_event_audit") or []
    if isinstance(audits, list):
        for audit in audits:
            if not isinstance(audit, dict):
                continue
            normalized = [
                normalize_row(dict(row))
                for row in audit.get("normalized_facts") or []
                if isinstance(row, Mapping)
            ]
            audit["normalized_facts"] = normalized
            audit["normalized_fact_digests"] = [content_hash(row) for row in normalized]

    # Re-fold custody after item alias normalization. This catches conflicting
    # holder transitions instead of keeping two active slots for one object.
    custody_active: dict[str, dict[str, Any]] = {}
    for row in sorted(
        facts.get("custody_history") or [],
        key=lambda item: (
            int(item.get("source_chapter") or 0),
            int(item.get("sequence") or 0),
            str(item.get("event_id") or ""),
        ),
    ):
        item_id = resolve("item", row.get("artifact_id"))
        row["artifact_id"] = item_id
        prior = custody_active.get(item_id)
        prior_holder = str((prior or {}).get("holder_id") or "")
        declared_from = str(row.get("from_holder") or "")
        if prior is not None and declared_from != prior_holder:
            raise LegacyMigrationError(
                "legacy_custody_transition_conflict",
                f"item={item_id}",
                f"expected_from={prior_holder}",
                f"declared_from={declared_from}",
            )
        row["prior_holder"] = prior_holder
        row["transition_consistent"] = True
        custody_active[item_id] = dict(row)
    facts["custody"] = dict(sorted(custody_active.items()))
    presence_active: dict[str, dict[str, Any]] = {}
    for row in sorted(
        facts.get("presence_history") or [],
        key=lambda item: (
            int(item.get("source_chapter") or 0),
            int(item.get("sequence") or 0),
            str(item.get("event_id") or ""),
        ),
    ):
        actor = resolve("actor", row.get("entity_id") or row.get("subject"))
        row["entity_id"] = actor
        if str(row.get("presence_kind") or "") == "physical":
            presence_active[actor] = dict(row)
    facts["presence"] = dict(sorted(presence_active.items()))

    state_slots: dict[tuple[str, str], Any] = {}
    for row in sorted(
        facts.get("state_changes") or [],
        key=lambda item: (
            int(item.get("chapter") or item.get("source_chapter") or 0),
            str(item.get("id") or item.get("source_event_id") or ""),
        ),
    ):
        key = (
            resolve("actor", row.get("entity_id") or row.get("subject")),
            str(row.get("field") or ""),
        )
        prior = state_slots.get(key)
        declared_old = row.get("old")
        if prior is not None and declared_old not in (None, "") and declared_old != prior:
            raise LegacyMigrationError(
                "legacy_state_transition_prior_mismatch",
                f"entity={key[0]}",
                f"field={key[1]}",
                f"expected={prior!r}",
                f"declared={declared_old!r}",
            )
        state_slots[key] = row.get("new")
    facts["entities"] = dict(sorted(canonical_entities.items()))
    return facts, sorted(
        receipts,
        key=lambda row: (
            row["namespace"], row["legacy_entity_id"], row["canonical_entity_id"]
        ),
    )


def _legacy_row_slot(row: Mapping[str, Any]) -> str:
    category = str(row.get("category") or "")
    payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
    if category in {"character_state", "character_state_changed", "power_breakthrough"}:
        descriptor = {
            "family": "character_state",
            "subject": str(row.get("subject") or row.get("entity_id") or ""),
            "field": str(row.get("field") or payload.get("system") or "realm"),
        }
    elif category == "relationship":
        descriptor = {
            "family": "relationship",
            "subject": str(row.get("subject") or ""),
            "object": str(row.get("field") or ""),
        }
    elif category == "world_rule":
        descriptor = {
            "family": "world_rule",
            "domain": str(row.get("subject") or ""),
            "field": str(row.get("field") or ""),
        }
    elif category in {"open_loop", "open_loop_created", "open_loop_closed"}:
        descriptor = {
            "family": "open_loop",
            "origin_event": str(
                payload.get("lifecycle_origin_event_id")
                or row.get("source_event_id")
                or row.get("event_id")
                or ""
            ),
            "semantic": str(payload.get("loop") or row.get("value") or ""),
        }
    elif category in {"reader_promise", "promise_created", "promise_paid", "promise_paid_off"}:
        descriptor = {
            "family": "promise",
            "origin_event": str(
                payload.get("lifecycle_origin_event_id")
                or row.get("source_event_id")
                or row.get("event_id")
                or ""
            ),
            "semantic": str(payload.get("promise") or row.get("value") or ""),
        }
    elif category in {"timeline", "timeline_observed"}:
        descriptor = {
            "family": "timeline_occurrence",
            "chapter": int(row.get("source_chapter") or 0),
            "source_event_id": str(row.get("source_event_id") or row.get("event_id") or ""),
            "event": str(row.get("value") or payload.get("event") or ""),
        }
    elif category in {"knowledge", "knowledge_state_changed"}:
        descriptor = {
            "family": "knowledge",
            "subject": str(row.get("subject") or row.get("entity_id") or ""),
            "claim": str(
                row.get("canonical_claim")
                or row.get("content")
                or row.get("value")
                or ""
            ),
        }
    elif category in {"presence", "presence_observed"}:
        descriptor = {
            "family": "presence",
            "subject": str(row.get("subject") or row.get("entity_id") or ""),
        }
    elif category in {"custody", "custody_changed", "artifact_obtained"}:
        descriptor = {
            "family": "custody",
            "item": str(
                row.get("artifact_id")
                or row.get("subject")
                or payload.get("artifact")
                or ""
            ),
        }
    else:
        descriptor = {
            "family": "occurrence",
            "category": category,
            "chapter": int(row.get("source_chapter") or 0),
            "source_event_id": str(row.get("source_event_id") or row.get("event_id") or row.get("id") or ""),
        }
    return content_hash(descriptor)


def _assign_cutover_slots(payload: dict[str, Any]) -> dict[str, Any]:
    facts = copy.deepcopy(payload)
    lifecycle_slots: dict[str, str] = {}
    for row in facts.get("lifecycle_history") or []:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category") or "")
        nested = row.setdefault("payload", {})
        if not isinstance(nested, dict):
            raise LegacyMigrationError("legacy_lifecycle_payload_invalid")
        legacy_id = str(nested.get("lifecycle_id") or "")
        if category in {"open_loop", "reader_promise", "open_loop_created", "promise_created"}:
            origin = str(row.get("source_event_id") or row.get("id") or "")
            nested["lifecycle_origin_event_id"] = origin
            slot = _legacy_row_slot(row)
            if legacy_id:
                previous = lifecycle_slots.get(legacy_id)
                if previous is not None and previous != slot:
                    raise LegacyMigrationError(
                        "legacy_opaque_id_semantic_conflict",
                        f"lifecycle_id={legacy_id}",
                    )
                lifecycle_slots[legacy_id] = slot
            row["slot_id"] = slot
        else:
            slot = lifecycle_slots.get(legacy_id)
            if not slot:
                raise LegacyMigrationError(
                    "legacy_terminal_target_not_found",
                    f"lifecycle_id={legacy_id or '<missing>'}",
                )
            nested["lifecycle_origin_event_id"] = slot
            row["slot_id"] = slot
        if legacy_id:
            nested["legacy_lifecycle_id"] = legacy_id
            nested["lifecycle_id"] = row["slot_id"]

    def set_slot(row: dict[str, Any]) -> dict[str, Any]:
        row.setdefault("slot_id", _legacy_row_slot(row))
        return row

    for channel in _LEGACY_LIST_FACT_CHANNELS:
        rows = facts.get(channel) or []
        if isinstance(rows, list):
            normalized_rows: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if channel == "state_changes":
                    row.setdefault("category", "character_state")
                    row.setdefault("subject", str(row.get("entity_id") or ""))
                    row.setdefault("field", str(row.get("field") or ""))
                elif channel == "presence_history":
                    row.setdefault("category", "presence")
                    row.setdefault("subject", str(row.get("entity_id") or ""))
                elif channel == "custody_history":
                    row.setdefault("category", "custody")
                    row.setdefault("subject", str(row.get("artifact_id") or ""))
                normalized_rows.append(set_slot(row))
            facts[channel] = normalized_rows
    for channel in _LEGACY_MAP_FACT_CHANNELS:
        rows = facts.get(channel) or {}
        if isinstance(rows, dict):
            normalized_rows: dict[str, dict[str, Any]] = {}
            for key, row in sorted(rows.items()):
                if not isinstance(row, dict):
                    continue
                if channel == "presence":
                    row.setdefault("category", "presence")
                    row.setdefault(
                        "subject", str(row.get("entity_id") or key)
                    )
                elif channel == "custody":
                    row.setdefault("category", "custody")
                    row.setdefault(
                        "subject", str(row.get("artifact_id") or key)
                    )
                normalized_rows[str(key)] = set_slot(row)
            facts[channel] = normalized_rows
    knowledge = facts.get("knowledge_by_entity") or {}
    if isinstance(knowledge, dict):
        normalized: dict[str, dict[str, Any]] = {}
        for actor, rows in sorted(knowledge.items()):
            if not isinstance(rows, dict):
                continue
            target: dict[str, Any] = {}
            for legacy_id, row in sorted(rows.items()):
                if not isinstance(row, dict):
                    continue
                row.setdefault("category", "knowledge")
                row.setdefault("subject", str(actor))
                row["slot_id"] = _legacy_row_slot(row)
                row["legacy_information_id"] = str(legacy_id)
                row["information_id"] = row["slot_id"]
                target[row["slot_id"]] = row
            normalized[str(actor)] = target
        facts["knowledge_by_entity"] = normalized
    information = facts.get("information") or {}
    if isinstance(information, dict):
        normalized_info: dict[str, dict[str, Any]] = {}
        for legacy_id, row in sorted(information.items()):
            if not isinstance(row, dict):
                continue
            row.setdefault("category", "knowledge_information")
            row["legacy_information_id"] = str(legacy_id)
            row["slot_id"] = content_hash(
                {
                    "family": "information",
                    "claim": str(row.get("canonical_claim") or row.get("content") or ""),
                }
            )
            normalized_info[row["slot_id"]] = row
        facts["information"] = normalized_info

    # Active views are reducers, not histories. Alias normalization may make
    # two former raw keys converge; keep the newest row for state-like slots
    # while occurrence histories remain intact in their dedicated channels.
    for channel in ("canonical_facts", "hard_constraints", "rules", "obligations"):
        rows = facts.get(channel) or []
        if not isinstance(rows, list):
            continue
        reduced: dict[str, dict[str, Any]] = {}
        occurrences: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            category = str(row.get("category") or "")
            state_like = category in {
                "character_state",
                "character_state_changed",
                "power_breakthrough",
                "relationship",
                "world_rule",
                "open_loop",
                "reader_promise",
                "presence",
                "custody",
                "knowledge",
            }
            if not state_like:
                occurrences.append(row)
                continue
            slot = str(row.get("slot_id") or _legacy_row_slot(row))
            previous = reduced.get(slot)
            current_order = (
                int(row.get("source_chapter") or 0),
                str(row.get("source_event_id") or row.get("id") or ""),
            )
            previous_order = (
                int((previous or {}).get("source_chapter") or 0),
                str((previous or {}).get("source_event_id") or (previous or {}).get("id") or ""),
            )
            if previous is None or current_order >= previous_order:
                reduced[slot] = row
        facts[channel] = sorted(
            [*reduced.values(), *occurrences],
            key=lambda row: (
                int(row.get("source_chapter") or 0),
                str(row.get("category") or ""),
                str(row.get("slot_id") or ""),
                str(row.get("id") or ""),
            ),
        )
    return facts


def _build_cutover_fact_admissions(
    payload: dict[str, Any],
    event_admissions: list[dict[str, Any]],
    identity_receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_event = {
        str(row.get("event_id") or ""): row
        for row in event_admissions
        if str(row.get("event_id") or "")
    }
    grouped: dict[str, dict[str, Any]] = {}

    def admit(row: Mapping[str, Any], location: str, *, identity: bool = False) -> None:
        fact = copy.deepcopy(dict(row))
        fact_digest = content_hash(fact)
        source_ids = {
            str(value).strip()
            for value in (
                fact.get("source_event_id"),
                fact.get("event_id"),
            )
            if str(value or "").strip() in by_event
        }
        source_chapter = int(
            fact.get("source_chapter") or fact.get("chapter") or 0
        )
        if source_ids:
            mode = "event_span_and_reducer"
        elif source_chapter == 0:
            mode = "author_axiom_snapshot"
        elif identity:
            # Implicit actor/item/location rows are schema-derived from an
            # admitted typed event. Resolve their proof from canonical usage.
            canonical = str(fact.get("id") or "")
            for event_id, admission in by_event.items():
                event_digest = str(admission.get("event_digest") or "")
                if event_digest and canonical and any(
                    canonical == str(value or "")
                    for value in (
                        fact.get("name"),
                        *(fact.get("aliases") or []),
                    )
                ):
                    source_ids.add(event_id)
            if not source_ids:
                # The entity row itself is not an additional fictional claim;
                # it is a typed identity projection. Its normalization receipt
                # is still exact and all state-bearing rows remain event-bound.
                mode = "identity_projection"
            else:
                mode = "event_identity_projection"
        else:
            raise LegacyMigrationError(
                "legacy_normalized_fact_without_admission", location
            )
        entry = grouped.setdefault(
            fact_digest,
            {
                "schema_version": LEGACY_ADMISSION_SCHEMA,
                "fact_content_sha256": fact_digest,
                "mode": mode,
                "locations": [],
                "source_event_admission_digests": [],
                "identity_normalization_digests": [],
            },
        )
        entry["locations"].append(location)
        entry["source_event_admission_digests"].extend(
            str(by_event[event_id].get("admission_digest") or "")
            for event_id in sorted(source_ids)
        )
        if identity:
            entry["identity_normalization_digests"].extend(
                content_hash(receipt)
                for receipt in identity_receipts
                if str(receipt.get("canonical_entity_id") or "")
                == str(fact.get("id") or "")
            )

    for channel in _LEGACY_LIST_FACT_CHANNELS:
        for index, row in enumerate(payload.get(channel) or []):
            if isinstance(row, Mapping):
                admit(row, f"/{channel}/{index}")
    for channel in _LEGACY_MAP_FACT_CHANNELS:
        rows = payload.get(channel) or {}
        if isinstance(rows, Mapping):
            for key, row in sorted(rows.items()):
                if isinstance(row, Mapping):
                    admit(row, f"/{channel}/{key}")
    knowledge = payload.get("knowledge_by_entity") or {}
    if isinstance(knowledge, Mapping):
        for actor, rows in sorted(knowledge.items()):
            if isinstance(rows, Mapping):
                for key, row in sorted(rows.items()):
                    if isinstance(row, Mapping):
                        admit(row, f"/knowledge_by_entity/{actor}/{key}")
    entities = payload.get("entities") or {}
    if isinstance(entities, Mapping):
        for key, row in sorted(entities.items()):
            if isinstance(row, Mapping):
                admit(row, f"/entities/{key}", identity=True)

    result: list[dict[str, Any]] = []
    for digest in sorted(grouped):
        row = grouped[digest]
        row["locations"] = sorted(set(row["locations"]))
        row["source_event_admission_digests"] = sorted(
            {value for value in row["source_event_admission_digests"] if value}
        )
        row["identity_normalization_digests"] = sorted(
            set(row["identity_normalization_digests"])
        )
        unsigned = dict(row)
        row["admission_digest"] = content_hash(unsigned)
        result.append(row)
    return result


def _fact_snapshot(project_root: Path, cutover_chapter: int) -> dict[str, Any]:
    # Migration/status must always re-read the frozen v2 prefix.  Once CURRENT
    # exists, the default history reader intentionally switches to v3; using
    # that derived view here would compare the genesis snapshot with itself
    # instead of detecting edits to the legacy source files.
    try:
        history = load_canonical_history(
            project_root,
            cutover_chapter,
            prefer_v3=False,
            cutover_strict=True,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "legacy_event_quote_not_in_bound_chapter":
            raise LegacyMigrationError(code) from exc
        raise LegacyMigrationError(
            "legacy_linked_fact_evidence_invalid", code
        ) from exc
    if history.invalid_sources:
        raise LegacyMigrationError(
            "legacy_history_invalid_sources", *sorted(history.invalid_sources)
        )
    expected = list(range(1, cutover_chapter + 1))
    if list(history.valid_chapters) != expected:
        raise LegacyMigrationError(
            "legacy_history_prefix_not_contiguous",
            ",".join(str(item) for item in history.valid_chapters) or "empty",
        )
    if history.omitted_fact_ids:
        raise LegacyMigrationError(
            "legacy_history_has_omitted_long_term_facts",
            *sorted(set(history.omitted_fact_ids)),
        )
    payload = history_to_asof_snapshot(
        history,
        chapter=cutover_chapter + 1,
    )

    expected_events: dict[tuple[int, str, str], dict[str, Any]] = {}
    globally_seen_event_ids: dict[str, tuple[int, str]] = {}
    cutover_admissions: list[dict[str, Any]] = []
    human_decisions = _load_v2_human_decisions(project_root)
    opaque_registry: dict[tuple[str, str], tuple[str, str]] = {}
    lifecycle_targets: dict[tuple[str, str], tuple[str, str]] = {}
    for chapter, path in _commit_paths(project_root):
        if chapter > cutover_chapter:
            continue
        _raw, commit = _read_commit(path, chapter)
        if not _accepted_status(commit):
            continue
        chapter_text = bound_chapter_text_for_commit(project_root, commit)
        if chapter_text is None:
            raise LegacyMigrationError(
                "legacy_commit_binding_invalid", str(chapter), "unreadable_text"
            )
        try:
            linked_records: Mapping[str, Any] = cutover_commit_linked_records(
                commit,
                chapter_text,
            )
        except ValueError as exc:
            code = str(exc)
            if code == "legacy_event_quote_not_in_bound_chapter":
                raise LegacyMigrationError(
                    "legacy_event_quote_not_in_bound_chapter",
                    str(chapter),
                ) from exc
            raise LegacyMigrationError(
                "legacy_linked_fact_evidence_invalid",
                str(chapter),
                str(exc),
            ) from exc
        extraction = commit.get("extraction_result")
        events = (
            extraction.get("accepted_events")
            if isinstance(extraction, Mapping)
            else ()
        )
        for event in events or ():
            if not isinstance(event, Mapping):
                continue
            if str(event.get("event_type") or "") not in _LONG_TERM_EVENT_TYPES:
                continue
            event_id = str(event.get("event_id") or "").strip()
            if not event_id:
                raise LegacyMigrationError(
                    "legacy_long_term_event_missing_id", str(chapter)
                )
            previous = globally_seen_event_ids.get(event_id)
            event_type = str(event.get("event_type") or "")
            if previous is not None:
                raise LegacyMigrationError(
                    "legacy_duplicate_long_term_event_id",
                    event_id,
                    f"first={previous[0]}:{previous[1]}",
                    f"second={chapter}:{event_type}",
                )
            globally_seen_event_ids[event_id] = (chapter, event_type)
            identity = (chapter, event_id, event_type)
            expected_events[identity] = dict(event)
            admission = _exact_human_event_admission(
                commit,
                chapter,
                event,
                human_decisions,
            )
            try:
                quote_binding = bind_legacy_event_quote_span(event, chapter_text)
            except ValueError as exc:
                raise LegacyMigrationError(
                    "legacy_event_quote_not_in_bound_chapter",
                    str(chapter),
                    f"event_id={event_id}",
                ) from exc
            _validate_opaque_event_transition(
                event,
                human_approved=admission is not None,
                registry=opaque_registry,
                lifecycle_targets=lifecycle_targets,
            )
            for linked_timeline in linked_records.get("timeline_events", ()):
                if (
                    not isinstance(linked_timeline, Mapping)
                    or str(linked_timeline.get("source_event_id") or "").strip()
                    != event_id
                ):
                    continue
                linked_id = str(linked_timeline.get("timeline_id") or "").strip()
                linked_event = _payload_first(
                    linked_timeline, "event", "content", "description"
                )
                linked_anchor = _payload_first(
                    linked_timeline, "time_anchor", "time_hint", "time_label"
                )
                _register_opaque_id(
                    opaque_registry,
                    family="timeline_id",
                    opaque_id=linked_id,
                    semantic_key=_opaque_semantic_key(
                        linked_event, linked_anchor
                    ),
                    description=f"{linked_event}|{linked_anchor}",
                )
            try:
                field_admission = validate_legacy_cutover_event(
                    event,
                    linked_records,
                    event_fields_human_approved=admission is not None,
                )
            except LegacyCutoverEvidenceError as exc:
                raise LegacyMigrationError(
                    "legacy_event_requires_human_verification",
                    f"chapter={chapter}",
                    f"event_id={event_id}",
                    f"event_type={event_type}",
                    "unproved_fields=" + ",".join(exc.fields),
                ) from exc
            if admission is None:
                admission = field_admission
            else:
                admission = {
                    **admission,
                    "linked_field_evidence": field_admission,
                }
            cutover_admissions.append(
                event_admission := {
                    "chapter": chapter,
                    "event_id": event_id,
                    "event_type": event_type,
                    "quote_binding": quote_binding,
                    **admission,
                }
            )
            event_admission["admission_digest"] = content_hash(event_admission)

    raw_audit = payload.get("long_term_event_audit") or []
    if not isinstance(raw_audit, list):
        raise LegacyMigrationError("legacy_long_term_event_audit_invalid")
    observed_ids: set[tuple[int, str, str]] = set()
    for row in raw_audit:
        if not isinstance(row, Mapping):
            raise LegacyMigrationError("legacy_long_term_event_audit_invalid")
        try:
            audited_chapter = int(row.get("chapter") or 0)
        except (TypeError, ValueError) as exc:
            raise LegacyMigrationError(
                "legacy_long_term_event_audit_invalid"
            ) from exc
        audited_id = str(row.get("event_id") or "").strip()
        audited_type = str(row.get("event_type") or "").strip()
        target = str(row.get("target") or "").strip()
        targets = row.get("targets")
        source_event_digest = str(row.get("source_event_digest") or "").strip()
        evidence_quote_digest = str(
            row.get("evidence_quote_digest") or ""
        ).strip()
        normalized_facts = row.get("normalized_facts")
        normalized_fact_digests = row.get("normalized_fact_digests")
        identity = (audited_chapter, audited_id, audited_type)
        if (
            audited_chapter <= 0
            or not audited_id
            or audited_type not in _LONG_TERM_EVENT_TYPES
            or not target
            or not isinstance(targets, list)
            or target not in targets
            or not source_event_digest
            or not evidence_quote_digest
            or not isinstance(normalized_facts, list)
            or not normalized_facts
            or not isinstance(normalized_fact_digests, list)
            or len(normalized_facts) != len(normalized_fact_digests)
            or identity in observed_ids
        ):
            raise LegacyMigrationError(
                "legacy_long_term_event_audit_invalid",
                repr(identity),
            )
        source_event = expected_events.get(identity)
        if source_event is None or content_hash(source_event) != source_event_digest:
            raise LegacyMigrationError(
                "legacy_long_term_event_audit_source_mismatch",
                repr(identity),
            )
        source_payload = source_event.get("payload")
        source_payload = source_payload if isinstance(source_payload, Mapping) else {}
        normalized_quote = normalize_author_text(
            source_payload.get("evidence_quote"),
            max_chars=600,
        )
        if content_hash(normalized_quote) != evidence_quote_digest:
            raise LegacyMigrationError(
                "legacy_long_term_event_audit_evidence_mismatch",
                repr(identity),
            )
        for fact, digest in zip(normalized_facts, normalized_fact_digests):
            if (
                not isinstance(fact, Mapping)
                or not isinstance(digest, str)
                or content_hash(fact) != digest
            ):
                raise LegacyMigrationError(
                    "legacy_long_term_event_audit_fact_digest_invalid",
                    repr(identity),
                )
            fact_source = str(
                fact.get("source_event_id") or fact.get("event_id") or ""
            ).strip()
            if fact_source != audited_id:
                raise LegacyMigrationError(
                    "legacy_long_term_event_audit_fact_source_mismatch",
                    repr(identity),
                )
        observed_ids.add(identity)
    expected_event_ids = set(expected_events)
    missing_events = sorted(expected_event_ids - observed_ids)
    if missing_events:
        raise LegacyMigrationError(
            "legacy_long_term_event_not_in_fact_snapshot",
            *(
                f"chapter={chapter}:event_id={event_id}:event_type={event_type}"
                for chapter, event_id, event_type in missing_events
            ),
        )
    unexpected_events = sorted(observed_ids - expected_event_ids)
    if unexpected_events:
        raise LegacyMigrationError(
            "legacy_long_term_event_audit_not_in_prefix",
            *(
                f"chapter={chapter}:event_id={event_id}:event_type={event_type}"
                for chapter, event_id, event_type in unexpected_events
            ),
        )
    payload, identity_receipts = _normalize_cutover_identity_graph(payload)
    payload = _assign_cutover_slots(payload)
    payload["cutover_admissions"] = sorted(
        cutover_admissions,
        key=lambda row: (
            int(row.get("chapter") or 0),
            str(row.get("event_id") or ""),
            str(row.get("event_type") or ""),
        ),
    )
    payload["identity_normalization_receipts"] = identity_receipts
    payload["cutover_fact_admissions"] = _build_cutover_fact_admissions(
        payload,
        payload["cutover_admissions"],
        identity_receipts,
    )
    return {
        "schema_version": LEGACY_SNAPSHOT_SCHEMA,
        "source_schema_version": str(payload.get("schema_version") or ""),
        "cutover_chapter": cutover_chapter,
        "facts": payload,
    }


def _build_material(
    project_root: Path,
    cutover_chapter: int | None,
) -> _LegacyMaterial:
    if cutover_chapter is None:
        discovered = _discover_default_cutover(project_root)
        chapter = discovered
    else:
        chapter = _normalize_cutover(cutover_chapter)
        # Explicit positive K deliberately scopes validation to [1, K].  K=0
        # is only a new-project declaration and must not hide accepted v2 work.
        discovered = _discover_default_cutover(project_root) if chapter == 0 else 0
    if chapter == 0 and discovered > 0:
        raise LegacyMigrationError(
            "legacy_cutover_excludes_accepted_commits", str(discovered)
        )

    # Read/verify the prefix, derive its fact-only snapshot, then read/verify
    # the prefix again.  This catches ordinary concurrent v2 writes instead of
    # binding commit references from one revision to facts from another.
    first_refs = _accepted_commit_refs(project_root, chapter)
    first_snapshot = _fact_snapshot(project_root, chapter)
    second_refs = _accepted_commit_refs(project_root, chapter)
    second_snapshot = _fact_snapshot(project_root, chapter)
    if first_refs != second_refs or first_snapshot != second_snapshot:
        raise LegacyMigrationError("legacy_sources_changed_during_migration")

    source = "v2_accepted_commits" if chapter > 0 else "new_project"
    digest = content_hash(second_snapshot)
    return _LegacyMaterial(
        source=source,
        cutover_chapter=chapter,
        commits=second_refs,
        snapshot=second_snapshot,
        snapshot_sha256=digest,
    )


def _genesis_manifest(
    repository: CanonV3Repository,
    head_hash: str,
) -> dict[str, Any]:
    seen: set[str] = set()
    current_hash = head_hash
    while True:
        if current_hash in seen:
            raise CanonIntegrityError("canon_v3_manifest_parent_cycle")
        seen.add(current_hash)
        manifest = repository.read_manifest(
            current_hash,
            validate_references=True,
        )
        generation = int(manifest.get("generation") or 0)
        if generation == 0:
            return manifest
        parent = manifest.get("parent_head_hash")
        if not isinstance(parent, str) or not parent:
            raise CanonIntegrityError("canon_v3_manifest_missing_parent")
        current_hash = parent


def _status_payload(
    *,
    state: str,
    migration_required: bool,
    reason_codes: list[str],
    head_hash: str | None,
    cutover_chapter: int | None,
    source: str | None,
    projection_fresh: bool,
    details: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": LEGACY_STATUS_SCHEMA,
        "state": state,
        "migration_required": migration_required,
        "reason_codes": sorted(set(reason_codes)),
        "details": sorted(set(details or [])),
        "head_hash": head_hash,
        "cutover_chapter": cutover_chapter,
        "source": source,
        "projection_fresh": projection_fresh,
    }


def legacy_prefix_status(project_root: str | Path) -> dict[str, Any]:
    """Check whether CURRENT still binds the exact imported v2 prefix.

    A manuscript edit at or before the cutover invalidates its old chapter
    binding, so this function returns ``state=stale`` and
    ``migration_required=true`` before any later v3 write is considered safe.
    """
    root = _root(project_root)
    repository = CanonV3Repository(root)
    try:
        head = repository.current_head(validate=True)
    except CanonRepositoryError as exc:
        return _status_payload(
            state="stale",
            migration_required=True,
            reason_codes=["v3_current_invalid"],
            details=[str(exc)],
            head_hash=None,
            cutover_chapter=None,
            source=None,
            projection_fresh=False,
        )
    if head is None:
        return _status_payload(
            state="migration_required",
            migration_required=True,
            reason_codes=["v3_not_initialized"],
            head_hash=None,
            cutover_chapter=None,
            source=None,
            projection_fresh=False,
        )

    try:
        genesis = _genesis_manifest(repository, head)
    except CanonRepositoryError as exc:
        return _status_payload(
            state="stale",
            migration_required=True,
            reason_codes=["v3_genesis_invalid"],
            details=[str(exc)],
            head_hash=head,
            cutover_chapter=None,
            source=None,
            projection_fresh=False,
        )
    metadata = genesis.get("genesis_metadata")
    if isinstance(metadata, dict) and metadata.get("schema_version") == (
        LEGACY_GENESIS_SCHEMA_V1
    ):
        return _status_payload(
            state="stale",
            migration_required=True,
            reason_codes=["legacy_genesis_needs_recertification"],
            head_hash=head,
            cutover_chapter=None,
            source=str(metadata.get("source") or "") or None,
            projection_fresh=projection_is_fresh(root),
        )
    if not isinstance(metadata, dict) or metadata.get("schema_version") != LEGACY_GENESIS_SCHEMA:
        return _status_payload(
            state="stale",
            migration_required=True,
            reason_codes=["legacy_genesis_metadata_missing"],
            head_hash=head,
            cutover_chapter=None,
            source=None,
            projection_fresh=projection_is_fresh(root),
        )

    source = str(metadata.get("source") or "")
    raw_cutover = metadata.get("cutover_chapter")
    reasons: list[str] = []
    details: list[str] = []
    try:
        cutover = _normalize_cutover(raw_cutover)
    except LegacyMigrationError as exc:
        return _status_payload(
            state="stale",
            migration_required=True,
            reason_codes=["legacy_genesis_cutover_invalid"],
            details=[str(exc)],
            head_hash=head,
            cutover_chapter=None,
            source=source or None,
            projection_fresh=projection_is_fresh(root),
        )

    stored_commits = metadata.get("v2_commits")
    stored_snapshot = metadata.get("legacy_snapshot")
    stored_digest = str(metadata.get("legacy_snapshot_sha256") or "")
    if not isinstance(stored_commits, list):
        reasons.append("legacy_genesis_commits_invalid")
    if not isinstance(stored_snapshot, dict):
        reasons.append("legacy_genesis_snapshot_invalid")
    elif content_hash(stored_snapshot) != stored_digest:
        reasons.append("legacy_genesis_snapshot_digest_invalid")
    if source not in {"new_project", "v2_accepted_commits"}:
        reasons.append("legacy_genesis_source_invalid")
    if source == "new_project" and (cutover != 0 or stored_commits):
        reasons.append("new_project_genesis_invalid")
    if source == "v2_accepted_commits" and (cutover <= 0 or not stored_commits):
        reasons.append("legacy_genesis_prefix_empty")

    current_material = None
    if source == "v2_accepted_commits":
        try:
            current_material = _build_material(root, cutover)
        except LegacyMigrationError as exc:
            reasons.append(exc.code)
            details.extend(exc.details)

    if current_material is not None:
        if current_material.source != source:
            reasons.append("legacy_source_changed")
        if isinstance(stored_commits, list) and list(current_material.commits) != stored_commits:
            reasons.append("legacy_commit_refs_changed")
        if isinstance(stored_snapshot, dict) and current_material.snapshot != stored_snapshot:
            reasons.append("legacy_snapshot_changed")
        if current_material.snapshot_sha256 != stored_digest:
            reasons.append("legacy_snapshot_digest_changed")

    fresh_projection = projection_is_fresh(root)
    if not fresh_projection:
        reasons.append("v3_projection_stale")
    stale = bool(reasons)
    return _status_payload(
        state="stale" if stale else "current",
        migration_required=stale,
        reason_codes=reasons,
        details=details,
        head_hash=head,
        cutover_chapter=cutover,
        source=source or None,
        projection_fresh=fresh_projection,
    )


def _invariant_for_reason(code: str) -> str:
    lowered = str(code or "").lower()
    if any(
        marker in lowered
        for marker in (
            "evidence",
            "quote",
            "binding",
            "admission",
            "linked",
            "normalized_fact",
        )
    ):
        return "evidence"
    if any(marker in lowered for marker in ("opaque", "terminal", "target")):
        return "target"
    if any(
        marker in lowered
        for marker in ("identity", "namespace", "alias", "custody")
    ):
        return "identity"
    return "source_prefix"


def _manifest_chain(
    repository: CanonV3Repository,
    head_hash: str,
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Return the exact active ancestry from genesis to ``head_hash``."""

    reverse: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    cursor: str | None = head_hash
    while cursor is not None:
        if cursor in seen:
            raise LegacyMigrationError("legacy_recertification_manifest_cycle")
        seen.add(cursor)
        try:
            manifest = repository.read_manifest(cursor, validate_references=True)
        except CanonRepositoryError as exc:
            raise LegacyMigrationError(
                "legacy_recertification_manifest_invalid", str(exc)
            ) from exc
        reverse.append((cursor, manifest))
        parent = manifest.get("parent_head_hash")
        cursor = str(parent) if parent else None
    return tuple(reversed(reverse))


def _active_suffix_materials(
    repository: CanonV3Repository,
    head_hash: str | None,
    *,
    cutover_chapter: int,
) -> tuple[dict[str, Any], ...]:
    if head_hash is None:
        return ()
    manifest = repository.read_manifest(head_hash, validate_references=True)
    active_axioms = repository._author_axiom_manifest_entries(  # noqa: SLF001
        manifest
    )
    if active_axioms:
        # A v1 genesis forces the public workflow into recertification before
        # AuthorAxiomChannel.prepare can open a transaction. Therefore this
        # mixed state is not reachable through the supported product flow; it
        # indicates hand-written/corrupt history. Copying its commits would
        # silently bypass their old parent/membership proof, so fail closed.
        raise LegacyMigrationError(
            "legacy_recertification_active_author_axioms_require_rebind",
            *(
                str(entry.get("commit_hash") or "")
                for entry in active_axioms
            ),
        )
    result: list[dict[str, Any]] = []
    for entry in manifest.get("chapters") or ():
        chapter = int(entry.get("chapter") or 0)
        if chapter <= cutover_chapter:
            raise LegacyMigrationError(
                "legacy_recertification_suffix_overlaps_prefix", str(chapter)
            )
        commit_hash = str(entry.get("commit_hash") or "")
        commit = repository.read_commit(commit_hash)
        result.append(
            {
                "chapter": chapter,
                "revision": int(entry.get("revision") or 0),
                "commit_hash": commit_hash,
                "transaction_hash": str(commit.get("transaction_hash") or ""),
                "decision_hashes": list(commit.get("decision_hashes") or ()),
                "lineage_decision_hashes": list(
                    commit.get("lineage_decision_hashes") or ()
                ),
                "canon_effects": copy.deepcopy(commit.get("canon_effects") or []),
            }
        )
    chapters = [row["chapter"] for row in result]
    expected = list(
        range(cutover_chapter + 1, cutover_chapter + len(chapters) + 1)
    )
    if chapters and chapters != expected:
        raise LegacyMigrationError(
            "legacy_recertification_suffix_not_contiguous",
            ",".join(str(value) for value in chapters),
        )
    return tuple(result)


def _validate_and_enrich_suffix_materials(
    project_root: Path,
    repository: CanonV3Repository,
    suffix_materials: Iterable[Mapping[str, Any]],
    decision_materials: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Prove old active effects agree with review and negative lineage."""

    from ..chapter_content_binding import require_chapter_binding
    from .evidence import candidate_digest, semantic_claim_digest
    from .service import CanonV3Service, PreparedEnvelope, StagingPointer
    from .source_verifier import verify_all_candidate_sources

    service = CanonV3Service(project_root)
    service.repository = repository
    negative_by_chapter: dict[int, list[Mapping[str, Any]]] = {}
    for material in decision_materials:
        if str(material.get("family") or "") != "semantic_negative_lineage":
            continue
        negative_by_chapter.setdefault(
            int(material.get("chapter") or 0), []
        ).append(material)

    enriched: list[dict[str, Any]] = []
    for raw in suffix_materials:
        suffix = copy.deepcopy(dict(raw))
        chapter = int(suffix.get("chapter") or 0)
        transaction_hash = str(suffix.get("transaction_hash") or "")
        try:
            transaction = repository.read_transaction(transaction_hash)
            if transaction.get("schema_version") == (
                LEGACY_RECERTIFIED_SUFFIX_TRANSACTION_SCHEMA
            ):
                raise ValueError("nested_recertified_wrapper_forbidden")
            envelope = PreparedEnvelope.model_validate(transaction)
            require_chapter_binding(
                project_root, chapter, envelope.chapter_binding
            )
            verify_all_candidate_sources(
                project_root,
                envelope.chapter_binding,
                envelope.candidates,
                active_author_axiom_source_keys=(
                    service._active_author_axiom_source_keys(  # noqa: SLF001
                        envelope.prepared_transaction.parent_head
                    )
                ),
            )
            pointer = StagingPointer(
                transaction_hash=transaction_hash,
                decision_hashes=tuple(suffix.get("decision_hashes") or ()),
                lineage_decision_hashes=tuple(
                    suffix.get("lineage_decision_hashes") or ()
                ),
            )
            reduction = service._validated_reduction(pointer, envelope)  # noqa: SLF001
        except Exception as exc:
            raise LegacyMigrationError(
                "legacy_recertification_suffix_review_invalid",
                f"chapter={chapter}",
                str(exc),
            ) from exc
        active_digests = {
            record.candidate_digest for record in reduction.active_candidates
        }
        candidates = {
            candidate_digest(candidate): candidate
            for candidate in envelope.candidates
        }
        expected_effects = [
            effect.model_dump(mode="json")
            for effect in envelope.prepared_transaction.effects
            if effect.candidate_digest in active_digests
        ]
        actual_effects = list(suffix.get("canon_effects") or ())
        if actual_effects != expected_effects:
            raise LegacyMigrationError(
                "legacy_recertification_suffix_effects_review_mismatch",
                f"chapter={chapter}",
            )

        negative_candidate_digests = {
            str(material.get("candidate_digest") or "")
            for material in negative_by_chapter.get(chapter, ())
        }
        negative_semantic_digests = {
            str(material.get("semantic_claim_digest") or "")
            for material in negative_by_chapter.get(chapter, ())
        }
        active_semantic_digests = {
            semantic_claim_digest(candidates[digest])
            for digest in active_digests
            if digest in candidates
        }
        if (
            active_digests & negative_candidate_digests
            or active_semantic_digests & negative_semantic_digests
        ):
            raise LegacyMigrationError(
                "legacy_recertification_negative_lineage_reactivated",
                f"chapter={chapter}",
            )

        effect_materials: list[dict[str, Any]] = []
        for effect in actual_effects:
            digest = str(effect.get("candidate_digest") or "")
            candidate = candidates.get(digest)
            if candidate is None:
                raise LegacyMigrationError(
                    "legacy_recertification_suffix_candidate_missing",
                    f"chapter={chapter}",
                    digest,
                )
            effect_materials.append(
                {
                    "chapter": chapter,
                    "commit_hash": str(suffix.get("commit_hash") or ""),
                    "transaction_hash": transaction_hash,
                    "candidate_digest": digest,
                    "semantic_claim_digest": semantic_claim_digest(candidate),
                    "candidate": candidate.model_dump(mode="json"),
                    "canon_effect": copy.deepcopy(effect),
                }
            )
        suffix["active_candidate_materials"] = sorted(
            effect_materials,
            key=lambda item: (
                item["candidate_digest"],
                str(item["canon_effect"].get("effect_id") or ""),
            ),
        )
        suffix["review_reduction_digest"] = content_hash(
            {
                "active_candidate_digests": sorted(active_digests),
                "applied_decision_hashes": list(
                    reduction.applied_decision_hashes
                ),
                "omitted_candidate_digests": list(
                    reduction.omitted_candidate_digests
                ),
                "canon_effects": actual_effects,
            }
        )
        enriched.append(suffix)
    return tuple(enriched)


def _decision_review_material(
    repository: CanonV3Repository,
    decision_hash: str,
) -> tuple[ReviewAction, int, dict[str, Any]]:
    """Strictly resolve one old decision and its semantic candidate."""

    try:
        wrapper = repository.read_decision(decision_hash)
        decision = decision_from_dict(wrapper.get("decision"))
        transaction_hash = str(wrapper.get("transaction_hash") or "")
        transaction = repository.read_transaction(transaction_hash)
        from .evidence import (
            candidate_digest,
            lineage_key,
            semantic_claim_digest,
        )
        from .service import PreparedEnvelope

        envelope = PreparedEnvelope.model_validate(transaction)
        candidate = next(
            (
                item
                for item in envelope.candidates
                if candidate_digest(item) == decision.context.candidate_digest
            ),
            None,
        )
        if candidate is None:
            raise ValueError("original_candidate_missing")
        semantic_digest = semantic_claim_digest(candidate)
        semantic_lineage_key = lineage_key(
            decision.context.chapter_digest,
            candidate,
        )
        stored_lineage_key = str(wrapper.get("lineage_key") or "")
        if stored_lineage_key and stored_lineage_key != semantic_lineage_key:
            raise ValueError("stored_lineage_key_mismatch")
    except Exception as exc:
        raise LegacyMigrationError(
            "legacy_recertification_decision_invalid",
            decision_hash,
            str(exc),
        ) from exc
    material = {
        "decision_hash": decision_hash,
        "decision_envelope": copy.deepcopy(wrapper),
        "transaction_hash": transaction_hash,
        "transaction_content_sha256": content_hash(transaction),
        "chapter": int(envelope.chapter),
        "chapter_digest": decision.context.chapter_digest,
        "candidate_digest": decision.context.candidate_digest,
        "candidate": candidate.model_dump(mode="json"),
        "semantic_claim_digest": semantic_digest,
        "semantic_lineage_key": semantic_lineage_key,
        "action": decision.action.value,
        "revision": int(decision.revision),
        "supersedes": decision.supersedes or None,
    }
    return decision.action, int(envelope.chapter), material


def _historical_decision_materials(
    repository: CanonV3Repository,
    head_hash: str | None,
) -> tuple[tuple[dict[str, Any], ...], dict[int, tuple[str, ...]]]:
    """Return reviewable decision heads and immutable negative tombstones."""

    if head_hash is None:
        return (), {}
    object_hashes: set[str] = set()
    explicit_lineage: set[str] = set()
    for _manifest_hash, manifest in _manifest_chain(repository, head_hash):
        for entry in manifest.get("chapters") or ():
            commit = repository.read_commit(str(entry.get("commit_hash") or ""))
            object_hashes.update(str(value) for value in commit.get("decision_hashes") or ())
            lineage = {
                str(value)
                for value in commit.get("lineage_decision_hashes") or ()
            }
            explicit_lineage.update(lineage)
            object_hashes.update(lineage)

    heads: dict[tuple[str, str], tuple[int, str, ReviewAction, int, dict[str, Any]]] = {}
    resolved_by_hash: dict[
        str, tuple[ReviewAction, int, dict[str, Any]]
    ] = {}
    for object_hash in sorted(object_hashes):
        action, chapter, material = _decision_review_material(
            repository, object_hash
        )
        resolved_by_hash[object_hash] = (action, chapter, material)
        wrapper = material["decision_envelope"]
        decision = decision_from_dict(wrapper.get("decision"))
        key = (material["transaction_hash"], decision.case_key)
        previous = heads.get(key)
        row = (decision.revision, object_hash, action, chapter, material)
        if previous is None or decision.revision > previous[0]:
            heads[key] = row
        elif decision.revision == previous[0] and object_hash != previous[1]:
            raise LegacyMigrationError(
                "legacy_recertification_decision_head_fork",
                material["transaction_hash"],
                decision.case_key,
            )

    review_materials: list[dict[str, Any]] = []
    negative_by_chapter: dict[int, set[str]] = {}
    for _key, (_revision, object_hash, action, chapter, material) in sorted(
        heads.items()
    ):
        if action in _POSITIVE_REVIEW_ACTIONS:
            review_materials.append(
                {"family": "suffix_positive_decision", **material}
            )
    negative_hashes = {
        object_hash
        for _key, (_revision, object_hash, action, _chapter, _material) in heads.items()
        if action in _NEGATIVE_REVIEW_ACTIONS
    }
    negative_hashes.update(explicit_lineage)
    for object_hash in sorted(negative_hashes):
        action, chapter, material = resolved_by_hash[object_hash]
        if action not in _NEGATIVE_REVIEW_ACTIONS:
            raise LegacyMigrationError(
                "legacy_recertification_lineage_not_negative", object_hash
            )
        negative_by_chapter.setdefault(chapter, set()).add(object_hash)
        review_materials.append(
            {"family": "semantic_negative_lineage", **material}
        )
    return (
        tuple(
            sorted(
                review_materials,
                key=lambda row: (
                    str(row.get("family") or ""),
                    int(row.get("chapter") or 0),
                    str(row.get("decision_hash") or ""),
                ),
            )
        ),
        {
            chapter: tuple(sorted(hashes))
            for chapter, hashes in sorted(negative_by_chapter.items())
        },
    )


def _recertification_case(
    *,
    expected_current_head: str,
    family: str,
    target: Mapping[str, Any],
    material: Mapping[str, Any],
) -> dict[str, Any]:
    target_digest = content_hash(dict(target))
    review_material = {
        "schema_version": LEGACY_RECERTIFICATION_MATERIAL_SCHEMA,
        "expected_current_head": expected_current_head,
        "family": family,
        "target": copy.deepcopy(dict(target)),
        "material": copy.deepcopy(dict(material)),
    }
    material_digest = content_hash(review_material)
    case_key = content_hash(
        {
            "schema_version": LEGACY_RECERTIFICATION_CASE_SCHEMA,
            "expected_current_head": expected_current_head,
            "family": family,
            "target_digest": target_digest,
            "material_digest": material_digest,
        }
    )
    return {
        "schema_version": LEGACY_RECERTIFICATION_CASE_SCHEMA,
        "case_key": case_key,
        "kind": "checkpoint",
        "level": "human_required",
        "family": family,
        "target_digest": target_digest,
        "material_digest": material_digest,
        "allowed_actions": ["confirm"],
        "review_material": review_material,
    }


def _recertification_cases(
    *,
    expected_current_head: str,
    material: _LegacyMaterial,
    suffix_materials: Iterable[Mapping[str, Any]],
    decision_materials: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Expose every promoted fact class as an independently bound checkpoint."""

    facts = material.snapshot.get("facts")
    facts = facts if isinstance(facts, Mapping) else {}
    cases: list[dict[str, Any]] = []

    for admission in facts.get("cutover_admissions") or ():
        if not isinstance(admission, Mapping):
            continue
        identity = {
            "chapter": int(admission.get("chapter") or 0),
            "event_id": str(admission.get("event_id") or ""),
            "event_type": str(admission.get("event_type") or ""),
        }
        cases.append(
            _recertification_case(
                expected_current_head=expected_current_head,
                family="positive_event_admission",
                target=identity,
                material=admission,
            )
        )
        if identity["event_type"] in _TARGET_EVENT_TYPES:
            normalized = [
                copy.deepcopy(row)
                for audit in facts.get("long_term_event_audit") or ()
                if isinstance(audit, Mapping)
                and int(audit.get("chapter") or 0) == identity["chapter"]
                and str(audit.get("event_id") or "") == identity["event_id"]
                for row in audit.get("normalized_facts") or ()
                if isinstance(row, Mapping)
            ]
            cases.append(
                _recertification_case(
                    expected_current_head=expected_current_head,
                    family="target_resolution",
                    target=identity,
                    material={
                        "event_admission": copy.deepcopy(dict(admission)),
                        "normalized_facts": normalized,
                    },
                )
            )

    for admission in facts.get("cutover_fact_admissions") or ():
        if not isinstance(admission, Mapping):
            continue
        cases.append(
            _recertification_case(
                expected_current_head=expected_current_head,
                family="positive_fact_admission",
                target={
                    "fact_content_sha256": str(
                        admission.get("fact_content_sha256") or ""
                    )
                },
                material=admission,
            )
        )

    for receipt in facts.get("identity_normalization_receipts") or ():
        if not isinstance(receipt, Mapping):
            continue
        cases.append(
            _recertification_case(
                expected_current_head=expected_current_head,
                family="identity_resolution",
                target={
                    "namespace": str(receipt.get("namespace") or ""),
                    "legacy_entity_id": str(
                        receipt.get("legacy_entity_id") or ""
                    ),
                    "canonical_entity_id": str(
                        receipt.get("canonical_entity_id") or ""
                    ),
                },
                material=receipt,
            )
        )

    for suffix in suffix_materials:
        cases.append(
            _recertification_case(
                expected_current_head=expected_current_head,
                family="suffix_commit_envelope",
                target={
                    "chapter": int(suffix.get("chapter") or 0),
                    "commit_hash": str(suffix.get("commit_hash") or ""),
                },
                material={
                    key: copy.deepcopy(value)
                    for key, value in suffix.items()
                    if key
                    not in {"canon_effects", "active_candidate_materials"}
                },
            )
        )
        for candidate_material in suffix.get("active_candidate_materials") or ():
            if not isinstance(candidate_material, Mapping):
                continue
            effect = candidate_material.get("canon_effect")
            effect = effect if isinstance(effect, Mapping) else {}
            cases.append(
                _recertification_case(
                    expected_current_head=expected_current_head,
                    family="suffix_fact_carry_forward",
                    target={
                        "chapter": int(candidate_material.get("chapter") or 0),
                        "commit_hash": str(
                            candidate_material.get("commit_hash") or ""
                        ),
                        "candidate_digest": str(
                            candidate_material.get("candidate_digest") or ""
                        ),
                        "semantic_claim_digest": str(
                            candidate_material.get("semantic_claim_digest") or ""
                        ),
                        "fact_key": str(effect.get("fact_key") or ""),
                        "effect_id": str(effect.get("effect_id") or ""),
                    },
                    material=candidate_material,
                )
            )

    for decision in decision_materials:
        family = str(decision.get("family") or "legacy_decision")
        cases.append(
            _recertification_case(
                expected_current_head=expected_current_head,
                family=family,
                target={
                    "decision_hash": str(decision.get("decision_hash") or ""),
                    "semantic_claim_digest": str(
                        decision.get("semantic_claim_digest") or ""
                    ),
                    "semantic_lineage_key": str(
                        decision.get("semantic_lineage_key") or ""
                    ),
                },
                material=decision,
            )
        )

    by_key: dict[str, dict[str, Any]] = {}
    for case in cases:
        key = str(case["case_key"])
        previous = by_key.get(key)
        if previous is not None and previous != case:
            raise LegacyMigrationError(
                "legacy_recertification_case_key_collision", key
            )
        by_key[key] = case
    return tuple(by_key[key] for key in sorted(by_key))


def _authoritative_staging_kinds(project_root: Path) -> tuple[str, ...]:
    try:
        from .staging_authority import authoritative_staging_kinds

        return tuple(authoritative_staging_kinds(project_root))
    except ImportError:
        paths = {
            "chapter": project_root / ".story-system" / "v3" / "STAGING.json",
            "author_axiom": (
                project_root
                / ".story-system"
                / "v3"
                / "AUTHOR_AXIOM_STAGING.json"
            ),
        }
        return tuple(key for key, path in paths.items() if path.is_file())


def _detached_recertification_plan(
    *,
    expected_current_head: str,
    material: _LegacyMaterial,
    suffix_materials: tuple[dict[str, Any], ...],
    negative_lineage_by_chapter: Mapping[int, tuple[str, ...]],
    cases: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    genesis_metadata = material.genesis_metadata()
    plan = {
        "schema_version": "canon-v3/legacy-recertification-detached-plan/v1",
        "expected_current_head": expected_current_head,
        "source": material.source,
        "cutover_chapter": material.cutover_chapter,
        "legacy_snapshot_sha256": material.snapshot_sha256,
        "commit_ref_digests": [
            content_hash(commit_ref) for commit_ref in material.commits
        ],
        "genesis_metadata_sha256": content_hash(genesis_metadata),
        "suffix_commit_digests": [
            content_hash(dict(item)) for item in suffix_materials
        ],
        "semantic_negative_lineage": {
            str(chapter): list(hashes)
            for chapter, hashes in sorted(negative_lineage_by_chapter.items())
        },
        "review_case_digests": [content_hash(case) for case in cases],
        "review_cases_digest": content_hash(list(cases)),
    }
    plan["detached_plan_digest"] = content_hash(plan)
    plan["publish_token"] = content_hash(
        {
            "schema_version": "canon-v3/legacy-recertification-publish-token/v1",
            "expected_current_head": expected_current_head,
            "detached_plan_digest": plan["detached_plan_digest"],
            "review_cases_digest": plan["review_cases_digest"],
        }
    )
    return plan


@dataclass(frozen=True)
class _RecertificationBundle:
    material: _LegacyMaterial
    suffix_materials: tuple[dict[str, Any], ...]
    decision_materials: tuple[dict[str, Any], ...]
    negative_lineage_by_chapter: dict[int, tuple[str, ...]]
    cases: tuple[dict[str, Any], ...]
    plan: dict[str, Any]


def _recertification_bundle_bytes(bundle: _RecertificationBundle) -> bytes:
    return canonical_json_bytes(
        {
            "genesis_metadata": bundle.material.genesis_metadata(),
            "suffix_materials": list(bundle.suffix_materials),
            "decision_materials": list(bundle.decision_materials),
            "negative_lineage_by_chapter": {
                str(chapter): list(hashes)
                for chapter, hashes in sorted(
                    bundle.negative_lineage_by_chapter.items()
                )
            },
            "cases": list(bundle.cases),
            "plan": bundle.plan,
        }
    )


def _build_recertification_bundle(
    project_root: Path,
    repository: CanonV3Repository,
    *,
    expected_current_head: str,
    cutover_chapter: int,
) -> _RecertificationBundle:
    """Build one byte-comparable source/review/publication snapshot."""

    material = _build_material(project_root, cutover_chapter)
    decision_materials, negative_lineage = _historical_decision_materials(
        repository, expected_current_head
    )
    suffix = _active_suffix_materials(
        repository,
        expected_current_head,
        cutover_chapter=material.cutover_chapter,
    )
    suffix = _validate_and_enrich_suffix_materials(
        project_root,
        repository,
        suffix,
        decision_materials,
    )
    cases = _recertification_cases(
        expected_current_head=expected_current_head,
        material=material,
        suffix_materials=suffix,
        decision_materials=decision_materials,
    )
    plan = _detached_recertification_plan(
        expected_current_head=expected_current_head,
        material=material,
        suffix_materials=suffix,
        negative_lineage_by_chapter=negative_lineage,
        cases=cases,
    )
    return _RecertificationBundle(
        material=material,
        suffix_materials=suffix,
        decision_materials=decision_materials,
        negative_lineage_by_chapter=negative_lineage,
        cases=cases,
        plan=plan,
    )


def audit_cutover(
    project_root: str | Path,
    cutover_chapter: int | None = None,
) -> dict[str, Any]:
    """Read-only proof report for a prospective v2 cutover.

    The report is deterministic and safe for CLI/status callers: it does not
    create content-addressed objects, rebuild a projection, or write CURRENT.
    """

    root = _root(project_root)
    repository = CanonV3Repository(root)
    head: str | None = None
    genesis_schema: str | None = None
    genesis_metadata: dict[str, Any] | None = None
    head_error: str | None = None
    try:
        head = repository.current_head(validate=True)
        if head is not None:
            genesis = _genesis_manifest(repository, head)
            metadata = genesis.get("genesis_metadata")
            if isinstance(metadata, Mapping):
                genesis_metadata = copy.deepcopy(dict(metadata))
                genesis_schema = str(metadata.get("schema_version") or "") or None
    except CanonRepositoryError as exc:
        head_error = str(exc)

    requires_recertification = genesis_schema == LEGACY_GENESIS_SCHEMA_V1
    reasons: list[str] = []
    details: list[str] = []
    material: _LegacyMaterial | None = None
    recertification_bundle: _RecertificationBundle | None = None
    if head_error:
        reasons.append("v3_current_invalid")
        details.append(head_error)
    else:
        effective_cutover = cutover_chapter
        if requires_recertification and genesis_metadata is not None:
            old_cutover = _normalize_cutover(
                genesis_metadata.get("cutover_chapter")
            )
            if effective_cutover is None:
                effective_cutover = old_cutover
            elif _normalize_cutover(effective_cutover) != old_cutover:
                reasons.append("legacy_recertification_cutover_mismatch")
                details.extend(
                    [f"expected={old_cutover}", f"actual={effective_cutover}"]
                )
        try:
            if not reasons:
                if requires_recertification and head is not None:
                    recertification_bundle = _build_recertification_bundle(
                        root,
                        repository,
                        expected_current_head=head,
                        cutover_chapter=_normalize_cutover(effective_cutover),
                    )
                    material = recertification_bundle.material
                else:
                    material = _build_material(root, effective_cutover)
        except LegacyMigrationError as exc:
            reasons.append(exc.code)
            details.extend(exc.details)
    if requires_recertification:
        reasons.append("legacy_genesis_needs_recertification")

    invariant_status = {
        "source_prefix": "pass" if material is not None else "unknown",
        "evidence": "pass" if material is not None else "unknown",
        "target": "pass" if material is not None else "unknown",
        "identity": "pass" if material is not None else "unknown",
    }
    detached_plan: dict[str, Any] | None = None
    review_cases: tuple[dict[str, Any], ...] = ()
    publish_token: str | None = None
    if material is not None:
        if requires_recertification and head is not None:
            assert recertification_bundle is not None
            review_cases = recertification_bundle.cases
            detached_plan = recertification_bundle.plan
            publish_token = str(detached_plan["publish_token"])
        else:
            target_metadata = material.genesis_metadata()
            detached_plan = {
                "expected_current_head": head,
                "source": material.source,
                "cutover_chapter": material.cutover_chapter,
                "legacy_snapshot_sha256": material.snapshot_sha256,
                "commit_ref_digests": [
                    content_hash(commit_ref) for commit_ref in material.commits
                ],
                "genesis_metadata_sha256": content_hash(target_metadata),
            }
            detached_plan["detached_plan_digest"] = content_hash(detached_plan)

    staging_kinds = _authoritative_staging_kinds(root)
    if requires_recertification and staging_kinds:
        reasons.append("authoritative_staging_conflicts_with_recertification")
        details.append("staging=" + ",".join(staging_kinds))

    for reason in reasons:
        if reason == "legacy_genesis_needs_recertification":
            continue
        invariant_status[_invariant_for_reason(reason)] = "blocked"

    blocked = any(
        reason != "legacy_genesis_needs_recertification" for reason in reasons
    )
    state = (
        "blocked"
        if blocked
        else "needs_recertification"
        if requires_recertification
        else "ready"
    )
    return {
        "schema_version": LEGACY_CUTOVER_AUDIT_SCHEMA,
        "state": state,
        "read_only": True,
        "writes_performed": False,
        "can_publish": state in {"ready", "needs_recertification"},
        "requires_recertification": requires_recertification,
        "current_head": head,
        "current_genesis_schema": genesis_schema,
        "reason_codes": sorted(set(reasons)),
        "details": sorted(set(details)),
        "invariants": invariant_status,
        "authoritative_transaction": (
            "legacy_recertification"
            if requires_recertification
            else "legacy_cutover"
        ),
        "conflicting_staging_kinds": list(staging_kinds),
        "cases": [copy.deepcopy(case) for case in review_cases],
        "required_case_count": len(review_cases),
        "detached_plan": detached_plan,
        "detached_plan_digest": (
            str(detached_plan.get("detached_plan_digest") or "")
            if detached_plan is not None
            else None
        ),
        "publish_token": publish_token,
    }


def repair_cutover_dry_run(
    project_root: str | Path,
    cutover_chapter: int | None = None,
) -> dict[str, Any]:
    """Return a detached recertification plan without switching CURRENT."""

    report = audit_cutover(project_root, cutover_chapter)
    return {
        "schema_version": LEGACY_REPAIR_DRY_RUN_SCHEMA,
        "state": report["state"],
        "read_only": True,
        "writes_performed": False,
        "would_switch_current": False,
        "current_head": report["current_head"],
        "requires_recertification": report["requires_recertification"],
        "reason_codes": list(report["reason_codes"]),
        "details": list(report["details"]),
        "invariants": dict(report["invariants"]),
        "authoritative_transaction": report["authoritative_transaction"],
        "conflicting_staging_kinds": list(report["conflicting_staging_kinds"]),
        "cases": copy.deepcopy(report["cases"]),
        "required_case_count": int(report["required_case_count"]),
        "detached_plan": copy.deepcopy(report["detached_plan"]),
        "detached_plan_digest": report["detached_plan_digest"],
        "publish_token": report["publish_token"],
        "audit_digest": content_hash(report),
    }


def _require_sha256(value: Any, *, code: str) -> str:
    normalized = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise LegacyMigrationError(code)
    return normalized


def _parse_recertification_publish_request(
    raw_request: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], str]:
    if not isinstance(raw_request, Mapping):
        raise LegacyMigrationError("legacy_recertification_request_invalid")
    expected_fields = {
        "schema_version",
        "expected_current_head",
        "detached_plan_digest",
        "publish_token",
        "decisions",
    }
    if set(raw_request) != expected_fields:
        raise LegacyMigrationError(
            "legacy_recertification_request_fields_invalid",
            ",".join(sorted(set(raw_request) ^ expected_fields)),
        )
    if raw_request.get("schema_version") != (
        LEGACY_RECERTIFICATION_PUBLISH_REQUEST_SCHEMA
    ):
        raise LegacyMigrationError("legacy_recertification_request_schema_invalid")
    parsed = {
        "schema_version": LEGACY_RECERTIFICATION_PUBLISH_REQUEST_SCHEMA,
        "expected_current_head": _require_sha256(
            raw_request.get("expected_current_head"),
            code="legacy_recertification_expected_head_invalid",
        ),
        "detached_plan_digest": _require_sha256(
            raw_request.get("detached_plan_digest"),
            code="legacy_recertification_plan_digest_invalid",
        ),
        "publish_token": _require_sha256(
            raw_request.get("publish_token"),
            code="legacy_recertification_publish_token_invalid",
        ),
    }
    raw_decisions = raw_request.get("decisions")
    if not isinstance(raw_decisions, list):
        raise LegacyMigrationError("legacy_recertification_decisions_invalid")
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    decision_fields = {
        "schema_version",
        "case_key",
        "target_digest",
        "material_digest",
        "action",
    }
    for index, raw in enumerate(raw_decisions):
        if not isinstance(raw, Mapping) or set(raw) != decision_fields:
            raise LegacyMigrationError(
                "legacy_recertification_decision_fields_invalid", str(index)
            )
        if raw.get("schema_version") != LEGACY_RECERTIFICATION_DECISION_SCHEMA:
            raise LegacyMigrationError(
                "legacy_recertification_decision_schema_invalid", str(index)
            )
        decision = {
            "schema_version": LEGACY_RECERTIFICATION_DECISION_SCHEMA,
            "case_key": _require_sha256(
                raw.get("case_key"),
                code="legacy_recertification_case_key_invalid",
            ),
            "target_digest": _require_sha256(
                raw.get("target_digest"),
                code="legacy_recertification_target_digest_invalid",
            ),
            "material_digest": _require_sha256(
                raw.get("material_digest"),
                code="legacy_recertification_material_digest_invalid",
            ),
            "action": str(raw.get("action") or ""),
        }
        if decision["action"] != "confirm":
            raise LegacyMigrationError(
                "legacy_recertification_decision_action_invalid",
                decision["case_key"],
            )
        if decision["case_key"] in seen:
            raise LegacyMigrationError(
                "legacy_recertification_duplicate_decision",
                decision["case_key"],
            )
        seen.add(decision["case_key"])
        decisions.append(decision)
    canonical = tuple(sorted(decisions, key=lambda row: row["case_key"]))
    decision_set_digest = content_hash(list(canonical))
    return parsed, canonical, decision_set_digest


def _validate_recertification_decisions(
    decisions: tuple[dict[str, Any], ...],
    cases: Iterable[Mapping[str, Any]],
) -> None:
    by_key = {str(case.get("case_key") or ""): case for case in cases}
    supplied = {decision["case_key"]: decision for decision in decisions}
    missing = sorted(set(by_key) - set(supplied))
    extra = sorted(set(supplied) - set(by_key))
    if missing:
        raise LegacyMigrationError(
            "legacy_recertification_decisions_incomplete", *missing
        )
    if extra:
        raise LegacyMigrationError(
            "legacy_recertification_decision_unknown_case", *extra
        )
    for case_key in sorted(by_key):
        case = by_key[case_key]
        decision = supplied[case_key]
        if decision["target_digest"] != case.get("target_digest"):
            raise LegacyMigrationError(
                "legacy_recertification_decision_target_stale", case_key
            )
        if decision["material_digest"] != case.get("material_digest"):
            raise LegacyMigrationError(
                "legacy_recertification_decision_material_stale", case_key
            )


def _recertification_receipt_from_head(
    repository: CanonV3Repository,
    head_hash: str | None,
) -> dict[str, Any] | None:
    if head_hash is None:
        return None
    try:
        genesis = _genesis_manifest(repository, head_hash)
    except CanonRepositoryError:
        return None
    metadata = genesis.get("genesis_metadata")
    receipt = (
        metadata.get("recertification")
        if isinstance(metadata, Mapping)
        else None
    )
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("schema_version") != LEGACY_RECERTIFICATION_RECEIPT_SCHEMA
    ):
        return None
    return copy.deepcopy(dict(receipt))


def _idempotent_recertification_result(
    *,
    repository: CanonV3Repository,
    current_head: str,
    request: Mapping[str, Any],
    decision_set_digest: str,
) -> dict[str, Any] | None:
    receipt = _recertification_receipt_from_head(repository, current_head)
    if receipt is None:
        return None
    if (
        receipt.get("prior_head_hash") != request["expected_current_head"]
        or receipt.get("detached_plan_digest")
        != request["detached_plan_digest"]
        or receipt.get("publish_token") != request["publish_token"]
        or receipt.get("review_decision_set_digest") != decision_set_digest
    ):
        return None
    chain = _manifest_chain(repository, current_head)
    terminal_head, terminal_manifest = chain[0]
    prior_manifest = terminal_manifest
    receipt_binding = {
        "prior_head_hash": receipt.get("prior_head_hash"),
        "detached_plan_digest": receipt.get("detached_plan_digest"),
        "publish_token": receipt.get("publish_token"),
        "review_decision_set_digest": receipt.get(
            "review_decision_set_digest"
        ),
        "review_cases_digest": receipt.get("review_cases_digest"),
    }
    for manifest_hash, manifest in chain[1:]:
        if repository._author_axiom_manifest_entries(  # noqa: SLF001
            manifest
        ) != repository._author_axiom_manifest_entries(  # noqa: SLF001
            prior_manifest
        ):
            break
        entries = repository._manifest_entries(manifest)  # noqa: SLF001
        prior_entries = repository._manifest_entries(  # noqa: SLF001
            prior_manifest
        )
        if entries == prior_entries or not entries:
            break
        commit = repository.read_commit(str(entries[-1].get("commit_hash") or ""))
        wrapper = repository.recertified_suffix_wrapper(
            str(commit.get("transaction_hash") or "")
        )
        if (
            wrapper is None
            or wrapper.get("recertification_binding") != receipt_binding
        ):
            break
        terminal_head = manifest_hash
        terminal_manifest = manifest
        prior_manifest = manifest
    current_manifest = repository.read_manifest(
        current_head, validate_references=True
    )
    return {
        "schema_version": LEGACY_RECERTIFICATION_RESULT_SCHEMA,
        "published": False,
        "idempotent_replay": True,
        "prior_head_hash": request["expected_current_head"],
        "head_hash": terminal_head,
        "recertification_terminal_head": terminal_head,
        "current_head": current_head,
        "generation": int(terminal_manifest.get("generation") or 0),
        "current_generation": int(current_manifest.get("generation") or 0),
        "detached_plan_digest": request["detached_plan_digest"],
        "publish_token": request["publish_token"],
        "review_decision_set_digest": decision_set_digest,
        "projection_fresh": projection_is_fresh(repository.project_root),
    }


def _inject_recertification_fault(
    fault_injector: Callable[[str], None] | None,
    stage: str,
) -> None:
    if fault_injector is not None:
        fault_injector(stage)


def publish_recertification(
    project_root: str | Path,
    raw_request: Mapping[str, Any],
    *,
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Publish a fully reviewed v1->v2 genesis and suffix with one HEAD CAS.

    Audit and dry-run remain read-only.  This is the only mutating repair
    boundary: every promoted admission, identity resolution, target and old
    decision is independently visible in the bound review cases.  Detached
    objects are harmless until the final compare-and-swap of ``CURRENT``.
    """

    root = _root(project_root)
    repository = CanonV3Repository(root)
    request, decisions, decision_set_digest = (
        _parse_recertification_publish_request(raw_request)
    )
    current = repository.current_head(validate=True)
    if current is not None and current != request["expected_current_head"]:
        replay = _idempotent_recertification_result(
            repository=repository,
            current_head=current,
            request=request,
            decision_set_digest=decision_set_digest,
        )
        if replay is not None:
            return replay
        raise LegacyMigrationError(
            "legacy_recertification_head_conflict",
            f"expected={request['expected_current_head']}",
            f"actual={current}",
        )

    report = audit_cutover(root)
    if report.get("state") != "needs_recertification":
        raise LegacyMigrationError(
            "legacy_recertification_not_publishable",
            *[str(value) for value in report.get("reason_codes") or ()],
        )
    if report.get("current_head") != request["expected_current_head"]:
        raise LegacyMigrationError("legacy_recertification_head_conflict")
    if report.get("detached_plan_digest") != request["detached_plan_digest"]:
        raise LegacyMigrationError("legacy_recertification_plan_stale")
    if report.get("publish_token") != request["publish_token"]:
        raise LegacyMigrationError("legacy_recertification_publish_token_stale")
    _validate_recertification_decisions(decisions, report.get("cases") or ())

    # All expensive source proof and every human decision are validated before
    # writing even an unreachable object.  The common publication/staging lock
    # then closes the only race window before CURRENT is compared and swapped.
    from filelock import FileLock
    from .staging_authority import AUTHORITATIVE_STAGING_LOCK_RELATIVE_PATH

    staging_lock = FileLock(
        str(root / AUTHORITATIVE_STAGING_LOCK_RELATIVE_PATH),
        timeout=10,
    )
    with staging_lock:
        with repository.locked():
            actual = repository._read_current_hash_unvalidated()  # noqa: SLF001
            if actual != request["expected_current_head"]:
                if actual is not None:
                    replay = _idempotent_recertification_result(
                        repository=repository,
                        current_head=actual,
                        request=request,
                        decision_set_digest=decision_set_digest,
                    )
                    if replay is not None:
                        return replay
                raise LegacyMigrationError(
                    "legacy_recertification_head_conflict",
                    f"expected={request['expected_current_head']}",
                    f"actual={actual}",
                )
            staging_kinds = _authoritative_staging_kinds(root)
            if staging_kinds:
                raise LegacyMigrationError(
                    "authoritative_staging_conflicts_with_recertification",
                    *staging_kinds,
                )

            # Re-read every mutable legacy source while holding the publication
            # lock, then require the exact plan/token observed by the reviewer.
            locked_report = audit_cutover(root)
            if (
                locked_report.get("state") != "needs_recertification"
                or locked_report.get("current_head")
                != request["expected_current_head"]
                or locked_report.get("detached_plan_digest")
                != request["detached_plan_digest"]
                or locked_report.get("publish_token") != request["publish_token"]
            ):
                raise LegacyMigrationError("legacy_recertification_plan_stale")
            _validate_recertification_decisions(
                decisions, locked_report.get("cases") or ()
            )
            plan = locked_report.get("detached_plan")
            if not isinstance(plan, Mapping):
                raise LegacyMigrationError("legacy_recertification_plan_missing")
            cutover = _normalize_cutover(plan.get("cutover_chapter"))
            locked_bundle = _build_recertification_bundle(
                root,
                repository,
                expected_current_head=request["expected_current_head"],
                cutover_chapter=cutover,
            )
            if (
                canonical_json_bytes(locked_bundle.plan)
                != canonical_json_bytes(dict(plan))
                or canonical_json_bytes(list(locked_bundle.cases))
                != canonical_json_bytes(
                    list(locked_report.get("cases") or ())
                )
                or locked_bundle.plan.get("detached_plan_digest")
                != request["detached_plan_digest"]
                or locked_bundle.plan.get("publish_token")
                != request["publish_token"]
            ):
                raise LegacyMigrationError("legacy_recertification_plan_stale")
            material = locked_bundle.material
            suffix_materials = locked_bundle.suffix_materials
            negative_by_chapter = (
                locked_bundle.negative_lineage_by_chapter
            )

            receipt = {
                "schema_version": LEGACY_RECERTIFICATION_RECEIPT_SCHEMA,
                "prior_head_hash": request["expected_current_head"],
                "detached_plan_digest": request["detached_plan_digest"],
                "publish_token": request["publish_token"],
                "review_decisions": [copy.deepcopy(row) for row in decisions],
                "review_decision_set_digest": decision_set_digest,
                "review_cases_digest": str(plan.get("review_cases_digest") or ""),
                "semantic_negative_lineage": copy.deepcopy(
                    plan.get("semantic_negative_lineage") or {}
                ),
            }
            metadata = repository._validate_genesis_metadata(  # noqa: SLF001
                material.genesis_metadata(recertification=receipt)
            )
            genesis_payload = {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": 0,
                "parent_head_hash": None,
                "chapters": [],
                "genesis_metadata": metadata,
            }
            detached_head = repository._put_payload_unlocked(  # noqa: SLF001
                "manifest", genesis_payload
            )
            _inject_recertification_fault(fault_injector, "after_genesis")

            entries: list[dict[str, Any]] = []
            predecessor: str | None = None
            from ..chapter_content_binding import require_chapter_binding
            from .evidence import candidate_digest
            from .service import CanonV3Service, PreparedEnvelope
            from .source_verifier import verify_all_candidate_sources

            recertification_service = CanonV3Service(root)
            recertification_service.repository = repository
            for generation, suffix in enumerate(suffix_materials, start=1):
                old_commit = repository.read_commit(str(suffix["commit_hash"]))
                chapter = int(suffix["chapter"])
                lineage = sorted(
                    {
                        *(
                            str(value)
                            for value in old_commit.get(
                                "lineage_decision_hashes"
                            )
                            or ()
                        ),
                        *negative_by_chapter.get(chapter, ()),
                    }
                )
                source_transaction_hash = str(
                    old_commit.get("transaction_hash") or ""
                )
                source_transaction = repository.read_transaction(
                    source_transaction_hash
                )
                try:
                    source_envelope = PreparedEnvelope.model_validate(
                        source_transaction
                    )
                except Exception as exc:
                    raise LegacyMigrationError(
                        "legacy_recertification_suffix_envelope_invalid",
                        str(chapter),
                    ) from exc
                require_chapter_binding(
                    root,
                    chapter,
                    source_envelope.chapter_binding,
                )
                active_digests = sorted(
                    {
                        str(effect.get("candidate_digest") or "")
                        for effect in old_commit.get("canon_effects") or ()
                        if isinstance(effect, Mapping)
                    }
                )
                candidates_by_digest = {
                    candidate_digest(candidate): candidate
                    for candidate in source_envelope.candidates
                }
                if set(active_digests) - set(candidates_by_digest):
                    raise LegacyMigrationError(
                        "legacy_recertification_suffix_candidate_missing",
                        str(chapter),
                    )
                active_candidates = tuple(
                    candidates_by_digest[digest] for digest in active_digests
                )
                # Re-verify immutable manuscript/axiom bytes, then recompile
                # slots, prior-fact links, effect IDs and entity resolution
                # against the new detached parent. Human recertification
                # replaces old scan decisions; it does not copy their HEAD.
                verify_all_candidate_sources(
                    root,
                    source_envelope.chapter_binding,
                    active_candidates,
                    active_author_axiom_source_keys=(
                        recertification_service._active_author_axiom_source_keys(  # noqa: SLF001
                            detached_head
                        )
                    ),
                )
                recompiled = (
                    recertification_service._compile_with_entity_registry(  # noqa: SLF001
                        active_candidates,
                        (),
                        detached_head,
                        (),
                        chapter=chapter,
                    )
                )
                parent_axiom_digest = (
                    recertification_service._active_author_axiom_digest(  # noqa: SLF001
                        detached_head
                    )
                )
                recertified_envelope = PreparedEnvelope(
                    chapter=chapter,
                    chapter_binding=source_envelope.chapter_binding,
                    prepared_transaction=recompiled,
                    candidates=active_candidates,
                    observations=(),
                    scan_attestations=(),
                    source_workflow_digest=request["publish_token"],
                    author_axiom_digest=parent_axiom_digest,
                )
                recertified_effects = [
                    effect.model_dump(mode="json")
                    for effect in recompiled.effects
                ]
                binding = {
                    "prior_head_hash": request["expected_current_head"],
                    "detached_plan_digest": request[
                        "detached_plan_digest"
                    ],
                    "publish_token": request["publish_token"],
                    "review_decision_set_digest": decision_set_digest,
                    "review_cases_digest": str(
                        plan.get("review_cases_digest") or ""
                    ),
                }
                wrapper_payload = {
                    "schema_version": (
                        LEGACY_RECERTIFIED_SUFFIX_TRANSACTION_SCHEMA
                    ),
                    "chapter": chapter,
                    "parent_head": detached_head,
                    "source_current_head": request[
                        "expected_current_head"
                    ],
                    "source_commit_hash": str(suffix["commit_hash"]),
                    "source_transaction_hash": source_transaction_hash,
                    "source_transaction_content_sha256": content_hash(
                        source_transaction
                    ),
                    "source_canon_effects_digest": content_hash(
                        old_commit.get("canon_effects") or []
                    ),
                    "source_decision_hashes": list(
                        old_commit.get("decision_hashes") or ()
                    ),
                    "source_lineage_decision_hashes": list(
                        old_commit.get("lineage_decision_hashes") or ()
                    ),
                    "active_source_candidate_digests": active_digests,
                    "recertified_envelope": recertified_envelope.model_dump(
                        mode="json"
                    ),
                    "recertified_canon_effects_digest": content_hash(
                        recertified_effects
                    ),
                    "entity_registry_digest": (
                        recompiled.entity_registry_digest
                    ),
                    "recertification_binding": binding,
                    "semantic_negative_lineage_hashes": list(
                        negative_by_chapter.get(chapter, ())
                    ),
                }
                transaction_hash = repository._put_payload_unlocked(  # noqa: SLF001
                    "transaction", wrapper_payload
                )
                commit_payload = {
                    "schema_version": "canon-v3/chapter-commit/v1",
                    "chapter": chapter,
                    "revision": int(old_commit.get("revision") or 1),
                    "transaction_hash": transaction_hash,
                    # Every old positive/negative decision was reviewed in the
                    # recertification receipt. Negative semantic tombstones are
                    # kept separately below; old positive heads never masquerade
                    # as decisions for this newly bound transaction.
                    "decision_hashes": [],
                    "lineage_decision_hashes": lineage,
                    "base_head_hash": detached_head,
                    "predecessor_commit_hash": predecessor,
                    "canon_effects": recertified_effects,
                }
                commit_hash = repository._put_payload_unlocked(  # noqa: SLF001
                    "commit", commit_payload
                )
                entries.append(
                    {
                        "chapter": chapter,
                        "revision": int(commit_payload["revision"]),
                        "commit_hash": commit_hash,
                    }
                )
                manifest_payload = {
                    "schema_version": "canon-v3/active-manifest/v1",
                    "generation": generation,
                    "parent_head_hash": detached_head,
                    "chapters": copy.deepcopy(entries),
                }
                detached_head = repository._put_payload_unlocked(  # noqa: SLF001
                    "manifest", manifest_payload
                )
                predecessor = commit_hash
                _inject_recertification_fault(
                    fault_injector, f"after_suffix_chapter_{chapter}"
                )

            # Validate the entire detached ancestry before the only authority
            # mutation.  A failure above leaves the old CURRENT byte-for-byte.
            repository.read_manifest(detached_head, validate_references=True)
            final_bundle = _build_recertification_bundle(
                root,
                repository,
                expected_current_head=request["expected_current_head"],
                cutover_chapter=cutover,
            )
            if (
                _recertification_bundle_bytes(final_bundle)
                != _recertification_bundle_bytes(locked_bundle)
            ):
                raise LegacyMigrationError(
                    "legacy_recertification_sources_changed_before_swap"
                )
            _inject_recertification_fault(fault_injector, "before_head_swap")
            repository._write_current_unlocked(detached_head)  # noqa: SLF001
            _inject_recertification_fault(fault_injector, "after_head_swap")

    projection_error: str | None = None
    try:
        projection = rebuild_projection(root)
        projection_binding = dict(projection.get("binding") or {})
    except Exception as exc:  # Canon is published; projection is disposable.
        projection_error = str(exc)
        projection_binding = {}
    manifest = repository.read_manifest(detached_head, validate_references=True)
    result = {
        "schema_version": LEGACY_RECERTIFICATION_RESULT_SCHEMA,
        "published": True,
        "idempotent_replay": False,
        "prior_head_hash": request["expected_current_head"],
        "head_hash": detached_head,
        "recertification_terminal_head": detached_head,
        "current_head": detached_head,
        "generation": int(manifest.get("generation") or 0),
        "current_generation": int(manifest.get("generation") or 0),
        "detached_plan_digest": request["detached_plan_digest"],
        "publish_token": request["publish_token"],
        "review_decision_set_digest": decision_set_digest,
        "projection_fresh": projection_is_fresh(root),
        "projection_binding": projection_binding,
    }
    if projection_error is not None:
        result["projection_error"] = projection_error
    return result


def repair_cutover_apply(
    project_root: str | Path,
    raw_request: Mapping[str, Any],
    *,
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """CLI-facing name for the explicit recertification publish boundary."""

    return publish_recertification(
        project_root,
        raw_request,
        fault_injector=fault_injector,
    )


def migrate_legacy(
    project_root: str | Path,
    cutover_chapter: int | None = None,
) -> dict[str, Any]:
    """Initialize deterministic Canon v3 genesis from a verified v2 prefix."""
    root = _root(project_root)
    repository = CanonV3Repository(root)
    try:
        existing_head = repository.current_head(validate=True)
    except CanonRepositoryError as exc:
        raise LegacyMigrationError("v3_current_invalid", str(exc)) from exc

    if existing_head is not None:
        status = legacy_prefix_status(root)
        if status.get("state") == "stale" and status.get("reason_codes") == [
            "v3_projection_stale"
        ]:
            rebuild_projection(root)
            status = legacy_prefix_status(root)
        if status.get("migration_required"):
            raise LegacyMigrationError(
                "legacy_migration_required",
                *[str(item) for item in status.get("reason_codes") or []],
            )
        requested = (
            None
            if cutover_chapter is None
            else _normalize_cutover(cutover_chapter)
        )
        if requested is not None and requested != status.get("cutover_chapter"):
            raise LegacyMigrationError(
                "legacy_cutover_mismatch",
                str(requested),
                str(status.get("cutover_chapter")),
            )
        return {
            "schema_version": LEGACY_MIGRATION_RESULT_SCHEMA,
            "migrated": False,
            "head_hash": existing_head,
            "source": status.get("source"),
            "cutover_chapter": status.get("cutover_chapter"),
            "status": status,
        }

    material = _build_material(root, cutover_chapter)
    try:
        head = repository.initialize(
            expected_head=None,
            genesis_metadata=material.genesis_metadata(),
        )
    except CanonRepositoryError as exc:
        raise LegacyMigrationError("v3_initialize_failed", str(exc)) from exc
    try:
        projection = rebuild_projection(root)
    except Exception as exc:
        # CURRENT remains a valid immutable genesis.  A retry can rebuild the
        # disposable projection without re-importing or rewriting canon.
        raise LegacyMigrationError("v3_projection_rebuild_failed", str(exc)) from exc

    status = legacy_prefix_status(root)
    if status.get("migration_required"):
        raise LegacyMigrationError(
            "legacy_sources_changed_after_initialize",
            *[str(item) for item in status.get("reason_codes") or []],
        )
    return {
        "schema_version": LEGACY_MIGRATION_RESULT_SCHEMA,
        "migrated": True,
        "head_hash": head,
        "source": material.source,
        "cutover_chapter": material.cutover_chapter,
        "legacy_snapshot_sha256": material.snapshot_sha256,
        "projection_binding": dict(projection.get("binding") or {}),
        "status": status,
    }


__all__ = [
    "LEGACY_ADMISSION_SCHEMA",
    "LEGACY_CUTOVER_AUDIT_SCHEMA",
    "LEGACY_COMMIT_REF_SCHEMA",
    "LEGACY_GENESIS_SCHEMA",
    "LEGACY_GENESIS_SCHEMA_V1",
    "LEGACY_MIGRATION_RESULT_SCHEMA",
    "LEGACY_RECERTIFICATION_CASE_SCHEMA",
    "LEGACY_RECERTIFICATION_DECISION_SCHEMA",
    "LEGACY_RECERTIFICATION_MATERIAL_SCHEMA",
    "LEGACY_RECERTIFICATION_PUBLISH_REQUEST_SCHEMA",
    "LEGACY_RECERTIFICATION_RECEIPT_SCHEMA",
    "LEGACY_RECERTIFICATION_RESULT_SCHEMA",
    "LEGACY_RECERTIFIED_SUFFIX_TRANSACTION_SCHEMA",
    "LEGACY_REPAIR_DRY_RUN_SCHEMA",
    "LEGACY_SNAPSHOT_SCHEMA",
    "LEGACY_STATUS_SCHEMA",
    "LegacyMigrationError",
    "audit_cutover",
    "legacy_prefix_status",
    "migrate_legacy",
    "publish_recertification",
    "repair_cutover_apply",
    "repair_cutover_dry_run",
]
