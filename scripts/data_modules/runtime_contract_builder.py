#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from chapter_outline_loader import (
    load_chapter_plot_structure,
    load_volume_goal,
    volume_num_for_chapter_from_state,
)

from .story_contract_schema import MasterSetting, ReviewContract, VolumeBrief
from .story_contracts import read_json_if_exists


class RuntimeContractBuilder:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def build_for_chapter(self, chapter: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        master = self._load_master_setting()
        anti_patterns = self._load_anti_patterns()
        plot = self._load_plot_structure(chapter)
        volume = self._resolve_volume(chapter)
        volume_goal = load_volume_goal(self.project_root, volume)

        volume_brief = VolumeBrief.model_validate(
            {
                "meta": {"schema_version": "story-system/v1", "contract_type": "VOLUME_BRIEF"},
                "volume_goal": volume_goal,
                # 题材标签不是套路 preset；默认合同不替作者选择桥段或节奏。
                "selected_tropes": [],
                "selected_pacing": {},
                "selected_scenes": list(plot.get("cpns") or []),
                "anti_patterns": [row.get("text", "") for row in anti_patterns if row.get("text")],
                "system_constraints": [],
                "overrides": {"locked": {}, "append_only": {}, "override_allowed": {}},
            }
        ).model_dump()
        review_contract = ReviewContract.model_validate(
            {
                "meta": {"schema_version": "story-system/v1", "contract_type": "REVIEW_CONTRACT"},
                "must_check": list(plot.get("must_cover_nodes") or []),
                "blocking_rules": list(plot.get("forbidden_zones") or []),
                "genre_specific_risks": [],
                "anti_patterns": volume_brief["anti_patterns"],
                "system_constraints": volume_brief["system_constraints"],
                # Outline fulfillment is advisory by default.  The review
                # contract only hard-gates proven factual contradictions.
                "review_thresholds": {"blocking_count": 0},
                "overrides": {"locked": {}, "append_only": {}, "override_allowed": {}},
            }
        ).model_dump()
        return volume_brief, review_contract

    def _load_master_setting(self) -> MasterSetting:
        raw = read_json_if_exists(self.project_root / ".story-system" / "MASTER_SETTING.json") or {}
        return MasterSetting.model_validate(raw)

    def _load_anti_patterns(self) -> list[Dict[str, Any]]:
        raw = read_json_if_exists(self.project_root / ".story-system" / "anti_patterns.json") or []
        return list(raw)

    def _load_plot_structure(self, chapter: int) -> Dict[str, Any]:
        raw = load_chapter_plot_structure(self.project_root, chapter) or {}
        return {
            # chapter_outline_loader 内部保留章纲解析名称；运行时合同边界
            # 只暴露当前规范字段。
            "must_cover_nodes": list(raw.get("mandatory_nodes") or []),
            "forbidden_zones": list(raw.get("prohibitions") or []),
            "cpns": list(raw.get("cpns") or []),
        }

    def _resolve_volume(self, chapter: int) -> int:
        return volume_num_for_chapter_from_state(self.project_root, chapter) or 1
