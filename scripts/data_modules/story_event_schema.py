#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, Literal

from pydantic import BaseModel, Field


class StoryEvent(BaseModel):
    event_id: str
    chapter: int = Field(ge=1)
    # Optional for persisted v1 commits. Producers of new long-term fact
    # events are required to provide it by chapter_commit_schema.
    sequence: int | None = Field(default=None, ge=1)
    # Model-extracted events are evidence-backed but not author-verified.
    # HumanReviewService upgrades only explicitly confirmed candidates.
    verification: Literal["supported", "verified"] = "supported"
    event_type: Literal[
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
    ]
    subject: str
    payload: Dict[str, Any] = Field(default_factory=dict)
