#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep retrieval text factual and discard embedded creative instructions."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


_CREATIVE_DIRECTIVE_PATTERNS = (
    # Explicit meta-writing vocabulary is not a story-world fact even when it
    # is phrased without an imperative verb.
    re.compile(
        r"(?:文风|写作风格|叙事风格|文笔|写作|写法|改写|续写|润色|口吻|"
        r"第一人称|第二人称|第三人称|人称|视角|POV|叙述者|叙事者|全知|限知|"
        r"时态|语态|本段|段落|句式|短句|长句|修辞|用词|措辞|旁白|内心独白|"
        r"心理描写|环境描写|对话比例|对白|对白比例|字数|篇幅|章节长度|"
        r"倒叙|插叙|顺叙|口语化|对话比例|环境细节|"
        r"笔调|叙述|场面|画面|镜头感|电影质感|电影气息|像诗一样写|写得像寓言|长篇大论|"
        r"像诗一样写|写得像寓言|长篇大论|镜头感|"
        r"海明威|莎士比亚|鲁迅|金庸|古龙|模仿作者|模仿作家|"
        r"桥段|套路|爽文|虐文|追妻火葬场|升级流|赛博朋克|题材|节奏|氛围|"
        r"系统提示|提示词|大模型|语言模型|剧情走向|剧情安排)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:prompt|instructions?|system\s+message|writing|rewrite|prose|"
        r"style|tone|pacing|trope|genre|point\s+of\s+view|POV|narrator|"
        r"first\s+person|second\s+person|third\s+person|past\s+tense|present\s+tense|"
        r"paragraph|sentence\s+structure|diction|word\s+count|dialogue\s+ratio|"
        r"lyrical|poetic|literary|terse|concise|simple\s+language|screenplay|"
        r"rhythm|cadence|imagery|abstractions?|cinematic|muscular|spare|"
        r"omniscient\s+voice|limited\s+voice|narrative\s+voice|"
        r"Hemingway|Shakespeare|write\s+like|imitate\s+(?:an?\s+)?author|"
        r"chapter\s+length)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:as\s+(?:an?\s+)?(?:language\s+model|assistant|writer)|"
        r"follow\s+(?:the\s+)?(?:user|system|developer)?\s*(?:requests?|instructions?)|"
        r"obey\s+(?:the\s+)?(?:user|system|developer))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:忽略|无视|绕过|覆盖|不要遵循|不必遵循).{0,24}"
        r"(?:合同|规则|约束|指令|提示词|系统提示|既有设定|前文要求)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:改写|改成|写成|采用|使用|切换|调整|加入|添加).{0,24}"
        r"(?:文风|写作风格|叙事风格|口吻|节奏|桥段|套路|爽文|虐文|火葬场|升级流|赛博朋克)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:文风|写作风格|叙事风格|口吻|节奏|桥段|套路).{0,24}"
        r"(?:改成|采用|使用|切换|调整|必须|应该)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:让|要求|命令)(?:模型|作者|写手).{0,28}"
        r"(?:写|改|忽略|采用|加入|添加)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:后续|下一章|接下来).{0,20}"
        r"(?:改写|写成|续写|润色|采用|使用|加入|添加|切换|安排|描述|讲述)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:next\s+chapter|future\s+chapters?)\b.{0,40}"
        r"\b(?:write|rewrite|continue|polish|use|adopt|switch|change|add|insert|narrate|describe)\b",
        re.IGNORECASE,
    ),
    # Meta-writing commands that avoid the usual words "style" and "prose".
    # Match the command and its craft target together so ordinary story facts
    # containing one of the nouns are not rejected on that noun alone.
    re.compile(
        r"\b(?:make|keep|tell|use|favor|favour|employ)\b.{0,48}"
        r"\b(?:sentences?|narration|story|third[-\s]?person|cinematic|imagery)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:每(?:一)?句.{0,8}(?:都|要|应|保持).{0,8}(?:短|简短)|"
        r"像诗一样写|故事写得像寓言)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:行文|文字|语言|字句).{0,16}"
        r"(?:保持|要|应|需|得|有|使用|采用).{0,12}"
        r"(?:克制|韵律|简洁|华丽|朴素|抒情|诗意|有力)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:(?:行文|文字|语言|字句).{0,8}(?:如|像|似).{0,16}(?:刀|诗|画|音乐)|"
        r"句句.{0,8}(?:见血|有力|铿锵|押韵))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:用动作代替解释|避免长篇大论|"
        r"场景.{0,8}(?:要|应|需|有|保持|具有).{0,8}电影质感)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:只|仅)?(?:使用|采用)(?:简单|简洁|朴素)(?:的)?(?:语言|文字|措辞)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:每(?:一)?(?:章|回|节|段|句)|(?:本|下|上)章|章节|"
        r"章(?:首|末|尾)|开场|开头|结尾|收尾|正文|段落|句子|"
        r"读者|作者|写手|模型|叙事|剧情|桥段|情节).{0,32}"
        r"(?:必须|应该|应当|需要|务必|总要|最好|都要|"
        r"出现|加入|添加|安排|设置|制造|保留|留下|写入|"
        r"描写|改写|揭示|呈现|反转|悬念|意外|钩子|爽点)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:write\s+in\s+clipped\s+fragments?|"
        r"favou?r\s+verbs?\s+over\s+adjectives?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:把|将|让|请|需要|务必|建议|可以|要).{0,36}"
        r"(?:提前呈现|提前揭示|留(?:下|一个)?悬念|改得|插入|增删|调整|强化|弱化)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ignore|disregard|override|do\s+not\s+follow)\b.{0,40}"
        r"\b(?:prompt|instruction|contract|rules?|previous|system|constraints?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:write|rewrite|continue|switch|change|use|adopt)\b.{0,40}"
        r"\b(?:style|tone|plot|pacing|trope|voice|genre)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[\s\"'`])(?:system|assistant|developer|user)\s*"
        r"(?:prompt|message)?\s*[:：]",
        re.IGNORECASE,
    ),
)

_IMPERATIVE_PREFIX = re.compile(
    r"^(?:请|务必|应当|应该|不要|避免|把本|将本|让|保持|"
    r"改为|改成|改用|采用|使用|加入|添加|插入|开头|开场|结尾|收尾|"
    r"每.{0,6}句|please\b|make\b|write\b|rewrite\b|use\b|adopt\b|"
    r"switch\b|change\b|insert\b|open\b|end\b|act\b|answer\b|follow\b|obey\b|"
    r"keep\b|tell\b|favor\b|favour\b|employ\b|render\b|choose\b|narrate\b|describe\b|"
    r"遵循|服从|执行|按照|按|用|以|回答|扮演|假装)",
    re.IGNORECASE,
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


def sanitize_fact_text(value: Any, *, max_chars: int = 1200) -> str:
    """Return declarative fragments while dropping creative-control text.

    Extracted summaries and event descriptions are data authored by a model or
    imported from an existing project. They can contain a valid fact followed
    by an instruction aimed at the next writer. Sentence-boundary filtering
    preserves the fact while refusing prose, plot, or contract instructions.
    """
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    fragments = re.split(r"(?:\r?\n)+|(?<=[。！？!?；;])", text)
    safe: list[str] = []
    for fragment in fragments:
        normalized = re.sub(r"\s+", " ", fragment).strip()
        if not normalized:
            continue
        if _IMPERATIVE_PREFIX.search(normalized):
            continue
        if any(pattern.search(normalized) for pattern in _CREATIVE_DIRECTIVE_PATTERNS):
            continue
        safe.append(normalized)
    return " ".join(safe)[: max(1, int(max_chars))].strip()


def sanitize_fact_atom(value: Any, *, max_chars: int = 80) -> str:
    """Accept a short data value, never an arbitrary sentence or instruction."""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text or len(text) > max(1, int(max_chars)):
        return ""
    if any(mark in text for mark in "。！？!?；;\n\r"):
        return ""
    if not _FACT_ATOM.fullmatch(text):
        return ""
    if _IMPERATIVE_PREFIX.search(text):
        return ""
    if any(pattern.search(text) for pattern in _CREATIVE_DIRECTIVE_PATTERNS):
        return ""
    return text
