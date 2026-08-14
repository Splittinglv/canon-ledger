#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from typing import Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .story_event_schema import StoryEvent
from .chapter_content_binding import ChapterContentBinding, chapter_bindings_equal
from .fact_text import normalize_world_rule_payload

EXTRACTION_CORE_FIELDS = ("accepted_events", "state_deltas", "entity_deltas")
EXTRACTION_LIST_FIELDS = (
    "accepted_events",
    "state_deltas",
    "entity_deltas",
    "entities_appeared",
    "scenes",
    "timeline_events",
)
FULFILLMENT_LIST_FIELDS = (
    "planned_nodes",
    "covered_nodes",
    "missed_nodes",
    "extra_nodes",
)

EVENT_TYPE_ALIASES = {
    "character_state": "character_state_changed",
    "character_state_change": "character_state_changed",
    "state_changed": "character_state_changed",
    "relationship_change": "relationship_changed",
    "relation_changed": "relationship_changed",
    "world_rule": "world_rule_revealed",
    "rule_revealed": "world_rule_revealed",
    "rule_broken": "world_rule_broken",
    "breakthrough": "power_breakthrough",
    "power_up": "power_breakthrough",
    "artifact": "artifact_obtained",
    "item_obtained": "artifact_obtained",
    "promise": "promise_created",
    "promise_resolved": "promise_paid_off",
    "promise_fulfilled": "promise_paid_off",
    "mystery_introduction": "open_loop_created",
    "mystery_introduced": "open_loop_created",
    "unresolved_thread": "open_loop_created",
    "scene_open": "open_loop_created",
    "open_loop": "open_loop_created",
    "loop_closed": "open_loop_closed",
}


class CommitArtifactModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    artifact_name: ClassVar[str]
    wrapper_key: ClassVar[str | None] = None
    required_top_level_fields: ClassVar[tuple[str, ...]] = ()

    chapter_binding: ChapterContentBinding

    @model_validator(mode="before")
    @classmethod
    def validate_top_level_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError(f"{cls.artifact_name} must be a JSON object")

        wrapper_key = cls.wrapper_key
        if wrapper_key and wrapper_key in value:
            if cls.artifact_name == "extraction_result":
                raise ValueError(
                    "extraction_result must expose accepted_events/state_deltas/entity_deltas "
                    "as top-level fields, not nested under extraction"
                )
            raise ValueError(
                f"{cls.artifact_name} fields must be top-level, not nested under {wrapper_key}"
            )

        missing = [
            field for field in cls.required_top_level_fields if field not in value
        ]
        if "chapter_binding" not in value:
            missing.append("chapter_binding")
        if missing:
            raise ValueError(
                f"{cls.artifact_name} missing required top-level fields: "
                + ", ".join(missing)
            )
        return value


def _ensure_list(artifact_name: str, field_name: str, value: Any) -> Any:
    if not isinstance(value, list):
        raise ValueError(f"{artifact_name}.{field_name} must be a list")
    return value


def _ensure_object_list(artifact_name: str, field_name: str, value: Any) -> Any:
    _ensure_list(artifact_name, field_name, value)
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{artifact_name}.{field_name}[{index}] must be a JSON object")
    return value


REVIEW_DIMENSIONS = ("setting", "timeline", "continuity", "character", "logic")
FAST_REVIEW_DIMENSIONS = REVIEW_DIMENSIONS[:3]


class ReviewDimensionResultSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: Literal["setting", "timeline", "continuity", "character", "logic"]
    conclusion: str = Field(min_length=1)


class ReviewIssueSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["critical", "high", "medium", "low"]
    category: Literal["setting", "timeline", "continuity", "character", "logic"]
    location: str = ""
    description: str = ""
    evidence: str = ""
    fix_hint: str = ""
    blocking: bool = Field(strict=True)


class ReviewResult(CommitArtifactModel):
    artifact_name: ClassVar[str] = "review_result"
    wrapper_key: ClassVar[str | None] = "review"
    required_top_level_fields: ClassVar[tuple[str, ...]] = (
        "review_mode",
        "review_status",
        "review_skipped",
        "review_degraded",
        "reviewed_dimensions",
        "skipped_dimensions",
        "dimension_results",
        "issues",
        "issues_count",
        "blocking_count",
        "has_blocking",
    )

    review_mode: Literal["standard", "fast", "minimal"]
    review_status: Literal["completed", "partial", "skipped"]
    review_skipped: bool = Field(strict=True)
    review_degraded: bool = Field(strict=True)
    reviewed_dimensions: list[
        Literal["setting", "timeline", "continuity", "character", "logic"]
    ]
    skipped_dimensions: list[
        Literal["setting", "timeline", "continuity", "character", "logic"]
    ]
    dimension_results: list[ReviewDimensionResultSchema]
    issues: list[ReviewIssueSchema]
    issues_count: int = Field(ge=0, strict=True)
    blocking_count: int = Field(ge=0, strict=True)
    has_blocking: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_review_coverage(self) -> "ReviewResult":
        if self.review_mode == "standard":
            expected_reviewed = list(REVIEW_DIMENSIONS)
            expected_status = "completed"
            expected_skipped = False
            expected_degraded = False
        elif self.review_mode == "fast":
            expected_reviewed = list(FAST_REVIEW_DIMENSIONS)
            expected_status = "partial"
            expected_skipped = False
            expected_degraded = True
        else:
            expected_reviewed = []
            expected_status = "skipped"
            expected_skipped = True
            expected_degraded = True

        expected_skipped_dimensions = [
            dimension for dimension in REVIEW_DIMENSIONS
            if dimension not in expected_reviewed
        ]
        actual_dimension_results = [item.dimension for item in self.dimension_results]
        if self.reviewed_dimensions != expected_reviewed:
            raise ValueError("reviewed_dimensions 与 review_mode 不一致")
        if self.skipped_dimensions != expected_skipped_dimensions:
            raise ValueError("skipped_dimensions 与 review_mode 不一致")
        if actual_dimension_results != expected_reviewed:
            raise ValueError("dimension_results 未完整覆盖本模式要求的维度")
        if self.review_status != expected_status:
            raise ValueError("review_status 与 review_mode 不一致")
        if self.review_skipped is not expected_skipped:
            raise ValueError("review_skipped 与 review_mode 不一致")
        if self.review_degraded is not expected_degraded:
            raise ValueError("review_degraded 与 review_mode 不一致")
        if self.review_mode == "minimal" and self.issues:
            raise ValueError("minimal 模式不能携带问题结论")
        if self.issues_count != len(self.issues):
            raise ValueError("issues_count 与 issues 数量不一致")
        actual_blocking = sum(1 for issue in self.issues if issue.blocking)
        if self.blocking_count != actual_blocking:
            raise ValueError("blocking_count 与 issues 中的阻断项数量不一致")
        if self.has_blocking is not (self.blocking_count > 0):
            raise ValueError("has_blocking 与 blocking_count 不一致")
        return self


class FulfillmentResult(CommitArtifactModel):
    artifact_name: ClassVar[str] = "fulfillment_result"
    wrapper_key: ClassVar[str | None] = "fulfillment"
    required_top_level_fields: ClassVar[tuple[str, ...]] = FULFILLMENT_LIST_FIELDS

    planned_nodes: list[Any]
    covered_nodes: list[Any]
    missed_nodes: list[Any]
    extra_nodes: list[Any]

    @field_validator(*FULFILLMENT_LIST_FIELDS, mode="before")
    @classmethod
    def validate_list_fields(cls, value: Any, info: ValidationInfo) -> Any:
        return _ensure_list(cls.artifact_name, info.field_name, value)


class DisambiguationResult(CommitArtifactModel):
    artifact_name: ClassVar[str] = "disambiguation_result"
    wrapper_key: ClassVar[str | None] = "disambiguation"
    required_top_level_fields: ClassVar[tuple[str, ...]] = ("pending",)

    pending: list[Any]

    @field_validator("pending", mode="before")
    @classmethod
    def validate_pending(cls, value: Any, info: ValidationInfo) -> Any:
        return _ensure_list(cls.artifact_name, info.field_name, value)


class ExtractionResult(CommitArtifactModel):
    artifact_name: ClassVar[str] = "extraction_result"
    wrapper_key: ClassVar[str | None] = "extraction"
    required_top_level_fields: ClassVar[tuple[str, ...]] = EXTRACTION_CORE_FIELDS

    accepted_events: list[dict[str, Any]]
    state_deltas: list[dict[str, Any]]
    entity_deltas: list[dict[str, Any]]
    entities_appeared: list[dict[str, Any]] = Field(default_factory=list)
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    # 时间线是 chapter commit 的一等事实；不能再依赖旧 data-agent 输出中的
    # memory_facts（该字段不属于 commit 主链 schema）。
    timeline_events: list[dict[str, Any]] = Field(default_factory=list)
    chapter_meta: Any = Field(default_factory=dict)
    dominant_strand: Any = ""
    summary_text: str = ""

    @field_validator(*EXTRACTION_LIST_FIELDS, mode="before")
    @classmethod
    def validate_object_list_fields(cls, value: Any, info: ValidationInfo) -> Any:
        return _ensure_object_list(cls.artifact_name, info.field_name, value)

    @field_validator("summary_text", mode="before")
    @classmethod
    def validate_summary_text(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("extraction_result.summary_text must be a string")
        return value


class ChapterCommitMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str
    chapter: int = Field(ge=1)
    status: str


class ChapterCommitSchema(BaseModel):
    """Strict binding envelope for a newly constructed chapter commit.

    Legacy commits remain readable as dictionaries by existing readers, but
    cannot pass this trusted-write schema without a complete content binding.
    """

    model_config = ConfigDict(extra="allow")

    meta: ChapterCommitMeta
    provenance: dict[str, Any]
    chapter_binding: ChapterContentBinding
    review_result: ReviewResult
    fulfillment_result: FulfillmentResult
    disambiguation_result: DisambiguationResult
    extraction_result: ExtractionResult
    projection_status: dict[str, str]

    @model_validator(mode="after")
    def validate_shared_chapter_binding(self) -> "ChapterCommitSchema":
        canonical = self.chapter_binding.model_dump()
        if self.chapter_binding.chapter != self.meta.chapter:
            raise ValueError("chapter_binding chapter does not match commit chapter")

        artifacts = {
            "review_result": self.review_result,
            "fulfillment_result": self.fulfillment_result,
            "disambiguation_result": self.disambiguation_result,
            "extraction_result": self.extraction_result,
        }
        for artifact_name, artifact in artifacts.items():
            if not chapter_bindings_equal(canonical, artifact.chapter_binding):
                raise ValueError(
                    f"{artifact_name}.chapter_binding does not match commit chapter_binding"
                )

        provenance_binding = self.provenance.get("chapter_binding")
        if not chapter_bindings_equal(canonical, provenance_binding):
            raise ValueError(
                "provenance.chapter_binding does not match commit chapter_binding"
            )
        return self


class AcceptedEventInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str
    chapter: int = Field(ge=1)
    event_type: str
    subject: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, value: Any, info: ValidationInfo) -> Any:
        if not isinstance(value, dict):
            index = _event_context_index(info)
            raise ValueError(f"accepted_events[{index}] must be a JSON object")

        payload = dict(value)
        context = info.context or {}
        index = _event_context_index(info)
        expected_chapter = int(context.get("chapter") or 0)
        raw_chapter = payload.get("chapter")
        if raw_chapter not in (None, ""):
            try:
                declared_chapter = int(raw_chapter)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"accepted_events[{index}].chapter must be an integer"
                ) from exc
            if expected_chapter > 0 and declared_chapter != expected_chapter:
                raise ValueError(
                    f"accepted_events[{index}].chapter {declared_chapter} "
                    f"does not match commit chapter {expected_chapter}"
                )
        chapter = expected_chapter or int(raw_chapter or 0)
        payload["chapter"] = chapter

        event_type = str(payload.get("event_type") or payload.get("type") or "").strip()
        if event_type:
            normalized_type = event_type.lower().replace("-", "_")
            payload["event_type"] = EVENT_TYPE_ALIASES.get(normalized_type, normalized_type)

        subject = _event_subject(payload)
        if not subject:
            raise ValueError(
                f"accepted_events[{index}].subject must be a non-empty string"
            )
        payload["subject"] = subject

        if not str(payload.get("event_id") or "").strip():
            payload["event_id"] = _generated_event_id(chapter, index + 1, payload)

        return payload


class AcceptedEventsInput(BaseModel):
    accepted_events: list[Any]

    @field_validator("accepted_events", mode="before")
    @classmethod
    def validate_events_list(cls, value: Any) -> Any:
        if not isinstance(value, list):
            raise ValueError("accepted_events must be a list")
        return value

    def normalize(self, chapter: int) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, event in enumerate(self.accepted_events):
            if not isinstance(event, dict):
                raise ValueError(f"accepted_events[{index}] must be a JSON object")
            payload = AcceptedEventInput.model_validate(
                event,
                context={"chapter": chapter, "index": index},
            ).model_dump()
            normalized_event = StoryEvent.model_validate(payload).model_dump()
            if normalized_event.get("event_type") == "world_rule_revealed":
                normalized_rule = normalize_world_rule_payload(
                    normalized_event.get("payload"),
                    normalized_event.get("subject"),
                )
                if normalized_rule is None:
                    raise ValueError(
                        f"accepted_events[{index}] 世界规则缺少受控类别、"
                        "故事内领域、具体字段、正文原文证据或安全的规则正文"
                    )
                normalized_event["payload"] = {
                    **normalized_event.get("payload", {}),
                    **normalized_rule,
                }
            normalized.append(_normalize_lifecycle_event(normalized_event))
        return normalized


def normalize_accepted_events(chapter: int, events: Any) -> list[dict[str, Any]]:
    accepted_events = AcceptedEventsInput.model_validate({"accepted_events": events})
    return accepted_events.normalize(chapter)


def normalize_timeline_events(chapter: int, events: Any) -> list[dict[str, Any]]:
    """Normalize timeline rows kept in the chapter commit extraction snapshot.

    A timeline row gets a deterministic ID when a legacy producer does not
    provide one.  New producers should always preserve ``timeline_id`` across
    retries/amendments so projection can remain idempotent.
    """
    if not isinstance(events, list):
        raise ValueError("timeline_events must be a list")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_sequences: set[tuple[int, int]] = set()
    for index, raw in enumerate(events):
        if not isinstance(raw, dict):
            raise ValueError(f"timeline_events[{index}] must be a JSON object")
        item = dict(raw)
        event = str(item.get("event") or item.get("content") or item.get("description") or "").strip()
        if not event:
            raise ValueError(f"timeline_events[{index}].event must be a non-empty string")

        try:
            source_chapter = int(item.get("chapter") or chapter)
        except (TypeError, ValueError):
            source_chapter = int(chapter)
        if source_chapter <= 0:
            source_chapter = int(chapter)
        if source_chapter != int(chapter):
            raise ValueError(
                f"timeline_events[{index}].chapter must match commit chapter {chapter}"
            )

        try:
            sequence = int(item.get("sequence") or index + 1)
        except (TypeError, ValueError):
            sequence = index + 1
        sequence = max(1, sequence)

        timeline_id = str(item.get("timeline_id") or "").strip()
        if not timeline_id:
            stable_payload = {
                key: value
                for key, value in item.items()
                if key not in {"timeline_id", "chapter", "sequence"}
            }
            raw_id = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:10]
            timeline_id = f"tl-ch{source_chapter:03d}-{sequence:03d}-{digest}"

        if timeline_id in seen_ids:
            raise ValueError(f"timeline_events[{index}].timeline_id is duplicated: {timeline_id}")
        sequence_key = (source_chapter, sequence)
        if sequence_key in seen_sequences:
            raise ValueError(
                f"timeline_events[{index}].sequence is duplicated in chapter "
                f"{source_chapter}: {sequence}"
            )
        seen_ids.add(timeline_id)
        seen_sequences.add(sequence_key)

        item["timeline_id"] = timeline_id
        item["chapter"] = source_chapter
        item["sequence"] = sequence
        item["event"] = event
        item["time_hint"] = str(item.get("time_hint") or item.get("time_label") or "").strip()
        item["event_type"] = str(item.get("event_type") or "").strip()
        normalized.append(item)
    return normalized


def _event_context_index(info: ValidationInfo) -> int:
    context = info.context or {}
    return int(context.get("index") or 0)


def _event_subject(payload: dict[str, Any]) -> str:
    for key in ("subject", "entity_id", "from_entity", "to_entity"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    characters = payload.get("characters")
    if isinstance(characters, str) and characters.strip():
        return characters.strip()
    if isinstance(characters, list):
        for character in characters:
            if isinstance(character, str) and character.strip():
                return character.strip()

    event_payload = payload.get("payload") or {}
    if isinstance(event_payload, dict):
        for key in ("subject", "entity_id", "owner", "holder", "artifact_id", "name"):
            value = event_payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _generated_event_id(chapter: int, index: int, payload: dict[str, Any]) -> str:
    stable_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"event_id", "chapter"}
    }
    raw = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"evt-ch{chapter:03d}-{index:03d}-{digest}"


def _normalize_lifecycle_event(event: dict[str, Any]) -> dict[str, Any]:
    """Add canonical lifecycle IDs while preserving older accepted-event shapes.

    Creation events can safely use their stable event_id as a compatibility
    fallback.  Closing events are intentionally *not* matched by subject or
    prose: without an explicit target ID the memory projection reports a
    repairable error instead of closing an arbitrary similarly named promise.
    """
    payload = dict(event.get("payload") or {})
    event_type = str(event.get("event_type") or "").strip()
    event_id = str(event.get("event_id") or "").strip()

    if event_type == "open_loop_created":
        payload["loop_id"] = str(
            payload.get("loop_id")
            or payload.get("open_loop_id")
            or event_id
            or ""
        ).strip()
    elif event_type == "promise_created":
        payload["promise_id"] = str(payload.get("promise_id") or event_id or "").strip()
    elif event_type == "open_loop_closed":
        target = (
            payload.get("loop_id")
            or payload.get("target_loop_id")
            or payload.get("open_loop_id")
            or payload.get("target_id")
            or payload.get("resolves_event_id")
        )
        if target:
            payload["loop_id"] = str(target).strip()
    elif event_type == "promise_paid_off":
        target = (
            payload.get("promise_id")
            or payload.get("target_promise_id")
            or payload.get("target_id")
            or payload.get("resolves_event_id")
        )
        if target:
            payload["promise_id"] = str(target).strip()

    event["payload"] = payload
    return event
