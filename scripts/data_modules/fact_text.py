#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep retrieval text factual and discard embedded creative instructions."""
from __future__ import annotations

import re
from typing import Any


_CREATIVE_DIRECTIVE_PATTERNS = (
    # Explicit meta-writing vocabulary is not a story-world fact even when it
    # is phrased without an imperative verb.
    re.compile(
        r"(?:文风|风格|文笔|写作|写法|改写|续写|润色|叙事|叙述|口吻|语气|"
        r"第一人称|第二人称|第三人称|人称|视角|POV|叙述者|叙事者|全知|限知|"
        r"时态|语态|本段|段落|句式|短句|长句|修辞|用词|措辞|旁白|内心独白|"
        r"心理描写|环境描写|对话比例|对白比例|字数|篇幅|章节长度|"
        r"倒叙|插叙|顺叙|开场|收尾|结尾|对白|台词|口语化|环境细节|"
        r"桥段|套路|爽文|虐文|追妻火葬场|升级流|赛博朋克|题材|节奏|氛围|"
        r"系统提示|提示词|大模型|语言模型|剧情走向|剧情安排|"
        r"下一章|后续章节|接下来的章节)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:prompt|instructions?|system\s+message|writing|rewrite|prose|"
        r"style|tone|pacing|trope|genre|point\s+of\s+view|POV|narrator|"
        r"first\s+person|second\s+person|third\s+person|past\s+tense|present\s+tense|"
        r"paragraph|sentence\s+structure|diction|word\s+count|dialogue\s+ratio|"
        r"chapter\s+length|next\s+chapter|future\s+chapters?)\b",
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
        r"(?:改写|写成|采用|加入|添加|切换)",
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
    r"^(?:请|务必|应当|应该|不要|避免|把本|将本|让模型|让作者|"
    r"改为|改成|改用|采用|使用|加入|添加|插入|开头|开场|结尾|收尾|"
    r"每.{0,6}句|please\b|make\b|write\b|rewrite\b|use\b|adopt\b|"
    r"switch\b|change\b|insert\b|open\b|end\b|act\b|answer\b|follow\b|obey\b|"
    r"遵循|服从|执行|按照|按|用|以|回答|扮演|假装)",
    re.IGNORECASE,
)

_FACT_ATOM = re.compile(r"^[\w\u4e00-\u9fff·.()（）:/\- ]+$", re.UNICODE)


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
