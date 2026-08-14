#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json

from data_modules.runtime_contract_builder import RuntimeContractBuilder


def test_runtime_contract_builder_creates_volume_and_review_contracts(tmp_path):
    project_root = tmp_path
    (project_root / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (project_root / ".canon-ledger" / "state.json").write_text(
        json.dumps(
            {
                "progress": {"volumes_planned": [{"volume": 1, "chapters_range": "1-20"}]},
                "chapter_meta": {},
                "disambiguation_pending": [],
                "disambiguation_warnings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project_root / ".story-system" / "MASTER_SETTING.json").parent.mkdir(parents=True, exist_ok=True)
    (project_root / ".story-system" / "MASTER_SETTING.json").write_text(
        json.dumps(
            {
                "meta": {"schema_version": "story-system/v1", "contract_type": "MASTER_SETTING"},
                "route": {"primary_genre": "玄幻退婚流"},
                "master_constraints": {"core_tone": "先压后爆"},
                "base_context": [],
                "source_trace": [],
                "override_policy": {"locked": ["route.primary_genre"], "append_only": ["anti_patterns"], "override_allowed": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project_root / ".story-system" / "anti_patterns.json").write_text(
        json.dumps([{"text": "配角不能抢主角兑现"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (project_root / "大纲").mkdir(parents=True, exist_ok=True)
    (project_root / "大纲" / "总纲.md").write_text(
        "\n".join(
            [
                "## 卷划分",
                "",
                "| 卷号 | 卷名 | 章节范围 | 卷内目标 | 预期结束状态 |",
                "|------|------|----------|----------|----------|",
                "| 1 | 立足 | 1-20 | 卷一站稳脚跟 | 主角在宗门站稳脚跟 |",
            ]
        ),
        encoding="utf-8",
    )
    (project_root / "大纲" / "第1卷-节拍表.md").write_text(
        "\n".join(
            [
                "# 第 1 卷：立足 - 卷内事实规划表",
                "",
                "> 卷内目标：节拍表不得覆盖总纲",
                "> 预期结束状态：节拍表补充结束状态",
            ]
        ),
        encoding="utf-8",
    )
    (project_root / "大纲" / "第1卷-详细大纲.md").write_text(
        "### 第3章：试压\nCBN：继续压迫\n必须覆盖节点：发现陷阱、决定隐忍\n本章禁区：不可提前摊牌",
        encoding="utf-8",
    )

    builder = RuntimeContractBuilder(project_root)
    volume_brief, review_contract = builder.build_for_chapter(3)

    assert volume_brief["meta"]["contract_type"] == "VOLUME_BRIEF"
    assert review_contract["meta"]["contract_type"] == "REVIEW_CONTRACT"
    assert "发现陷阱" in review_contract["must_check"]
    assert "不可提前摊牌" in review_contract["blocking_rules"]
    assert volume_brief["selected_tropes"] == []
    assert review_contract["genre_specific_risks"] == []
    assert volume_brief["volume_goal"]["summary"] == "卷一站稳脚跟"
    assert volume_brief["volume_goal"]["name"] == "立足"
    assert volume_brief["volume_goal"]["chapters_range"] == "1-20"
    assert volume_brief["volume_goal"]["end_state"] == "主角在宗门站稳脚跟"
    assert "玄幻退婚流" not in volume_brief["volume_goal"]["summary"]
    assert "节拍表不得覆盖总纲" not in volume_brief["volume_goal"]["summary"]

    from data_modules.story_contracts import persist_runtime_contracts

    persist_runtime_contracts(project_root, 3, volume_brief, review_contract)
    stored = json.loads(
        (project_root / ".story-system" / "volumes" / "volume_001.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored["volume_goal"]["summary"] == "卷一站稳脚跟"
    assert stored["volume_goal"]["name"] == "立足"


def test_runtime_contract_builder_surfaces_review_extracted_anti_patterns(tmp_path):
    project_root = tmp_path
    (project_root / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (project_root / ".canon-ledger" / "state.json").write_text(
        json.dumps({"progress": {"volumes_planned": []}}, ensure_ascii=False),
        encoding="utf-8",
    )
    story_root = project_root / ".story-system"
    story_root.mkdir(parents=True, exist_ok=True)
    (story_root / "MASTER_SETTING.json").write_text(
        json.dumps(
            {
                "meta": {"schema_version": "story-system/v1", "contract_type": "MASTER_SETTING"},
                "route": {"primary_genre": "仙侠"},
                "master_constraints": {"core_tone": "克制具体"},
                "base_context": [],
                "source_trace": [],
                "override_policy": {"locked": [], "append_only": ["anti_patterns"], "override_allowed": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (story_root / "anti_patterns.json").write_text(
        json.dumps(
            [
                {
                    "text": "唯一一个知道复利公式的人",
                    "source_table": "review_extracted",
                    "source_id": "ch0002_issue_1",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    volume_brief, review_contract = RuntimeContractBuilder(project_root).build_for_chapter(3)

    assert "唯一一个知道复利公式的人" in volume_brief["anti_patterns"]
    assert "唯一一个知道复利公式的人" in review_contract["anti_patterns"]


def test_runtime_contract_builder_reads_volume_goal_from_beat_sheet(tmp_path):
    project_root = tmp_path
    (project_root / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (project_root / ".canon-ledger" / "state.json").write_text(
        json.dumps(
            {
                "progress": {"volumes_planned": [{"volume": 2, "chapters_range": "21-40"}]},
                "chapter_meta": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project_root / ".story-system").mkdir(parents=True, exist_ok=True)
    (project_root / ".story-system" / "MASTER_SETTING.json").write_text(
        json.dumps(
            {
                "meta": {"schema_version": "story-system/v1", "contract_type": "MASTER_SETTING"},
                "route": {"primary_genre": "仙侠"},
                "master_constraints": {},
                "base_context": [],
                "source_trace": [],
                "override_policy": {"locked": [], "append_only": [], "override_allowed": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project_root / "大纲").mkdir(parents=True, exist_ok=True)
    (project_root / "大纲" / "第2卷-节拍表.md").write_text(
        "\n".join(
            [
                "# 第 2 卷：远行 - 卷内事实规划表",
                "> 卷内目标：离开宗门",
                "> 预期结束状态：抵达皇城",
            ]
        ),
        encoding="utf-8",
    )

    volume_brief, _review = RuntimeContractBuilder(project_root).build_for_chapter(21)
    assert volume_brief["volume_goal"]["name"] == "远行"
    assert volume_brief["volume_goal"]["summary"] == "离开宗门"
    assert volume_brief["volume_goal"]["end_state"] == "抵达皇城"
