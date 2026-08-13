#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared write-time as-of validation for the mutable state projection."""
from __future__ import annotations

from typing import Any, Mapping, Tuple


def _material(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, Mapping):
        return any(_material(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_material(item) for item in value)
    return True


def _positive_or_invalid(value: Any) -> bool:
    try:
        return int(value or 0) > 0
    except (TypeError, ValueError):
        return value not in (None, "", 0)


def has_projected_state_content(state: Any) -> bool:
    """Whether a marker-less state contains chapter-derived information."""
    if not isinstance(state, Mapping):
        return bool(state)

    protagonist = state.get("protagonist_state")
    if isinstance(protagonist, Mapping):
        if str(protagonist.get("name") or "").strip():
            return True
        location = protagonist.get("location")
        if isinstance(location, str) and location.strip():
            return True
        if isinstance(location, Mapping) and (
            str(location.get("current") or "").strip()
            or _positive_or_invalid(location.get("last_chapter"))
        ):
            return True
        power = protagonist.get("power")
        if isinstance(power, Mapping):
            if str(power.get("realm") or "").strip() or str(
                power.get("bottleneck") or ""
            ).strip():
                return True
            if power.get("layer") not in (None, "", 0, 1, "0", "1"):
                return True
            if any(
                _material(value)
                for key, value in power.items()
                if key not in {"realm", "layer", "bottleneck"}
            ):
                return True
        golden_finger = protagonist.get("golden_finger")
        if isinstance(golden_finger, Mapping) and (
            str(golden_finger.get("name") or "").strip()
            or bool(golden_finger.get("skills"))
            or _positive_or_invalid(golden_finger.get("level"))
            or _positive_or_invalid(golden_finger.get("cooldown"))
        ):
            return True
        if isinstance(golden_finger, Mapping) and any(
            _material(value)
            for key, value in golden_finger.items()
            if key not in {"name", "level", "cooldown", "skills"}
        ):
            return True
        if protagonist.get("attributes"):
            return True
        if any(
            _material(value)
            for key, value in protagonist.items()
            if key not in {"name", "location", "power", "golden_finger", "attributes"}
        ):
            return True

    progress = state.get("progress")
    if isinstance(progress, Mapping):
        if _positive_or_invalid(progress.get("total_words")):
            return True
        if _material(progress.get("volumes_completed")):
            return True
        if _positive_or_invalid(progress.get("current_volume")) and progress.get(
            "current_volume"
        ) not in (1, "1"):
            return True

    for key in (
        "chapter_meta",
        "disambiguation_warnings",
        "disambiguation_pending",
        "review_checkpoints",
        "relationships",
        "structured_relationships",
        "entities_v3",
        "alias_index",
    ):
        if state.get(key):
            return True

    for key in ("plot_threads", "world_settings"):
        value = state.get(key)
        if isinstance(value, Mapping) and any(_material(item) for item in value.values()):
            return True

    strand_tracker = state.get("strand_tracker")
    if isinstance(strand_tracker, Mapping):
        if _material(strand_tracker.get("history")):
            return True
        if any(
            _positive_or_invalid(strand_tracker.get(key))
            for key in (
                "last_quest_chapter",
                "last_fire_chapter",
                "last_constellation_chapter",
                "chapters_since_switch",
            )
        ):
            return True
        dominant = str(strand_tracker.get("current_dominant") or "").strip()
        if dominant and dominant != "quest":
            return True
    return False


def state_as_of_chapter(state: Any) -> Tuple[int | None, bool]:
    """Return the explicit projection watermark and whether it is trustworthy."""
    if not isinstance(state, Mapping):
        return None, False
    progress = state.get("progress")
    if not isinstance(progress, Mapping) or "current_chapter" not in progress:
        return (None, False) if has_projected_state_content(state) else (0, True)
    raw = progress.get("current_chapter")
    if type(raw) is not int or raw < 0:
        return None, False
    return raw, True


def validate_state_snapshot(state: Any, target_chapter: int) -> Tuple[int | None, bool, str]:
    as_of, valid = state_as_of_chapter(state)
    if not valid:
        return as_of, False, "invalid_state_as_of_chapter"
    if as_of and as_of >= int(target_chapter):
        return as_of, False, "state_snapshot_not_before_target"
    return as_of, True, ""
