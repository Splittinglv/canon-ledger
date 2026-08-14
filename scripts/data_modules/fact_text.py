#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize story facts. Author sources keep their wording; model text only drops jailbreaks."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


# Model-authored free text may try to override the writing contract. Keep only
# that jailbreak filter — not a style/pacing vocabulary. Ordinary facts such as
# 「用三年时间炼成金丹」 or 「限知视角」 must survive.
_JAILBREAK_PATTERNS = (
    re.compile(
        r"(?:忽略|无视|绕过|不要遵循|不必遵循).{0,24}"
        r"(?:合同|规则|约束|指令|提示词|系统提示|既有设定|前文要求)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ignore|disregard|override|do\s+not\s+follow)\b.{0,40}"
        r"\b(?:prompt|instruction|contract|rules?|previous|system|constraints?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:as\s+(?:an?\s+)?(?:language\s+model|assistant)|"
        r"follow\s+(?:the\s+)?(?:user|system|developer)?\s*(?:requests?|instructions?)|"
        r"obey\s+(?:the\s+)?(?:user|system|developer))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:作为(?:一个)?(?:语言模型|助手)|扮演|假装).{0,16}"
        r"(?:不受约束|无限制|越狱)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[\s\"'`])(?:system|assistant|developer|user)\s*"
        r"(?:prompt|message)?\s*[:：]",
        re.IGNORECASE,
    ),
)

_FACT_ATOM = re.compile(r"^[\w\u4e00-\u9fff·.()（）:/\- ]+$", re.UNICODE)

# 世界规则的主语应是故事世界中的人、物、制度或环境，而不是“章节/段落/读者”
# 这类创作单位。只有“创作单位 + 应当如何安排”同时出现时才拒绝，因此
# “宵禁后不得点燃蓝灯”这类世界内铁律仍然可用。
_STORY_STRUCTURE_TARGET = re.compile(
    r"(?:每(?:一)?(?:章|回|节|段|句)|(?:本|下|上)章|章节|"
    r"章(?:首|末|尾)|开场|开头|结尾|收尾|正文|段落|句子|"
    r"场景(?:转换|切换|过渡)?|转场|故事单元|情节节点|推进频率|"
    r"读者|作者|写手|模型|叙事|剧情|桥段|情节)",
    re.IGNORECASE,
)

WORLD_RULE_CATEGORIES = frozenset(
    {
        "自然", "物理", "地理", "时间", "力量", "法术", "科技", "制度",
        "法律", "社会", "习俗", "经济", "金融", "资源", "生物", "契约",
        "组织", "能力",
        "natural", "physical", "geography", "time", "power", "magic",
        "technology", "institution", "law", "social", "custom", "economy",
        "finance", "resource", "biology", "contract", "organization", "ability",
    }
)
_WORLD_RULE_META_ATOM = re.compile(
    r"(?:全局|章节|章法|场景转换|转场|段落|句子|读者|作者|"
    r"写手|模型|叙事|剧情|桥段|情节|反转|悬念|意外|"
    r"钩子|爽点|global|narrative|chapter|writing|reader|author)",
    re.IGNORECASE,
)
_STORY_STRUCTURE_PRESCRIPTION = re.compile(
    r"(?:必须|应该|应当|需要|务必|总要|最好|都要|"
    r"出现|加入|添加|安排|设置|制造|保留|留下|写入|"
    r"描写|改写|揭示|呈现|反转|悬念|意外|钩子|爽点)",
    re.IGNORECASE,
)

_WORLD_RULE_META_LANGUAGE = re.compile(
    r"(?:故事|叙事|剧情|情节|篇章|章节|章回|回目|正文|段落|句子|"
    r"场景转换|场景切换|转场|故事单元|情节节点|推进频率|"
    r"读者|作者|写手|模型|创作|写作).{0,32}"
    r"(?:推进|展开|发展|收束|结束|开场|结尾|安排|设置|制造|"
    r"出现|加入|添加|呈现|反转|悬念|意外|变故|钩子|爽点)|"
    r"(?:推进|展开|发展|收束).{0,20}"
    r"(?:故事|叙事|剧情|情节|篇章|章节|章回|回目|场景)",
    re.IGNORECASE,
)

_WORLD_RULE_EVIDENCE_LIMIT = 600


def sanitize_world_rule_text(value: Any, *, max_chars: int = 1200) -> str:
    """只保留故事世界内的声明式铁律，拒绝伪装成规则的章法配方。"""
    text = sanitize_fact_text(value, max_chars=max_chars)
    if not text:
        return ""
    if _STORY_STRUCTURE_TARGET.search(text) and _STORY_STRUCTURE_PRESCRIPTION.search(text):
        return ""
    if _WORLD_RULE_META_LANGUAGE.search(text):
        return ""
    return text


def normalize_world_rule_payload(payload: Any, subject: Any) -> dict[str, str] | None:
    """将模型抽取的世界规则收口为闭合的故事内事实。

    自由文本不能仅凭 ``rule_content`` 就升级为硬约束；必须同时声明
    受控类别、故事内领域和具体字段，且事件主体与领域一致。
    """
    if not isinstance(payload, dict):
        return None
    raw_content = payload.get("rule_content")
    if not isinstance(raw_content, str) or not raw_content.strip():
        return None
    content = sanitize_world_rule_text(raw_content)
    if not content or content != raw_content.strip():
        return None
    category = sanitize_fact_atom(payload.get("rule_category"), max_chars=32)
    domain = sanitize_fact_atom(payload.get("domain"), max_chars=160)
    field = sanitize_fact_atom(payload.get("field"), max_chars=160)
    event_subject = sanitize_fact_atom(subject, max_chars=160)
    raw_evidence = payload.get("evidence_quote")
    if not isinstance(raw_evidence, str):
        return None
    evidence_quote = raw_evidence.strip()
    if (
        not evidence_quote
        or len(evidence_quote) > _WORLD_RULE_EVIDENCE_LIMIT
        or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", evidence_quote)
        or content not in evidence_quote
        or domain not in evidence_quote
    ):
        return None
    if (
        category not in WORLD_RULE_CATEGORIES
        or not domain
        or not field
        or not event_subject
        or event_subject != domain
        or _WORLD_RULE_META_ATOM.search(domain)
        or _WORLD_RULE_META_ATOM.search(field)
    ):
        return None
    scope = sanitize_fact_atom(payload.get("scope"), max_chars=80) or "global"
    return {
        "rule_content": content,
        "rule_category": category,
        "domain": domain,
        "field": field,
        "scope": scope,
        "evidence_quote": evidence_quote,
    }


def world_rule_evidence_in_chapter(
    payload: Any,
    subject: Any,
    chapter_text: Any,
) -> bool:
    """确认世界规则的证据原文确实存在于绑定正文中。"""
    normalized = normalize_world_rule_payload(payload, subject)
    if normalized is None or not isinstance(chapter_text, str):
        return False
    return normalized["evidence_quote"] in chapter_text


def world_rule_evidence_in_commit(
    project_root: str | Path,
    commit_payload: Any,
    event: Any,
) -> bool:
    """只信任与当前提交绑定正文逐字一致的世界规则证据。"""
    if not isinstance(commit_payload, dict) or not isinstance(event, dict):
        return False
    binding = commit_payload.get("chapter_binding")
    meta = commit_payload.get("meta")
    if not isinstance(binding, dict) or not isinstance(meta, dict):
        return False
    try:
        chapter = int(meta.get("chapter") or 0)
        binding_chapter = int(binding.get("chapter") or 0)
        expected_bytes = int(binding.get("bytes") or -1)
    except (TypeError, ValueError):
        return False
    relative = str(binding.get("path") or "").strip().replace("\\", "/")
    digest = str(binding.get("sha256") or "").strip().lower()
    pure = Path(relative)
    if (
        chapter <= 0
        or binding_chapter != chapter
        or not relative
        or pure.is_absolute()
        or ".." in pure.parts
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or expected_bytes <= 0
    ):
        return False
    root = Path(project_root).expanduser().resolve()
    target = (root / pure).resolve()
    try:
        target.relative_to(root)
        raw = target.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    if len(raw) != expected_bytes or hashlib.sha256(raw).hexdigest() != digest:
        return False
    return world_rule_evidence_in_chapter(
        event.get("payload"),
        event.get("subject"),
        text,
    )


def _strip_controls(value: Any) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _is_jailbreak(text: str) -> bool:
    return any(pattern.search(text) for pattern in _JAILBREAK_PATTERNS)


def normalize_author_text(value: Any, *, max_chars: int = 1200) -> str:
    """Keep author-owned wording. No style vocabulary, no jailbreak filter.

    设定集、init 输入和已接受 commit 是作者真源；插件不论文风，也不准
    因为句首「用/以/按」或子串「视角/题材」把事实整段丢掉。
    """
    text = _strip_controls(value)
    if not text:
        return ""
    return text[: max(1, int(max_chars))].strip()


def sanitize_fact_text(value: Any, *, max_chars: int = 1200) -> str:
    """Keep model free text except jailbreak sentences such as 「忽略合同」."""
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    fragments = re.split(r"(?:\r?\n)+|(?<=[。！？!?；;])", text)
    safe: list[str] = []
    for fragment in fragments:
        normalized = re.sub(r"\s+", " ", fragment).strip()
        if not normalized or _is_jailbreak(normalized):
            continue
        safe.append(normalized)
    return " ".join(safe)[: max(1, int(max_chars))].strip()


def sanitize_fact_atom(value: Any, *, max_chars: int = 80) -> str:
    """Accept a short data value. Jailbreak atoms are refused; style words are not."""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = _strip_controls(value)
    if not text or len(text) > max(1, int(max_chars)):
        return ""
    if any(mark in text for mark in "。！？!?；;"):
        return ""
    if not _FACT_ATOM.fullmatch(text):
        return ""
    if _is_jailbreak(text):
        return ""
    return text
