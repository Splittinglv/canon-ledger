#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json

from chapter_outline_loader import (
    load_chapter_execution_directive,
    load_chapter_outline,
)


def test_load_chapter_execution_directive_from_volume_outline(tmp_path):
    outline_dir = tmp_path / "大纲"
    outline_dir.mkdir()
    (tmp_path / ".canon-ledger").mkdir()
    (tmp_path / ".canon-ledger" / "state.json").write_text(
        json.dumps({"progress": {"volumes_planned": [{"volume": 1, "chapters_range": "1-50"}]}}),
        encoding="utf-8",
    )
    (outline_dir / "第1卷-详细大纲.md").write_text(
        "\n".join(
            [
                "### 第一章：债从天降",
                "- **目标：搞清楚借据条款的荒谬**",
                "**阻力**：杂役不能随意离开宗门",
                "**代价**：暴露自己懂账",
                "- 时间锚点：决战日清晨",
                "- 章内时间跨度：一炷香",
                "- 与上章间隔：紧接上章",
                "- 倒计时状态：三日内还债",
                "- 本章变化：陆鸣确认借据利息被人篡改",
                "**核心冲突：** 保住秘密与查清债务不可兼得",
                "- 视角：陆鸣限知",
                "- Strand：债务调查",
                "- 反派层级：小反派",
                "- 关键实体：陆鸣、借据、利息",
                "- CBN：陆鸣 | 醒来发现 | 巨额债务",
                "- CPNs：陆鸣 | 检查 | 借据；陆鸣 | 发现 | 复利陷阱",
                "- CPN：陆鸣、赵简 | 合力 | 推开石门",
                "- CEN：陆鸣 | 决定 | 去井边打听",
                "- 必须覆盖节点：陆鸣 | 核对 | 借据金额，确认印章缺角；陆鸣 | 算出 | 复利算法",
                "- 本章禁区：不得离开宗门；不得提前摊牌",
                "**未闭合问题**：谁改了借据？",
                "**结尾钩子：借据背面出现陌生印章**",
                "- 钩子类型：信息钩",
                "- 钩子强度：中",
                "",
                "### 第二章：井边口风",
                "- 目标：打听债主来历",
            ]
        ),
        encoding="utf-8",
    )

    directive = load_chapter_execution_directive(tmp_path, 1)

    assert directive["goal"] == "搞清楚借据条款的荒谬"
    assert directive["obstacles"] == "杂役不能随意离开宗门"
    assert directive["cost"] == "暴露自己懂账"
    assert directive["time_anchor"] == "决战日清晨"
    assert directive["chapter_span"] == "一炷香"
    assert directive["previous_chapter_gap"] == "紧接上章"
    assert directive["countdown"] == "三日内还债"
    assert directive["chapter_change"] == "陆鸣确认借据利息被人篡改"
    assert directive["core_conflict"] == "保住秘密与查清债务不可兼得"
    assert directive["viewpoint"] == "陆鸣限知"
    assert directive["strand"] == "债务调查"
    assert directive["antagonist_tier"] == "小反派"
    assert directive["cbn"] == "陆鸣 | 醒来发现 | 巨额债务"
    assert directive["cpns"] == [
        "陆鸣 | 检查 | 借据",
        "陆鸣 | 发现 | 复利陷阱",
        "陆鸣、赵简 | 合力 | 推开石门",
    ]
    assert directive["cen"] == "陆鸣 | 决定 | 去井边打听"
    assert directive["must_cover_nodes"] == [
        "陆鸣 | 核对 | 借据金额，确认印章缺角",
        "陆鸣 | 算出 | 复利算法",
    ]
    assert "不得离开宗门" in directive["forbidden_zones"]
    assert "借据" in directive["key_entities"]
    assert directive["chapter_end_open_question"] == "谁改了借据？"
    assert directive["hook"] == "借据背面出现陌生印章"
    assert directive["hook_type"] == "信息钩"
    assert directive["hook_strength"] == "中"
    assert directive["source"] == "chapter_outline"


def test_load_directive_supports_hundred_chinese_heading_without_neighbor_leakage(
    tmp_path,
):
    outline_dir = tmp_path / "大纲"
    outline_dir.mkdir()
    (tmp_path / ".canon-ledger").mkdir()
    (tmp_path / ".canon-ledger" / "state.json").write_text(
        json.dumps(
            {
                "progress": {
                    "volumes_planned": [
                        {"volume": 2, "chapters_range": "51-150"}
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (outline_dir / "第2卷-详细大纲.md").write_text(
        "\n".join(
            [
                "### 第九十九章：旧线索",
                "- 目标：只属于九十九章",
                "### 第一百章：账簿",
                "- 目标：拿到账簿",
                "- 章内时间跨度：一炷香",
                "### 第一百零一章：追兵",
                "- 目标：只属于一百零一章",
            ]
        ),
        encoding="utf-8",
    )

    directive = load_chapter_execution_directive(tmp_path, 100)
    raw_outline = load_chapter_outline(tmp_path, 100, max_chars=None)

    assert directive["goal"] == "拿到账簿"
    assert directive["chapter_span"] == "一炷香"
    assert "只属于九十九章" not in raw_outline
    assert "只属于一百零一章" not in raw_outline
