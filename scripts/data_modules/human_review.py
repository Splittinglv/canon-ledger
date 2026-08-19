#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistent, chapter-bound decisions for ambiguous extracted facts."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .chapter_content_binding import ChapterContentBinding, chapter_bindings_equal
from .story_contracts import write_json


SCHEMA_VERSION = "canon-ledger-human-review/v1"
VALID_ACTIONS = {"confirm", "ignore", "replace", "rewrite"}
STATUS_PENDING = "pending"
STATUS_RESOLVED = "resolved"
STATUS_REWRITE_REQUIRED = "rewrite_required"
REVIEW_MANUAL_CHECK_SOURCE = "review_manual_check"
_CATEGORY_DIMENSION = {
    "knowledge": "knowledge",
    "knowledge_identity": "knowledge",
    "knowledge_boundary": "knowledge",
    "entity_identity": "knowledge",
    "presence": "presence",
    "presence_kind": "presence",
    "timeline": "presence",
    "continuity": "presence",
    "custody": "custody",
    "custody_transition": "custody",
}
_REVIEW_CHECK_SPECS = {
    "character": {
        "category": "knowledge_boundary",
        "dimension": "knowledge",
        "id_prefix": "knowledge-boundary",
        "options": ["confirm", "rewrite", "replace"],
        "hint": (
            "confirm=作者确认并非越界知情，关闭疑点；"
            "rewrite=确认角色不该知道，返回修改正文；"
            "replace=作者追认一条角色已知事实（须提供 knowledge_state_changed，"
            "state=known）。"
        ),
    },
    "timeline": {
        "category": "timeline",
        "dimension": "presence",
        "id_prefix": "timeline-check",
        "options": ["confirm", "rewrite"],
        "hint": (
            "confirm=作者确认时间线可成立，关闭疑点；"
            "rewrite=确认穿帮，返回修改正文。"
        ),
    },
    "continuity": {
        "category": "continuity",
        "dimension": "presence",
        "id_prefix": "continuity-check",
        "options": ["confirm", "rewrite"],
        "hint": (
            "confirm=作者确认并非不该出现或持有冲突，关闭疑点；"
            "rewrite=确认不该出现或事实冲突，返回修改正文。"
        ),
    },
    "setting": {
        "category": "setting",
        "dimension": "",
        "id_prefix": "setting-check",
        "options": ["confirm", "rewrite"],
        "hint": (
            "confirm=作者确认设定可同时成立，关闭疑点；"
            "rewrite=确认设定冲突，返回修改正文。"
        ),
    },
    "logic": {
        "category": "logic",
        "dimension": "",
        "id_prefix": "logic-check",
        "options": ["confirm", "rewrite"],
        "hint": (
            "confirm=作者确认机械规则可成立，关闭疑点；"
            "rewrite=确认规则冲突，返回修改正文。"
        ),
    },
}
_EVENT_DIMENSION = {
    "knowledge_state_changed": "knowledge",
    "presence_observed": "presence",
    "custody_changed": "custody",
}


def _text(value: Any, limit: int = 1200) -> str:
    return str(value or "").strip()[:limit]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_event_repr(chapter: int, event: Any) -> dict[str, Any] | None:
    """Return a normalization-stable, verification-free view of an event.

    The same candidate appears raw in a fresh extraction and normalized in a
    replayed commit.  Fingerprints and content hashes must match across both,
    so always compare the normalized form with the verification flag removed.
    """
    if not isinstance(event, dict):
        return None
    stripped = {
        key: value for key, value in dict(event).items() if key != "verification"
    }
    try:
        from .chapter_commit_schema import normalize_accepted_events

        normalized = normalize_accepted_events(int(chapter), [stripped])[0]
    except (TypeError, ValueError):
        return stripped
    normalized.pop("verification", None)
    return normalized


def _event_content_sha(chapter: int, event: Any) -> str:
    represented = stable_event_repr(chapter, event)
    if represented is None:
        return ""
    return hashlib.sha256(_canonical_json(represented).encode("utf-8")).hexdigest()


def _candidate_fingerprint(chapter: int, item: dict[str, Any]) -> str:
    stable = {
        "category": item.get("category"),
        "dimension": item.get("dimension"),
        "candidate_event_id": item.get("candidate_event_id"),
        "candidate_event": stable_event_repr(chapter, item.get("candidate_event")),
        "evidence_quote": item.get("evidence_quote"),
        "existing_fact": item.get("existing_fact"),
        "reason": item.get("reason"),
        "new_entity_id": item.get("new_entity_id"),
        "matched_entity_id": item.get("matched_entity_id"),
    }
    return hashlib.sha256(_canonical_json(stable).encode("utf-8")).hexdigest()[:16]


def _decision_receipt(decision: dict[str, Any]) -> str:
    """Fingerprint the decision semantics that a commit actually replayed.

    ``decision_id`` alone is not sufficient provenance: a later edit can keep
    the same ID while changing confirm/ignore/replace or the replacement event.
    Notes and timestamps are intentionally excluded because they do not change
    the canonical effect of the decision.
    """
    try:
        chapter = int(decision.get("chapter") or 0)
    except (TypeError, ValueError):
        chapter = 0
    replacement = decision.get("replacement_event")
    stable = {
        "decision_id": _text(decision.get("decision_id"), 180),
        "chapter": chapter,
        "chapter_sha256": _text(decision.get("chapter_sha256"), 64),
        "candidate_fingerprint": _text(
            decision.get("candidate_fingerprint"), 64
        ),
        "action": _text(decision.get("action"), 40),
        "replacement_event": (
            stable_event_repr(chapter, replacement)
            if isinstance(replacement, dict) and chapter > 0
            else None
        ),
        "verified_event_id": _text(decision.get("verified_event_id"), 180),
        "verified_event_sha256": _text(
            decision.get("verified_event_sha256"), 64
        ),
    }
    return hashlib.sha256(_canonical_json(stable).encode("utf-8")).hexdigest()


def _is_review_knowledge_item(item: dict[str, Any]) -> bool:
    return bool(
        item.get("source") == REVIEW_MANUAL_CHECK_SOURCE
        and item.get("category") == "knowledge_boundary"
    )


def _assert_review_knowledge_replacement(
    event: dict[str, Any],
    decision_id: str,
) -> None:
    if _text(event.get("event_type"), 80) != "knowledge_state_changed":
        raise ValueError(
            f"human_review_replace_requires_knowledge_event:{decision_id}"
        )
    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    if (
        not _text(payload.get("information_id"), 180)
        or _text(payload.get("state"), 40) != "known"
    ):
        raise ValueError(
            f"human_review_replace_requires_known_information:{decision_id}"
        )


def review_manual_check_items_from_review(review: Any) -> list[dict[str, Any]]:
    """Turn reviewer manual_checks into confirm-queue items.

    Character checks stay on the knowledge-boundary path. Timeline and
    continuity checks keep presence unverified until the author decides, so
    “not present / not held” cannot auto-fire. Setting and logic checks are
    queued without a coverage dimension.
    """
    raw_checks: list[Any]
    if hasattr(review, "manual_checks"):
        raw_checks = list(review.manual_checks or [])
    elif isinstance(review, dict):
        raw_checks = list(review.get("manual_checks") or [])
    else:
        raw_checks = []
    items: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    for check in raw_checks:
        if hasattr(check, "model_dump"):
            check = check.model_dump()
        if not isinstance(check, dict):
            continue
        spec = _REVIEW_CHECK_SPECS.get(str(check.get("category") or ""))
        if spec is None:
            continue
        prefix = str(spec["id_prefix"])
        counters[prefix] = counters.get(prefix, 0) + 1
        description = _text(check.get("description"), 600)
        reason = _text(check.get("reason"), 600)
        item: dict[str, Any] = {
            "source": "review_manual_check",
            "category": spec["category"],
            "candidate_event_id": f"{prefix}-{counters[prefix]}",
            "evidence_quote": _text(check.get("evidence"), 600),
            "existing_fact": _text(check.get("location"), 600),
            "reason": f"{description} {reason} {spec['hint']}".strip(),
            "options": list(spec["options"]),
            "blocking": False,
        }
        dimension = str(spec.get("dimension") or "")
        if dimension:
            item["dimension"] = dimension
        items.append(item)
    return items


def knowledge_boundary_items_from_review(review: Any) -> list[dict[str, Any]]:
    """Compatibility alias: all reviewer manual_checks now enter the queue."""
    return review_manual_check_items_from_review(review)


def _assert_replacement_keeps_identity(
    candidate_repr: dict[str, Any],
    replacement_repr: dict[str, Any],
    decision_id: str,
) -> None:
    for field_name in ("event_type", "subject"):
        if _text(replacement_repr.get(field_name), 180) != _text(
            candidate_repr.get(field_name), 180
        ):
            raise ValueError(
                "human_review_replacement_must_keep_"
                f"{field_name}:{decision_id}"
            )
    candidate_payload = (
        candidate_repr.get("payload")
        if isinstance(candidate_repr.get("payload"), dict)
        else {}
    )
    replacement_payload = (
        replacement_repr.get("payload")
        if isinstance(replacement_repr.get("payload"), dict)
        else {}
    )
    information_id = _text(candidate_payload.get("information_id"), 180)
    if information_id and _text(
        replacement_payload.get("information_id"), 180
    ) != information_id:
        raise ValueError(
            f"human_review_replacement_must_keep_information_id:{decision_id}"
        )


def _decision_id(
    chapter: int,
    chapter_sha256: str,
    item: dict[str, Any],
) -> str:
    supplied = _text(item.get("decision_id") or item.get("review_id"), 180)
    prefix = f"ch{int(chapter):04d}-"
    if supplied:
        # Namespace caller-supplied IDs by chapter so an extractor that reuses
        # "review-1" in every chapter can never cross-wire another chapter's
        # decision.
        if supplied.startswith(prefix) or supplied.startswith(f"decision-{prefix}"):
            return supplied
        return f"{prefix}{supplied}"[:180]
    stable_item = {
        key: value
        for key, value in item.items()
        if key not in {"decision_id", "review_id", "status"}
    }
    raw = json.dumps(
        {
            "chapter": chapter,
            "chapter_sha256": chapter_sha256,
            "item": stable_item,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"decision-ch{chapter:04d}-{digest}"


class HumanReviewService:
    """Keep uncertain model candidates out of canon until a person decides.

    A decision is bound to the exact chapter bytes. Editing the prose makes an
    old decision inapplicable instead of silently carrying it forward.
    """

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).expanduser().resolve()
        self.root = self.project_root / ".canon-ledger" / "human-review"
        self.queue_root = self.root / "queue"
        self.ledger_path = self.root / "decisions.json"

    def queue_path(self, chapter: int) -> Path:
        return self.queue_root / f"chapter_{int(chapter):04d}.json"

    def _load_queue(
        self,
        path: Path,
    ) -> tuple[dict[str, Any], ChapterContentBinding]:
        """Load one queue without silently discarding authoritative state."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != SCHEMA_VERSION
                or not isinstance(payload.get("items"), list)
            ):
                raise ValueError("invalid queue envelope")
            binding = ChapterContentBinding.model_validate(
                payload.get("chapter_binding")
            )
            filename_match = re.fullmatch(r"chapter_(\d+)\.json", path.name)
            if (
                int(payload.get("chapter") or 0) != binding.chapter
                or filename_match is None
                or int(filename_match.group(1)) != binding.chapter
            ):
                raise ValueError("queue chapter does not match binding")
            for item in payload["items"]:
                if not isinstance(item, dict):
                    raise ValueError("queue item must be an object")
                if (
                    not _text(item.get("decision_id"), 180)
                    or int(item.get("chapter") or 0) != binding.chapter
                    or _text(item.get("chapter_sha256"), 64) != binding.sha256
                    or not _text(item.get("candidate_fingerprint"), 64)
                ):
                    raise ValueError("queue item binding is invalid")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(
                f"human_review_queue_invalid:{path.name}"
            ) from exc
        return payload, binding

    def _load_ledger(self) -> dict[str, Any]:
        if not self.ledger_path.is_file():
            return {"schema_version": SCHEMA_VERSION, "decisions": []}
        try:
            payload = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("human_review_ledger_invalid") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != SCHEMA_VERSION
            or not isinstance(payload.get("decisions"), list)
        ):
            raise ValueError("human_review_ledger_invalid")
        return payload

    def _normalize_items(
        self,
        chapter: int,
        binding: dict[str, Any],
        pending: list[Any],
    ) -> list[dict[str, Any]]:
        parsed_binding = ChapterContentBinding.model_validate(binding).model_dump()
        chapter_sha256 = parsed_binding["sha256"]
        result: list[dict[str, Any]] = []
        seen_decision_ids: set[str] = set()
        seen_event_ids: set[str] = set()
        for raw in pending:
            item = dict(raw) if isinstance(raw, dict) else {"reason": _text(raw)}
            candidate_event = item.get("candidate_event")
            if candidate_event is not None and not isinstance(candidate_event, dict):
                raise ValueError("human_review_candidate_event_must_be_object")
            candidate_event = dict(candidate_event) if isinstance(candidate_event, dict) else None
            candidate_event_id = _text(
                item.get("candidate_event_id")
                or item.get("event_id")
                or (candidate_event or {}).get("event_id"),
                180,
            )
            if candidate_event_id and candidate_event_id in seen_event_ids:
                raise ValueError(
                    f"human_review_candidate_event_duplicate:{candidate_event_id}"
                )
            if candidate_event_id:
                seen_event_ids.add(candidate_event_id)

            category = _text(item.get("category") or "disambiguation", 80)
            dimension = _text(
                item.get("dimension")
                or _CATEGORY_DIMENSION.get(category, "")
                or _EVENT_DIMENSION.get(
                    _text((candidate_event or {}).get("event_type"), 80),
                    "",
                ),
                40,
            )
            if dimension and dimension not in {"knowledge", "presence", "custody"}:
                raise ValueError(f"human_review_dimension_invalid:{dimension}")
            raw_options = item.get("options")
            options = [
                option
                for option in (
                    _text(value, 40)
                    for value in (
                        raw_options
                        if isinstance(raw_options, list)
                        else ["confirm", "ignore", "replace"]
                    )
                )
                if option in VALID_ACTIONS
            ]
            options = list(dict.fromkeys(options)) or ["confirm", "ignore", "replace"]
            evidence_quote = _text(item.get("evidence_quote"), 600)
            if not evidence_quote and candidate_event:
                event_payload = candidate_event.get("payload")
                if isinstance(event_payload, dict):
                    evidence_quote = _text(event_payload.get("evidence_quote"), 600)
            decision_id = _decision_id(chapter, chapter_sha256, item)
            if decision_id in seen_decision_ids:
                raise ValueError(f"human_review_decision_id_duplicate:{decision_id}")
            seen_decision_ids.add(decision_id)
            entry = {
                "decision_id": decision_id,
                "chapter": int(chapter),
                "chapter_sha256": chapter_sha256,
                "source": _text(item.get("source") or "fact_extraction", 80),
                "category": category,
                "dimension": dimension,
                "candidate_event_id": candidate_event_id,
                "candidate_event": candidate_event,
                "new_entity_id": _text(item.get("new_entity_id"), 180),
                "matched_entity_id": _text(item.get("matched_entity_id"), 180),
                "evidence_quote": evidence_quote,
                "existing_fact": _text(item.get("existing_fact"), 600),
                "reason": _text(
                    item.get("reason")
                    or item.get("question")
                    or item.get("mention")
                    or "需要作者确认这条候选事实",
                    600,
                ),
                "options": options,
                # Ambiguity is advisory by default. Callers may opt into a
                # blocking decision for a genuinely unsafe missing fact.
                "blocking": bool(item.get("blocking", False)),
            }
            # A decision recorded later is bound to this exact candidate
            # content; a new extraction round cannot inherit an old verdict.
            entry["candidate_fingerprint"] = _candidate_fingerprint(chapter, entry)
            result.append(entry)
        return result

    def _decision_map(
        self,
        chapter: int,
        chapter_sha256: str,
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for raw in self._load_ledger().get("decisions") or []:
            if not isinstance(raw, dict):
                continue
            if int(raw.get("chapter") or 0) != int(chapter):
                continue
            if _text(raw.get("chapter_sha256"), 64) != chapter_sha256:
                continue
            decision_id = _text(raw.get("decision_id"), 180)
            if decision_id:
                result[decision_id] = dict(raw)
        return result

    @staticmethod
    def _matched_decision(
        item: dict[str, Any],
        decisions: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Return the recorded decision only when it binds this candidate.

        A decision carries the fingerprint of the candidate it judged.  If the
        queue item was regenerated with different content (new extraction under
        the same prose), the old verdict must not silently apply to it.
        """
        decision = decisions.get(_text(item.get("decision_id"), 180))
        if decision is None:
            return None
        expected = _text(item.get("candidate_fingerprint"), 64)
        recorded = _text(decision.get("candidate_fingerprint"), 64)
        if not expected or recorded != expected:
            return None
        return decision

    def _decision_status(
        self,
        item: dict[str, Any],
        decisions: dict[str, dict[str, Any]],
    ) -> str:
        decision = self._matched_decision(item, decisions)
        if decision is None:
            return STATUS_PENDING
        action = _text(decision.get("action"), 40)
        if (
            item.get("source") == REVIEW_MANUAL_CHECK_SOURCE
            and action in {"ignore", "rewrite"}
        ):
            # v7.2 recorded review "this is a bug" choices as ``ignore``.
            # Preserve that documented meaning while exposing an explicit
            # workflow state for both legacy and new decisions.
            return STATUS_REWRITE_REQUIRED
        return STATUS_RESOLVED

    @staticmethod
    def _effective_action(item: dict[str, Any], action: str) -> str:
        """Map legacy review ``ignore`` to the explicit rewrite outcome."""
        if (
            item.get("source") == REVIEW_MANUAL_CHECK_SOURCE
            and action == "ignore"
        ):
            return "rewrite"
        return action

    @classmethod
    def _offered_actions(cls, item: dict[str, Any]) -> list[str]:
        actions = [
            cls._effective_action(item, _text(value, 40))
            for value in (item.get("options") or [])
        ]
        allowed = (
            {"confirm", "rewrite", "replace"}
            if item.get("source") == REVIEW_MANUAL_CHECK_SOURCE
            else {"confirm", "ignore", "replace"}
        )
        return list(dict.fromkeys(value for value in actions if value in allowed))

    def persist_queue(
        self,
        chapter: int,
        binding: dict[str, Any],
        pending: list[Any],
    ) -> list[dict[str, Any]]:
        parsed_binding = ChapterContentBinding.model_validate(binding).model_dump()
        items = self._normalize_items(chapter, parsed_binding, pending)
        queue_path = self.queue_path(chapter)
        if queue_path.is_file():
            previous, previous_binding = self._load_queue(queue_path)
            if chapter_bindings_equal(previous_binding, parsed_binding):
                current_ids = {
                    _text(item.get("decision_id"), 180) for item in items
                }
                current_fingerprints = {
                    _text(item.get("candidate_fingerprint"), 64)
                    for item in items
                }
                # A nondeterministic second model pass must not erase an item
                # awaiting the author, a rewrite verdict, or a resolved verdict
                # that still needs durable replay provenance. Editing the
                # chapter changes the binding and deliberately starts a fresh
                # review queue.
                for raw in previous.get("items") or []:
                    preserved = {
                        key: value
                        for key, value in raw.items()
                        if key not in {
                            "status",
                            "workflow_state",
                            "decision_action",
                            "decision_sha256",
                        }
                    }
                    decision_id = _text(preserved.get("decision_id"), 180)
                    fingerprint = _text(
                        preserved.get("candidate_fingerprint"), 64
                    )
                    if (
                        decision_id in current_ids
                        or fingerprint in current_fingerprints
                    ):
                        continue
                    items.append(preserved)
                    current_ids.add(decision_id)
                    current_fingerprints.add(fingerprint)
        decisions = self._decision_map(chapter, parsed_binding["sha256"])
        rendered = [
            {
                **item,
                "status": self._decision_status(item, decisions),
            }
            for item in items
        ]
        write_json(
            queue_path,
            {
                "schema_version": SCHEMA_VERSION,
                "chapter": int(chapter),
                "chapter_binding": parsed_binding,
                "items": rendered,
            },
        )
        return items

    @staticmethod
    def _verified_event(event: dict[str, Any]) -> dict[str, Any]:
        return {**dict(event), "verification": "verified"}

    @staticmethod
    def _normalized_event(
        chapter: int,
        event: dict[str, Any],
        decision_id: str,
        role: str,
    ) -> dict[str, Any]:
        """Normalize an event a verdict vouches for; invalid events fail loud."""
        stripped = {
            key: value for key, value in dict(event).items() if key != "verification"
        }
        try:
            from .chapter_commit_schema import normalize_accepted_events

            normalized = normalize_accepted_events(int(chapter), [stripped])[0]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"human_review_{role}_invalid:{decision_id}:{exc}"
            ) from exc
        normalized.pop("verification", None)
        return normalized

    def apply_decisions(
        self,
        chapter: int,
        binding: dict[str, Any],
        pending: list[Any],
        candidate_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        chapter_sha256 = ChapterContentBinding.model_validate(binding).sha256
        decisions = self._decision_map(chapter, chapter_sha256)
        # Extraction is model evidence, not a human verdict.  Never trust a
        # model-supplied ``verified`` flag; only a chapter-bound decision may
        # promote an event from supported to verified.  A replay keeps an
        # event verified only while the decision ledger still holds a matching
        # content hash for it, so a second replay cannot silently downgrade a
        # human-confirmed fact and a new candidate cannot inherit the flag.
        ledger_verified: dict[str, str] = {}
        for decision in decisions.values():
            if _text(decision.get("action"), 40) not in {"confirm", "replace"}:
                continue
            verified_id = _text(decision.get("verified_event_id"), 180)
            verified_sha = _text(decision.get("verified_event_sha256"), 64)
            if verified_id and verified_sha:
                ledger_verified[verified_id] = verified_sha
        events: list[dict[str, Any]] = []
        preserved_verified_ids: list[str] = []
        for event in candidate_events:
            event = dict(event)
            event_id = _text(event.get("event_id"), 180)
            expected_sha = ledger_verified.get(event_id)
            if expected_sha and _event_content_sha(chapter, event) == expected_sha:
                event["verification"] = "verified"
                preserved_verified_ids.append(event_id)
            else:
                event["verification"] = "supported"
            events.append(event)
        candidates_by_id = {
            _text(event.get("event_id"), 180): dict(event)
            for event in events
            if _text(event.get("event_id"), 180)
        }
        enriched_pending: list[Any] = []
        for raw in pending:
            if not isinstance(raw, dict):
                enriched_pending.append(raw)
                continue
            item = dict(raw)
            candidate_id = _text(
                item.get("candidate_event_id") or item.get("event_id"),
                180,
            )
            if not isinstance(item.get("candidate_event"), dict) and candidate_id:
                candidate = candidates_by_id.get(candidate_id)
                if candidate is not None:
                    item["candidate_event"] = candidate
            enriched_pending.append(item)

        items = self.persist_queue(chapter, binding, enriched_pending)
        override_ids = {
            _text(item.get("candidate_event_id"), 180)
            for item in items
            if _text(item.get("candidate_event_id"), 180)
            and isinstance(item.get("candidate_event"), dict)
        }
        if override_ids:
            events = [
                event
                for event in events
                if _text(event.get("event_id"), 180) not in override_ids
            ]
        event_indexes = {
            _text(event.get("event_id"), 180): index
            for index, event in enumerate(events)
            if _text(event.get("event_id"), 180)
        }
        dropped: set[int] = set()
        replacements: dict[int, dict[str, Any]] = {}
        additions: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        rewrite_required: list[dict[str, Any]] = []
        resolved_ids: list[str] = []
        decision_receipts: list[dict[str, str]] = []
        verified_event_ids: list[str] = []
        affected_dimensions: set[str] = set()
        resolved_dimensions: set[str] = set()
        identity_actions: dict[str, str] = {}

        for item in items:
            decision = self._matched_decision(item, decisions)
            event_index = event_indexes.get(item["candidate_event_id"])
            if decision is None:
                unresolved.append(item)
                if event_index is not None:
                    dropped.add(event_index)
                if item.get("dimension"):
                    affected_dimensions.add(str(item["dimension"]))
                continue

            action = self._effective_action(
                item,
                _text(decision.get("action"), 40),
            )
            if action not in self._offered_actions(item):
                raise ValueError(
                    f"human_review_action_not_offered:{item['decision_id']}"
                )
            if action == "rewrite":
                rewrite_required.append(
                    {**item, "status": STATUS_REWRITE_REQUIRED}
                )
                if event_index is not None:
                    dropped.add(event_index)
                if item.get("dimension"):
                    affected_dimensions.add(str(item["dimension"]))
                # A confirmed prose bug is intentionally not a resolved fact
                # decision.  The caller must edit and re-review these bytes.
                continue
            if action == "ignore":
                if event_index is not None:
                    dropped.add(event_index)
            elif action == "replace":
                if item.get("new_entity_id") and not isinstance(
                    item.get("candidate_event"), dict
                ):
                    identity_actions[str(item["new_entity_id"])] = "replace"
                else:
                    replacement = decision.get("replacement_event")
                    if not isinstance(replacement, dict):
                        raise ValueError(
                            f"human_review_replacement_missing:{item['decision_id']}"
                        )
                    replacement_repr = self._normalized_event(
                        chapter,
                        replacement,
                        item["decision_id"],
                        "replacement_event",
                    )
                    # Extraction replace corrects the wording of a real
                    # candidate and may not smuggle in a different identity.
                    # A character review has no candidate event; its explicit
                    # replace option instead lets the author record the missing
                    # knowledge fact.
                    candidate_repr = stable_event_repr(
                        chapter, item.get("candidate_event")
                    )
                    if not candidate_repr:
                        if _is_review_knowledge_item(item):
                            _assert_review_knowledge_replacement(
                                replacement_repr,
                                item["decision_id"],
                            )
                        else:
                            raise ValueError(
                                "human_review_replace_requires_candidate:"
                                f"{item['decision_id']}"
                            )
                    else:
                        _assert_replacement_keeps_identity(
                            candidate_repr,
                            replacement_repr,
                            item["decision_id"],
                        )
                    verified = self._verified_event(replacement_repr)
                    if event_index is None:
                        additions.append(verified)
                    else:
                        replacements[event_index] = verified
                    verified_event_ids.append(_text(verified.get("event_id"), 180))
            elif action == "confirm":
                if item.get("new_entity_id"):
                    identity_actions[str(item["new_entity_id"])] = "confirm"
                source = (
                    item.get("candidate_event")
                    if isinstance(item.get("candidate_event"), dict)
                    else None
                )
                if (
                    source is None
                    and _is_review_knowledge_item(item)
                    and isinstance(decision.get("replacement_event"), dict)
                ):
                    # Compatibility with v7.2 decisions, where character
                    # review confirm required a knowledge event.
                    source = self._normalized_event(
                        chapter,
                        decision["replacement_event"],
                        item["decision_id"],
                        "replacement_event",
                    )
                    _assert_review_knowledge_replacement(
                        source,
                        item["decision_id"],
                    )
                if source is not None:
                    verified = self._verified_event(source)
                    if event_index is not None:
                        replacements[event_index] = verified
                    else:
                        additions.append(verified)
                    verified_event_ids.append(_text(verified.get("event_id"), 180))
                elif event_index is not None:
                    replacements[event_index] = self._verified_event(
                        events[event_index]
                    )
                    verified_event_ids.append(item["candidate_event_id"])
            else:
                raise ValueError(f"human_review_action_invalid:{item['decision_id']}")
            if action == "ignore" and item.get("new_entity_id"):
                identity_actions[str(item["new_entity_id"])] = "ignore"
            resolved_ids.append(item["decision_id"])
            decision_receipts.append(
                {
                    "decision_id": item["decision_id"],
                    "decision_sha256": _decision_receipt(decision),
                }
            )
            if item.get("dimension"):
                resolved_dimensions.add(str(item["dimension"]))

        effective_events = [
            replacements.get(index, event)
            for index, event in enumerate(events)
            if index not in dropped
        ]
        effective_events.extend(additions)
        all_verified_ids = [
            event_id
            for event_id in dict.fromkeys([*preserved_verified_ids, *verified_event_ids])
            if event_id
        ]
        return {
            "events": effective_events,
            "unresolved": unresolved,
            "rewrite_required": rewrite_required,
            "resolved_decision_ids": resolved_ids,
            "decision_receipts": decision_receipts,
            "verified_event_ids": all_verified_ids,
            "affected_dimensions": sorted(affected_dimensions),
            "resolved_dimensions": sorted(resolved_dimensions),
            "identity_actions": identity_actions,
        }

    def record(self, payload: Any) -> dict[str, Any]:
        raw_decisions = payload.get("decisions") if isinstance(payload, dict) else payload
        if not isinstance(raw_decisions, list) or not raw_decisions:
            raise ValueError("human_review_decisions_must_be_nonempty_list")

        queued: dict[str, dict[str, Any]] = {}
        ambiguous_ids: set[str] = set()
        for path in sorted(self.queue_root.glob("chapter_*.json")):
            queue, _binding = self._load_queue(path)
            for item in queue.get("items") or []:
                if isinstance(item, dict) and item.get("decision_id"):
                    decision_id = str(item["decision_id"])
                    if decision_id in queued:
                        ambiguous_ids.add(decision_id)
                    queued[decision_id] = {
                        **item,
                        "chapter_binding": queue.get("chapter_binding") or {},
                    }

        ledger = self._load_ledger()
        by_id = {
            str(item.get("decision_id")): dict(item)
            for item in ledger.get("decisions") or []
            if isinstance(item, dict) and item.get("decision_id")
        }
        recorded: list[str] = []
        for raw in raw_decisions:
            if not isinstance(raw, dict):
                raise ValueError("human_review_decision_must_be_object")
            decision_id = _text(raw.get("decision_id"), 180)
            if decision_id in ambiguous_ids:
                raise ValueError(f"human_review_decision_id_ambiguous:{decision_id}")
            queue_item = queued.get(decision_id)
            if queue_item is None and decision_id:
                # 队列里的 ID 都带 chXXXX- 章节前缀。裁决文件写了不带前缀的
                # 短 ID 时，仅当它在全部队列中唯一对应一条时才接受；多章同名
                # 必须写全前缀，防止串单。
                matches = [
                    qid
                    for qid in queued
                    if re.fullmatch(r"ch\d{4}-" + re.escape(decision_id), qid)
                ]
                if len(matches) > 1:
                    raise ValueError(
                        f"human_review_decision_id_ambiguous:{decision_id}"
                    )
                if len(matches) == 1:
                    decision_id = matches[0]
                    queue_item = queued.get(decision_id)
            if queue_item is None:
                raise ValueError(f"human_review_decision_not_queued:{decision_id}")
            candidate_fingerprint = _text(
                queue_item.get("candidate_fingerprint"), 64
            )
            if not candidate_fingerprint:
                # An old queue file without content binding cannot prove which
                # candidate this verdict judged.  Regenerate the queue first.
                raise ValueError(f"human_review_queue_outdated:{decision_id}")
            action = self._effective_action(
                queue_item,
                _text(raw.get("action"), 40),
            )
            if action not in VALID_ACTIONS:
                raise ValueError(f"human_review_action_invalid:{decision_id}")
            if action not in self._offered_actions(queue_item):
                raise ValueError(f"human_review_action_not_offered:{decision_id}")
            binding = ChapterContentBinding.model_validate(
                queue_item.get("chapter_binding")
            )
            replacement = raw.get("replacement_event")
            candidate_event = queue_item.get("candidate_event")
            verified_event_id = ""
            verified_event_sha256 = ""
            identity_replace = bool(queue_item.get("new_entity_id")) and not isinstance(
                candidate_event, dict
            )
            if action == "replace" and identity_replace:
                pass
            elif action == "replace":
                if not isinstance(replacement, dict):
                    raise ValueError(f"human_review_replacement_missing:{decision_id}")
                replacement_repr = self._normalized_event(
                    binding.chapter, replacement, decision_id, "replacement_event"
                )
                if isinstance(candidate_event, dict) and candidate_event:
                    candidate_repr = (
                        stable_event_repr(binding.chapter, candidate_event) or {}
                    )
                    _assert_replacement_keeps_identity(
                        candidate_repr,
                        replacement_repr,
                        decision_id,
                    )
                elif _is_review_knowledge_item(queue_item):
                    _assert_review_knowledge_replacement(
                        replacement_repr,
                        decision_id,
                    )
                else:
                    raise ValueError(
                        f"human_review_replace_requires_candidate:{decision_id}"
                    )
                replacement_payload = replacement_repr.get("payload")
                quote = str(
                    (replacement_payload or {}).get("evidence_quote") or ""
                ).strip()
                if not quote:
                    raise ValueError(
                        f"human_review_replacement_missing_evidence:{decision_id}"
                    )
                verified_event_id = _text(replacement_repr.get("event_id"), 180)
                verified_event_sha256 = hashlib.sha256(
                    _canonical_json(replacement_repr).encode("utf-8")
                ).hexdigest()
            elif action == "confirm":
                source = candidate_event if isinstance(candidate_event, dict) else None
                if (
                    source is None
                    and _is_review_knowledge_item(queue_item)
                    and isinstance(replacement, dict)
                ):
                    # Accept already-recorded v7.2 confirm payloads while new
                    # prompts use replace for explicit knowledge backfill.
                    source = replacement
                if isinstance(source, dict):
                    candidate_repr = self._normalized_event(
                        binding.chapter, source, decision_id, "candidate_event"
                    )
                    if _is_review_knowledge_item(queue_item):
                        _assert_review_knowledge_replacement(
                            candidate_repr,
                            decision_id,
                        )
                    verified_event_id = _text(candidate_repr.get("event_id"), 180)
                    verified_event_sha256 = hashlib.sha256(
                        _canonical_json(candidate_repr).encode("utf-8")
                    ).hexdigest()
            by_id[decision_id] = {
                "decision_id": decision_id,
                "chapter": binding.chapter,
                "chapter_sha256": binding.sha256,
                "candidate_fingerprint": candidate_fingerprint,
                "action": action,
                "outcome": (
                    STATUS_REWRITE_REQUIRED
                    if action == "rewrite"
                    else STATUS_RESOLVED
                ),
                "source": _text(queue_item.get("source"), 80),
                "category": _text(queue_item.get("category"), 80),
                "replacement_event": (
                    dict(replacement) if isinstance(replacement, dict) else None
                ),
                "verified_event_id": verified_event_id,
                "verified_event_sha256": verified_event_sha256,
                "note": _text(raw.get("note"), 600),
                "recorded_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
            }
            recorded.append(decision_id)

        write_json(
            self.ledger_path,
            {
                "schema_version": SCHEMA_VERSION,
                "decisions": sorted(
                    by_id.values(),
                    key=lambda item: (
                        int(item.get("chapter") or 0),
                        str(item.get("decision_id") or ""),
                    ),
                ),
            },
        )
        return {"recorded": recorded, "count": len(recorded)}

    def list_items(self, chapter: int | None = None) -> list[dict[str, Any]]:
        paths = (
            [self.queue_path(chapter)]
            if chapter is not None
            else sorted(self.queue_root.glob("chapter_*.json"))
        )
        result: list[dict[str, Any]] = []
        for path in paths:
            if not path.is_file():
                continue
            payload, binding = self._load_queue(path)
            decisions = self._decision_map(binding.chapter, binding.sha256)
            for raw in payload.get("items") or []:
                item = dict(raw)
                decision = self._matched_decision(item, decisions)
                item["status"] = self._decision_status(item, decisions)
                item["decision_action"] = _text(
                    (decision or {}).get("action"), 40
                )
                item["decision_sha256"] = (
                    _decision_receipt(decision) if decision is not None else ""
                )
                result.append(item)
        return result

    def gate_summary(self, *, before_chapter: int) -> dict[str, Any]:
        """Return authoritative human-review blockers before a target chapter.

        Queue/ledger state is the source of truth.  ``state.json`` may lag the
        latest decision or replay and must not decide whether prose can advance.
        """
        target = int(before_chapter)
        prior: list[dict[str, Any]] = []
        for path in sorted(self.queue_root.glob("chapter_*.json")):
            match = re.fullmatch(r"chapter_(\d+)\.json", path.name)
            if match is None:
                raise ValueError(f"human_review_queue_invalid:{path.name}")
            queue_chapter = int(match.group(1))
            # Only earlier chapters can constrain this write. A broken current
            # or future queue must not prevent returning to the target chapter;
            # its own pipeline will validate that queue before committing.
            if 0 < queue_chapter < target:
                prior.extend(self.list_items(queue_chapter))
        pending = [item for item in prior if item.get("status") == STATUS_PENDING]
        rewrite_required = [
            item
            for item in prior
            if item.get("status") == STATUS_REWRITE_REQUIRED
        ]

        commits: dict[int, tuple[set[str], dict[str, str]]] = {}
        not_replayed: list[dict[str, Any]] = []
        for item in prior:
            if item.get("status") != STATUS_RESOLVED:
                continue
            chapter = int(item.get("chapter") or 0)
            if chapter not in commits:
                commit_path = (
                    self.project_root
                    / ".story-system"
                    / "commits"
                    / f"chapter_{chapter:03d}.commit.json"
                )
                applied_ids: set[str] = set()
                applied_receipts: dict[str, str] = {}
                try:
                    commit = json.loads(commit_path.read_text(encoding="utf-8"))
                except (FileNotFoundError, OSError, json.JSONDecodeError):
                    commit = {}
                if isinstance(commit, dict):
                    provenance = commit.get("provenance")
                    provenance = provenance if isinstance(provenance, dict) else {}
                    human_review = provenance.get("human_review")
                    human_review = (
                        human_review if isinstance(human_review, dict) else {}
                    )
                    applied_ids = {
                        _text(value, 180)
                        for value in human_review.get("resolved_decision_ids") or []
                        if _text(value, 180)
                    }
                    for receipt in human_review.get("decision_receipts") or []:
                        if not isinstance(receipt, dict):
                            continue
                        decision_id = _text(receipt.get("decision_id"), 180)
                        decision_sha256 = _text(
                            receipt.get("decision_sha256"), 64
                        )
                        if decision_id and decision_sha256:
                            applied_receipts[decision_id] = decision_sha256
                commits[chapter] = (applied_ids, applied_receipts)
            decision_id = _text(item.get("decision_id"), 180)
            decision_sha256 = _text(item.get("decision_sha256"), 64)
            applied_ids, applied_receipts = commits[chapter]
            if (
                decision_id not in applied_ids
                or not decision_sha256
                or applied_receipts.get(decision_id) != decision_sha256
            ):
                not_replayed.append(item)

        def compact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {
                    "chapter": int(row.get("chapter") or 0),
                    "decision_id": _text(row.get("decision_id"), 180),
                    "source": _text(row.get("source"), 80),
                    "category": _text(row.get("category"), 80),
                    "status": _text(row.get("status"), 40),
                    "decision_sha256": _text(
                        row.get("decision_sha256"), 64
                    ),
                }
                for row in rows
            ]

        return {
            "before_chapter": target,
            "pending": compact(pending),
            "rewrite_required": compact(rewrite_required),
            "not_replayed": compact(not_replayed),
            "counts": {
                "pending": len(pending),
                "rewrite_required": len(rewrite_required),
                "not_replayed": len(not_replayed),
            },
        }
