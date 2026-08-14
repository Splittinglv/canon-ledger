#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, List, Set

from .commit_artifacts import extraction_list, extraction_text


class EventProjectionRouter:
    TABLE = {
        "character_state_changed": ["state", "memory", "vector"],
        "power_breakthrough": ["state", "memory", "vector"],
        "relationship_changed": ["index", "memory", "vector"],
        "world_rule_revealed": ["memory", "vector"],
        "world_rule_broken": ["memory", "vector"],
        "open_loop_created": ["memory", "vector"],
        "open_loop_closed": ["memory", "vector"],
        "promise_created": ["memory", "vector"],
        "promise_paid_off": ["memory", "vector"],
        "artifact_obtained": ["index", "vector"],
        # These remain in the bound commit/event log and are replayed by
        # canonical_history. In particular, knowledge contents must not be
        # flattened into generic vector retrieval where access boundaries are
        # lost.
        "knowledge_state_changed": [],
        "presence_observed": [],
        "custody_changed": [],
    }

    def route(self, event: Dict) -> List[str]:
        return list(self.TABLE.get(str(event.get("event_type") or "").strip(), []))

    def required_writers(self, commit_payload: Dict) -> List[str]:
        writers: Set[str] = set()
        status = str((commit_payload.get("meta") or {}).get("status") or "")
        if status == "rejected":
            writers.add("state")
            # A rejection can replace a previously accepted chapter.  The
            # retrieval writer must clear that chapter's old facts.
            writers.add("vector")
            return sorted(writers)
        if status == "accepted":
            writers.add("state")
            writers.add("index")
            # Even an empty accepted snapshot must run retrieval replacement
            # so removed events from an earlier revision cannot linger.
            writers.add("vector")
        if extraction_list(commit_payload, "entity_deltas"):
            writers.add("index")
        if extraction_text(commit_payload, "summary_text"):
            writers.add("summary")
        if extraction_list(commit_payload, "timeline_events"):
            writers.add("memory")
        for event in extraction_list(commit_payload, "accepted_events"):
            if not isinstance(event, dict):
                continue
            writers.update(self.route(event))
        return sorted(writers)
