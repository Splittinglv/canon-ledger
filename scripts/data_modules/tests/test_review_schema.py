#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审查 schema 测试"""
import json

import pytest
import hashlib
from data_modules.review_schema import (
    ReviewIssue,
    ReviewResult,
    append_ai_flavor_anti_patterns,
    parse_review_output,
)


def _binding(chapter: int) -> dict:
    raw = "待审正文".encode("utf-8")
    return {
        "schema_version": "webnovel-chapter-content-binding/v1",
        "chapter": chapter,
        "path": f"正文/第{chapter:04d}章.md",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def test_review_issue_blocking_defaults():
    """critical severity 默认 blocking=True"""
    issue = ReviewIssue(
        severity="critical",
        category="continuity",
        location="第3段",
        description="主角在上章已经失去灵力，本章却直接施展了御剑术。",
    )
    assert issue.blocking is True


def test_review_issue_non_critical_not_blocking():
    """非 critical 默认 blocking=False"""
    issue = ReviewIssue(
        severity="high",
        category="setting",
        location="第7段",
        description="倒计时从三天无故回到了五天。",
    )
    assert issue.blocking is False


def test_review_result_counts():
    """blocking_count 自动计算"""
    result = ReviewResult(
        chapter=10,
        issues=[
            ReviewIssue(
                severity="critical",
                category="continuity",
                location="第1段",
                description="主角在上章已经失去灵力，本章却直接施展了御剑术。",
            ),
            ReviewIssue(
                severity="high",
                category="setting",
                location="第4段",
                description="宗门禁地被写成任何弟子都能随意进入。",
            ),
            ReviewIssue(
                severity="high",
                category="timeline",
                location="第7段",
                description="倒计时从三天无故回到了五天。",
                blocking=True,
            ),
        ],
        summary="本章共发现三处事实一致性问题。",
    )
    assert result.blocking_count == 2
    assert result.issues_count == 3
    assert result.has_blocking is True


def test_review_result_no_issues():
    result = ReviewResult(chapter=10, issues=[], summary="本章未发现需要处理的问题。")
    assert result.blocking_count == 0
    assert result.has_blocking is False


def test_review_result_to_dict_roundtrip():
    result = ReviewResult(
        chapter=10,
        issues=[
            ReviewIssue(
                severity="medium",
                category="ai_flavor",
                location="第5段",
                description="同一句“稳住心神”在相邻段落中重复出现了三次。",
                evidence="“稳住心神”在相邻三段中各出现一次。",
                fix_hint="把其中两处改成角色当时采取的具体动作。",
            ),
        ],
        summary="本章发现一处重复句式问题。",
    )
    d = result.to_dict()
    assert d["chapter"] == 10
    assert d["blocking_count"] == 0
    assert len(d["issues"]) == 1
    assert d["issues"][0]["category"] == "ai_flavor"
    assert d["issues"][0]["fix_hint"] == "把其中两处改成角色当时采取的具体动作。"


def test_parse_review_output_from_dict():
    raw = {
        "chapter": 5,
        "chapter_binding": _binding(5),
        "issues": [
            {
                "severity": "critical",
                "category": "continuity",
                "location": "第1段",
                "description": "上章写明木桥已经坠入河中，本章众人却直接从桥上通过。",
                "evidence": "上章末尾写着“木桥坠入河中”，本章第1段却写着“众人踏桥过河”。",
                "fix_hint": "补充木桥被修复的经过，或改用其他过河方式。",
            },
        ],
        "summary": "本章发现一处严重的连贯性问题。",
    }
    result = parse_review_output(chapter=5, raw=raw)
    assert result.chapter == 5
    assert result.blocking_count == 1


def test_parse_review_output_tolerates_missing_fields():
    raw = {
        "chapter": 1,
        "chapter_binding": _binding(1),
        "issues": [
            {"severity": "low", "description": "同一封信在本章被重复拆开了两次。"},
        ],
        "summary": "本章发现一处轻微的事实问题。",
    }
    result = parse_review_output(chapter=1, raw=raw)
    assert result.issues[0].category == "other"
    assert result.issues[0].location == ""


def test_review_result_to_metrics_dict():
    result = ReviewResult(
        chapter=10,
        issues=[
            ReviewIssue(
                severity="critical",
                category="continuity",
                location="第1段",
                description="上章写明木桥已经坠入河中，本章众人却直接从桥上通过。",
            ),
            ReviewIssue(
                severity="high",
                category="ai_flavor",
                location="第5段",
                description="同一句“稳住心神”在相邻段落中重复出现了三次。",
            ),
        ],
        summary="本章发现两处需要处理的问题。",
    )
    metrics = result.to_metrics_dict()
    assert metrics["chapter"] == 10
    assert metrics["start_chapter"] == 10
    assert metrics["end_chapter"] == 10
    assert metrics["issues_count"] == 2
    assert metrics["blocking_count"] == 1
    assert "continuity" in metrics["categories"]
    assert "ai_flavor" in metrics["categories"]
    assert metrics["severity_counts"]["critical"] == 1
    assert metrics["severity_counts"]["high"] == 1
    assert metrics["critical_issues"] == [
        "上章写明木桥已经坠入河中，本章众人却直接从桥上通过。"
    ]
    assert metrics["report_file"] == ""
    assert metrics["overall_score"] < 100
    assert metrics["dimension_scores"]["continuity"] < 100
    assert metrics["dimension_scores"]["ai_flavor"] < 100


def test_ai_flavor_review_issue_added_to_anti_patterns(tmp_path):
    result = ReviewResult(
        chapter=2,
        issues=[
            ReviewIssue(
                severity="medium",
                category="ai_flavor",
                evidence="唯一一个知道复利公式的人。唯一一个知道账本秘密的人。",
            ),
            ReviewIssue(severity="low", category="ai_flavor", evidence="低风险句式"),
            ReviewIssue(severity="high", category="logic", evidence="逻辑问题"),
        ],
    )

    added = append_ai_flavor_anti_patterns(tmp_path, result)

    patterns = json.loads((tmp_path / ".story-system" / "anti_patterns.json").read_text(encoding="utf-8"))
    assert added == 1
    assert any("唯一一个知道" in item["text"] for item in patterns)
    assert patterns[0]["source_id"].startswith("ch0002_issue_")


def test_ai_flavor_review_feedback_dedupes_evidence(tmp_path):
    existing = tmp_path / ".story-system" / "anti_patterns.json"
    existing.parent.mkdir(parents=True)
    existing.write_text(
        json.dumps([{"text": "第一片 / 第二片 / 第三片", "source_table": "review_extracted"}], ensure_ascii=False),
        encoding="utf-8",
    )
    result = ReviewResult(
        chapter=3,
        issues=[
            ReviewIssue(
                severity="high",
                category="ai_flavor",
                evidence="第一片 / 第二片 / 第三片",
            )
        ],
    )

    added = append_ai_flavor_anti_patterns(tmp_path, result)

    patterns = json.loads(existing.read_text(encoding="utf-8"))
    assert added == 0
    assert len(patterns) == 1
