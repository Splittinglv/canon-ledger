#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事实审查结果契约。

审查只记录可验证的一致性问题，不产生文笔评分、节奏评分或写法反模式。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .chapter_content_binding import ChapterContentBinding, chapter_bindings_equal


REVIEW_DIMENSIONS = ("setting", "timeline", "continuity", "character", "logic")
FAST_REVIEW_DIMENSIONS = ("setting", "timeline", "continuity", "character")
VALID_SEVERITIES = {"critical", "high", "medium", "low"}
VALID_CATEGORIES = set(REVIEW_DIMENSIONS)


def expected_dimensions_for_mode(review_mode: str) -> tuple[str, ...]:
    if review_mode == "standard":
        return REVIEW_DIMENSIONS
    if review_mode == "fast":
        return FAST_REVIEW_DIMENSIONS
    if review_mode == "minimal":
        return ()
    raise ValueError("review_result.review_mode 必须是 standard、fast 或 minimal")


@dataclass(frozen=True)
class ReviewDimensionResult:
    dimension: str
    conclusion: str

    def __post_init__(self) -> None:
        if self.dimension not in REVIEW_DIMENSIONS:
            raise ValueError(f"未知审查维度：{self.dimension}")
        if not self.conclusion.strip():
            raise ValueError(f"审查维度 {self.dimension} 缺少结论")

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class ReviewIssue:
    severity: str
    category: str
    location: str = ""
    description: str = ""
    evidence: str = ""
    fix_hint: str = ""
    blocking: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(f"未知问题级别：{self.severity}")
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"未知事实审查分类：{self.category}")
        if self.blocking is None:
            self.blocking = self.severity == "critical"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ManualReviewCheck:
    """A plausible concern that lacks enough evidence for an issue verdict."""

    category: str
    description: str
    reason: str
    location: str = ""
    evidence: str = ""
    options: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"未知人工检查分类：{self.category}")
        if not self.description.strip() or not self.reason.strip():
            raise ValueError("人工检查项必须包含 description 和 reason")
        if any(not str(option).strip() for option in self.options):
            raise ValueError("人工检查项 options 不能包含空字符串")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewResult:
    chapter: int
    review_mode: str
    chapter_binding: Dict[str, Any] = field(default_factory=dict)
    issues: List[ReviewIssue] = field(default_factory=list)
    dimension_results: List[ReviewDimensionResult] = field(default_factory=list)
    manual_checks: List[ManualReviewCheck] = field(default_factory=list)
    summary: str = ""

    def __post_init__(self) -> None:
        expected = expected_dimensions_for_mode(self.review_mode)
        actual = tuple(item.dimension for item in self.dimension_results)
        if actual != expected:
            expected_text = "、".join(expected) if expected else "无"
            actual_text = "、".join(actual) if actual else "无"
            raise ValueError(
                f"{self.review_mode} 模式必须按顺序审查维度 {expected_text}，"
                f"实际为 {actual_text}"
            )
        if self.review_mode == "minimal" and (self.issues or self.manual_checks):
            raise ValueError("minimal 模式跳过审查，不能携带问题或人工检查结论")

    @property
    def issues_count(self) -> int:
        return len(self.issues)

    @property
    def blocking_count(self) -> int:
        return sum(1 for issue in self.issues if issue.blocking)

    @property
    def has_blocking(self) -> bool:
        return self.blocking_count > 0

    @property
    def review_status(self) -> str:
        if self.review_mode == "standard":
            return "completed"
        if self.review_mode == "fast":
            return "partial"
        return "skipped"

    @property
    def review_skipped(self) -> bool:
        return self.review_mode == "minimal"

    @property
    def review_degraded(self) -> bool:
        return self.review_mode != "standard"

    @property
    def reviewed_dimensions(self) -> List[str]:
        return [item.dimension for item in self.dimension_results]

    @property
    def skipped_dimensions(self) -> List[str]:
        reviewed = set(self.reviewed_dimensions)
        return [dimension for dimension in REVIEW_DIMENSIONS if dimension not in reviewed]

    @property
    def severity_counts(self) -> Dict[str, int]:
        counts = {level: 0 for level in ("critical", "high", "medium", "low")}
        for issue in self.issues:
            counts[issue.severity] += 1
        return counts

    @property
    def categories(self) -> List[str]:
        return sorted({issue.category for issue in self.issues})

    @property
    def critical_issues(self) -> List[str]:
        return [
            issue.description
            for issue in self.issues
            if issue.severity == "critical" and issue.description
        ]

    def _build_notes(self) -> str:
        parts: List[str] = []
        if self.summary:
            parts.append(self.summary)
        parts.append(f"问题数={self.issues_count}")
        parts.append(f"阻断数={self.blocking_count}")
        if self.categories:
            parts.append("分类=" + "、".join(self.categories))
        return "；".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chapter": self.chapter,
            "chapter_binding": dict(self.chapter_binding),
            "review_mode": self.review_mode,
            "review_status": self.review_status,
            "review_skipped": self.review_skipped,
            "review_degraded": self.review_degraded,
            "reviewed_dimensions": self.reviewed_dimensions,
            "skipped_dimensions": self.skipped_dimensions,
            "dimension_results": [item.to_dict() for item in self.dimension_results],
            "manual_checks": [item.to_dict() for item in self.manual_checks],
            "issues": [issue.to_dict() for issue in self.issues],
            "issues_count": self.issues_count,
            "blocking_count": self.blocking_count,
            "has_blocking": self.has_blocking,
            "summary": self.summary,
        }

    def to_audit_dict(self, report_file: str = "") -> Dict[str, Any]:
        """生成事实审查审计记录，不生成任何质量分数。"""
        return {
            "chapter": self.chapter,
            "start_chapter": self.chapter,
            "end_chapter": self.chapter,
            "review_mode": self.review_mode,
            "review_status": self.review_status,
            "review_degraded": self.review_degraded,
            "reviewed_dimensions": self.reviewed_dimensions,
            "skipped_dimensions": self.skipped_dimensions,
            "dimension_results": [item.to_dict() for item in self.dimension_results],
            "manual_checks_count": len(self.manual_checks),
            "severity_counts": self.severity_counts,
            "critical_issues": self.critical_issues,
            "report_file": report_file,
            "notes": self._build_notes(),
            "issues_count": self.issues_count,
            "blocking_count": self.blocking_count,
            "categories": self.categories,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

    # 保留旧方法名供调用方迁移，返回值已经是无评分的审计记录。
    def to_metrics_dict(self, report_file: str = "") -> Dict[str, Any]:
        return self.to_audit_dict(report_file=report_file)


def _parse_dimension_results(raw: Any) -> List[ReviewDimensionResult]:
    if not isinstance(raw, list):
        raise ValueError("review_result.dimension_results 必须是数组")
    results: List[ReviewDimensionResult] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"review_result.dimension_results[{index}] 必须是对象")
        results.append(
            ReviewDimensionResult(
                dimension=str(item.get("dimension") or "").strip(),
                conclusion=str(item.get("conclusion") or "").strip(),
            )
        )
    return results


def _parse_manual_checks(raw: Any) -> List[ManualReviewCheck]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise ValueError("review_result.manual_checks 必须是数组")
    checks: List[ManualReviewCheck] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"review_result.manual_checks[{index}] 必须是对象")
        raw_options = item.get("options")
        if raw_options is None:
            raw_options = []
        if not isinstance(raw_options, list):
            raise ValueError(
                f"review_result.manual_checks[{index}].options 必须是数组"
            )
        checks.append(
            ManualReviewCheck(
                category=str(item.get("category") or "").strip(),
                location=str(item.get("location") or ""),
                description=str(item.get("description") or "").strip(),
                evidence=str(item.get("evidence") or ""),
                reason=str(item.get("reason") or "").strip(),
                options=[str(option).strip() for option in raw_options],
            )
        )
    return checks


def parse_review_output(
    chapter: int,
    raw: Dict[str, Any],
    *,
    expected_binding: Dict[str, Any] | None = None,
) -> ReviewResult:
    if not isinstance(raw, dict):
        raise ValueError("审查输出必须是 JSON 对象")
    try:
        declared_chapter = int(raw.get("chapter") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("review_result.chapter 必须是整数") from exc
    if declared_chapter != int(chapter):
        raise ValueError(
            f"review_result.chapter 为 {declared_chapter}，与请求章节 {chapter} 不一致"
        )

    review_mode = str(raw.get("review_mode") or "").strip()
    expected_dimensions_for_mode(review_mode)

    raw_binding = raw.get("chapter_binding")
    try:
        binding = ChapterContentBinding.model_validate(raw_binding).model_dump()
    except Exception as exc:
        raise ValueError("review_result.chapter_binding 缺失或无效") from exc
    if expected_binding is not None and not chapter_bindings_equal(binding, expected_binding):
        raise ValueError("review_result.chapter_binding 与预期正文不一致")

    raw_issues = raw.get("issues")
    if not isinstance(raw_issues, list):
        raise ValueError("review_result.issues 必须是数组")
    issues: List[ReviewIssue] = []
    for index, item in enumerate(raw_issues):
        if not isinstance(item, dict):
            raise ValueError(f"review_result.issues[{index}] 必须是对象")
        issues.append(
            ReviewIssue(
                severity=str(item.get("severity") or "").strip(),
                category=str(item.get("category") or "").strip(),
                location=str(item.get("location") or ""),
                description=str(item.get("description") or ""),
                evidence=str(item.get("evidence") or ""),
                fix_hint=str(item.get("fix_hint") or ""),
                blocking=item.get("blocking"),
            )
        )

    result = ReviewResult(
        chapter=chapter,
        review_mode=review_mode,
        chapter_binding=binding,
        issues=issues,
        dimension_results=_parse_dimension_results(raw.get("dimension_results")),
        manual_checks=_parse_manual_checks(raw.get("manual_checks")),
        summary=str(raw.get("summary") or ""),
    )
    if result.review_mode == "minimal" and raw.get("review_skipped") is not True:
        raise ValueError("minimal 模式必须显式声明 review_skipped=true")
    if result.review_mode != "minimal" and raw.get("review_skipped") is True:
        raise ValueError("standard/fast 模式不能声明跳过审查")
    return result
