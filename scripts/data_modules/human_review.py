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

from .chapter_content_binding import ChapterContentBinding
from .story_contracts import write_json


SCHEMA_VERSION = "canon-ledger-human-review/v1"
VALID_ACTIONS = {"confirm", "ignore", "replace"}
_CATEGORY_DIMENSION = {
    "knowledge": "knowledge",
    "knowledge_identity": "knowledge",
    "presence": "presence",
    "presence_kind": "presence",
    "custody": "custody",
    "custody_transition": "custody",
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
    }
    return hashlib.sha256(_canonical_json(stable).encode("utf-8")).hexdigest()[:16]


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
        return _text((decision or {}).get("action"), 40) or "pending"

    def persist_queue(
        self,
        chapter: int,
        binding: dict[str, Any],
        pending: list[Any],
    ) -> list[dict[str, Any]]:
        parsed_binding = ChapterContentBinding.model_validate(binding).model_dump()
        items = self._normalize_items(chapter, parsed_binding, pending)
        decisions = self._decision_map(chapter, parsed_binding["sha256"])
        rendered = [
            {
                **item,
                "status": self._decision_status(item, decisions),
            }
            for item in items
        ]
        write_json(
            self.queue_path(chapter),
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
        event_indexes = {
            _text(event.get("event_id"), 180): index
            for index, event in enumerate(events)
            if _text(event.get("event_id"), 180)
        }
        dropped: set[int] = set()
        replacements: dict[int, dict[str, Any]] = {}
        additions: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        resolved_ids: list[str] = []
        verified_event_ids: list[str] = []
        affected_dimensions: set[str] = set()
        resolved_dimensions: set[str] = set()

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

            action = _text(decision.get("action"), 40)
            if action not in item["options"]:
                raise ValueError(
                    f"human_review_action_not_offered:{item['decision_id']}"
                )
            if action == "ignore":
                if event_index is not None:
                    dropped.add(event_index)
            elif action == "replace":
                replacement = decision.get("replacement_event")
                if not isinstance(replacement, dict):
                    raise ValueError(
                        f"human_review_replacement_missing:{item['decision_id']}"
                    )
                # replace corrects the wording of a real candidate; it may not
                # smuggle in a fact with a different identity.
                candidate_repr = stable_event_repr(chapter, item.get("candidate_event"))
                if not candidate_repr:
                    raise ValueError(
                        f"human_review_replace_requires_candidate:{item['decision_id']}"
                    )
                replacement_repr = stable_event_repr(chapter, replacement) or {}
                for field_name in ("event_type", "subject"):
                    if _text(replacement_repr.get(field_name), 180) != _text(
                        candidate_repr.get(field_name), 180
                    ):
                        raise ValueError(
                            "human_review_replacement_must_keep_"
                            f"{field_name}:{item['decision_id']}"
                        )
                verified = self._verified_event(replacement)
                if event_index is None:
                    additions.append(verified)
                else:
                    replacements[event_index] = verified
                verified_event_ids.append(_text(verified.get("event_id"), 180))
            elif action == "confirm":
                if event_index is not None:
                    replacements[event_index] = self._verified_event(
                        events[event_index]
                    )
                    verified_event_ids.append(item["candidate_event_id"])
                elif isinstance(item.get("candidate_event"), dict):
                    verified = self._verified_event(item["candidate_event"])
                    additions.append(verified)
                    verified_event_ids.append(_text(verified.get("event_id"), 180))
            else:
                raise ValueError(f"human_review_action_invalid:{item['decision_id']}")
            resolved_ids.append(item["decision_id"])
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
            "resolved_decision_ids": resolved_ids,
            "verified_event_ids": all_verified_ids,
            "affected_dimensions": sorted(affected_dimensions),
            "resolved_dimensions": sorted(resolved_dimensions),
        }

    def record(self, payload: Any) -> dict[str, Any]:
        raw_decisions = payload.get("decisions") if isinstance(payload, dict) else payload
        if not isinstance(raw_decisions, list) or not raw_decisions:
            raise ValueError("human_review_decisions_must_be_nonempty_list")

        queued: dict[str, dict[str, Any]] = {}
        ambiguous_ids: set[str] = set()
        for path in sorted(self.queue_root.glob("chapter_*.json")):
            try:
                queue = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
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
            action = _text(raw.get("action"), 40)
            if action not in VALID_ACTIONS:
                raise ValueError(f"human_review_action_invalid:{decision_id}")
            if action not in (queue_item.get("options") or []):
                raise ValueError(f"human_review_action_not_offered:{decision_id}")
            binding = ChapterContentBinding.model_validate(
                queue_item.get("chapter_binding")
            )
            replacement = raw.get("replacement_event")
            candidate_event = queue_item.get("candidate_event")
            verified_event_id = ""
            verified_event_sha256 = ""
            if action == "replace":
                if not isinstance(replacement, dict):
                    raise ValueError(f"human_review_replacement_missing:{decision_id}")
                if not isinstance(candidate_event, dict) or not candidate_event:
                    raise ValueError(
                        f"human_review_replace_requires_candidate:{decision_id}"
                    )
                replacement_repr = self._normalized_event(
                    binding.chapter, replacement, decision_id, "replacement_event"
                )
                candidate_repr = stable_event_repr(binding.chapter, candidate_event) or {}
                # replace corrects wording; the event identity (type + subject)
                # must stay the candidate's, so a verdict cannot inject an
                # unrelated fact into canon.
                for field_name in ("event_type", "subject"):
                    if _text(replacement_repr.get(field_name), 180) != _text(
                        candidate_repr.get(field_name), 180
                    ):
                        raise ValueError(
                            "human_review_replacement_must_keep_"
                            f"{field_name}:{decision_id}"
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
            elif action == "confirm" and isinstance(candidate_event, dict):
                candidate_repr = self._normalized_event(
                    binding.chapter, candidate_event, decision_id, "candidate_event"
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
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                binding = ChapterContentBinding.model_validate(
                    payload.get("chapter_binding")
                )
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            decisions = self._decision_map(binding.chapter, binding.sha256)
            for raw in payload.get("items") or []:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                item["status"] = self._decision_status(item, decisions)
                result.append(item)
        return result
