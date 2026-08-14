#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事实审查契约测试。"""
import hashlib

import pytest

from data_modules.review_schema import (
    FAST_REVIEW_DIMENSIONS,
    REVIEW_DIMENSIONS,
    ReviewDimensionResult,
    ReviewIssue,
    ReviewResult,
    parse_review_output,
)


def _binding(chapter: int) -> dict:
    raw = "待审正文".encode("utf-8")
    return {
        "schema_version": "canon-ledger-chapter-content-binding/v1",
        "chapter": chapter,
        "path": f"正文/第{chapter:04d}章.md",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def _dimensions(names=REVIEW_DIMENSIONS) -> list[ReviewDimensionResult]:
    return [ReviewDimensionResult(dimension=name, conclusion="未发现事实问题") for name in names]


def _raw_dimensions(names=REVIEW_DIMENSIONS) -> list[dict[str, str]]:
    return [{"dimension": name, "conclusion": "未发现事实问题"} for name in names]


def test_严重问题默认阻断():
    issue = ReviewIssue(
        severity="critical",
        category="continuity",
        location="第三段",
        description="主角在上章已经失去灵力，本章却直接施展了御剑术。",
    )
    assert issue.blocking is True


def test_标准模式完整保存五维结论():
    result = ReviewResult(
        chapter=10,
        review_mode="standard",
        dimension_results=_dimensions(),
        issues=[
            ReviewIssue(
                severity="critical",
                category="timeline",
                description="倒计时从三天无故回到了五天。",
            )
        ],
        summary="本章发现一处时间线问题。",
    )

    payload = result.to_dict()

    assert payload["review_status"] == "completed"
    assert payload["review_degraded"] is False
    assert payload["reviewed_dimensions"] == list(REVIEW_DIMENSIONS)
    assert payload["skipped_dimensions"] == []
    assert payload["blocking_count"] == 1


def test_快速模式明确报告有限维度():
    result = ReviewResult(
        chapter=11,
        review_mode="fast",
        dimension_results=_dimensions(FAST_REVIEW_DIMENSIONS),
        summary="本轮只完成快速事实审查。",
    )

    payload = result.to_dict()

    assert payload["review_status"] == "partial"
    assert payload["review_degraded"] is True
    assert payload["reviewed_dimensions"] == list(FAST_REVIEW_DIMENSIONS)
    assert payload["skipped_dimensions"] == ["logic"]


def test_最简模式显式记录全部跳过():
    result = ReviewResult(
        chapter=12,
        review_mode="minimal",
        dimension_results=[],
        summary="用户明确选择跳过事实审查。",
    )

    payload = result.to_dict()

    assert payload["review_status"] == "skipped"
    assert payload["review_skipped"] is True
    assert payload["review_degraded"] is True
    assert payload["skipped_dimensions"] == list(REVIEW_DIMENSIONS)


def test_标准模式缺少任一维度时拒绝():
    with pytest.raises(ValueError, match="standard 模式必须"):
        ReviewResult(
            chapter=13,
            review_mode="standard",
            dimension_results=_dimensions(REVIEW_DIMENSIONS[:-1]),
        )


def test_解析标准模式并复核正文绑定():
    raw = {
        "chapter": 5,
        "chapter_binding": _binding(5),
        "review_mode": "standard",
        "dimension_results": _raw_dimensions(),
        "issues": [
            {
                "severity": "critical",
                "category": "continuity",
                "location": "第一段",
                "description": "上章写明木桥已经坠入河中，本章众人却直接从桥上通过。",
                "evidence": "上章末尾木桥坠河，本章第一段众人踏桥过河。",
                "fix_hint": "补充木桥被修复的事实，或改用其他过河方式。",
            }
        ],
        "summary": "本章发现一处严重的连贯性问题。",
    }

    result = parse_review_output(chapter=5, raw=raw, expected_binding=_binding(5))

    assert result.chapter == 5
    assert result.review_mode == "standard"
    assert result.blocking_count == 1


def test_解析结果缺少模式时拒绝():
    raw = {
        "chapter": 1,
        "chapter_binding": _binding(1),
        "dimension_results": _raw_dimensions(),
        "issues": [],
    }
    with pytest.raises(ValueError, match="review_mode"):
        parse_review_output(chapter=1, raw=raw)


def test_解析结果包含文风分类时拒绝():
    raw = {
        "chapter": 2,
        "chapter_binding": _binding(2),
        "review_mode": "standard",
        "dimension_results": _raw_dimensions(),
        "issues": [
            {
                "severity": "medium",
                "category": "ai_flavor",
                "description": "这不是事实一致性问题。",
            }
        ],
    }
    with pytest.raises(ValueError, match="未知事实审查分类"):
        parse_review_output(chapter=2, raw=raw)


def test_审计记录不包含评分字段():
    result = ReviewResult(
        chapter=10,
        review_mode="standard",
        dimension_results=_dimensions(),
        issues=[
            ReviewIssue(
                severity="high",
                category="setting",
                description="禁地被写成任何弟子都能随意进入。",
            )
        ],
        summary="本章发现一处设定问题。",
    )

    audit = result.to_audit_dict("审查报告/第10章.md")

    assert audit["issues_count"] == 1
    assert audit["review_mode"] == "standard"
    assert audit["report_file"] == "审查报告/第10章.md"
    assert "overall_score" not in audit
    assert "dimension_scores" not in audit


def test_审查报告缺省分类不是非法枚举():
    import review_pipeline

    lines = review_pipeline._format_issue(
        {
            "severity": "low",
            "description": "缺少分类时不得回落到旧枚举。",
        },
        1,
    )
    joined = "\n".join(lines)
    assert "other" not in joined
    assert "未填写分类" in joined
