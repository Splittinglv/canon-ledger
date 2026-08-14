#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json

import pytest

from data_modules.outline_fulfillment import (
    fulfillment_node_errors,
    load_authoritative_planned_nodes,
)


def _write_contract(tmp_path, directive):
    path = tmp_path / ".story-system" / "chapters" / "chapter_001.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"meta": {"chapter": 1}, "chapter_directive": directive},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_outline(tmp_path, nodes):
    path = tmp_path / "大纲" / "第1章-测试.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "### 第一章：测试\n- 必须覆盖节点：" + "；".join(nodes),
        encoding="utf-8",
    )


def test_authoritative_nodes_preserve_canonical_and_legacy_order(tmp_path):
    _write_contract(
        tmp_path,
        {
            "must_cover_nodes": ["识别封蜡缺口"],
            "mandatory_nodes": ["识别封蜡缺口", "记下账房暗号"],
        },
    )

    assert load_authoritative_planned_nodes(tmp_path, 1) == [
        "识别封蜡缺口",
        "记下账房暗号",
    ]


def test_outline_nodes_fail_closed_when_contract_drops_them(tmp_path):
    _write_outline(tmp_path, ["识别封蜡缺口"])
    _write_contract(tmp_path, {"goal": "拿到账簿"})

    with pytest.raises(
        ValueError,
        match="chapter_contract_missing_must_cover_nodes",
    ):
        load_authoritative_planned_nodes(tmp_path, 1)


def test_contract_nodes_must_match_current_outline(tmp_path):
    _write_outline(tmp_path, ["识别封蜡缺口", "记下账房暗号"])
    _write_contract(tmp_path, {"must_cover_nodes": ["识别封蜡缺口"]})

    with pytest.raises(ValueError, match="chapter_contract_outline_nodes_mismatch"):
        load_authoritative_planned_nodes(tmp_path, 1)


@pytest.mark.parametrize(
    "bad_value",
    ["识别封蜡缺口", ["识别封蜡缺口", 3], ["识别封蜡缺口", ""]],
)
def test_authoritative_nodes_reject_malformed_values(tmp_path, bad_value):
    _write_contract(tmp_path, {"must_cover_nodes": bad_value})

    with pytest.raises(ValueError, match="chapter_must_cover"):
        load_authoritative_planned_nodes(tmp_path, 1)


def test_fulfillment_must_copy_and_partition_authoritative_nodes():
    expected = ["识别封蜡缺口", "记下账房暗号"]

    assert fulfillment_node_errors(
        {
            "planned_nodes": expected,
            "covered_nodes": [expected[0]],
            "missed_nodes": [expected[1]],
        },
        expected,
    ) == []
    assert fulfillment_node_errors(
        {"planned_nodes": [], "covered_nodes": [], "missed_nodes": []},
        expected,
    ) == ["fulfillment_planned_nodes_mismatch"]
    assert fulfillment_node_errors(
        {
            "planned_nodes": expected,
            "covered_nodes": [expected[0]],
            "missed_nodes": [],
        },
        expected,
    ) == ["fulfillment_node_partition_mismatch"]
