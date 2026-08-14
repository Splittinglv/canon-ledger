#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

from chapter_outline_loader import volume_num_for_chapter_from_state
from .consistency_context import sanitize_initial_canon, sanitize_story_contracts
from .fact_text import normalize_author_text, sanitize_fact_atom

try:
    from security_utils import atomic_write_json
except ImportError:  # pragma: no cover
    from scripts.security_utils import atomic_write_json


MARKER_BEGIN = "<!-- STORY-SYSTEM:BEGIN -->"
MARKER_END = "<!-- STORY-SYSTEM:END -->"
SETTING_CANON_SCHEMA_VERSION = "setting-canon/v1"
_SETTING_FACT_LIMIT = 2000
_SETTING_SOURCE_LIMIT = 256
_SETTING_VALUE_LIMIT = 1200
_SETTING_SOURCE_SIZE_LIMIT = 2 * 1024 * 1024
_SETTING_ID_RE = re.compile(r"^setting-[0-9a-f]{20}$")
_SETTING_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SETTING_LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s+)(.+?)\s*$")
_SETTING_LABELED_RE = re.compile(
    r"^\s*(?:[-*+]\s+|\d+[.)、]\s+)(.{1,120}?)[：:]\s*(.*?)\s*$"
)
# 只拦章法处方词。世界内事实里的「节奏 / 氛围 / 反转」由
# `_SETTING_META_CONTROL_RE` 在「每章必须…」这类控制句里处理，不能当子串误杀。
_SETTING_CRAFT_RE = re.compile(
    r"(?:文风|文笔|写作|写法|风格|口吻|语气|笔调|行文|句式|段落|"
    r"叙事视角|镜头|修辞|韵律|提示词|技巧|套路|桥段|爽点|"
    r"读者|短句|长句|旁白|核心卖点|镜像对抗)"
)
_SETTING_META_CONTROL_RE = re.compile(
    r"(?:每章|每一章|章首|章末|每段|每句|开场|收尾|结尾).{0,24}"
    r"(?:必须|需要|应该|应当|务必|采用|出现|加入|设置|安排|制造|反转)"
)
_SETTING_HARD_RE = re.compile(
    r"(?:硬约束|硬限制|不可违背|禁止事项|禁忌|限制|规则|公理|底线|"
    r"代价|冷却|边界|条件)"
)
_SETTING_CHARACTER_FILE_RE = re.compile(r"(?:主角|女主|角色|人物|反派|对立)")
_SETTING_PLACEHOLDER_RE = re.compile(
    r"(?:\{[^{}]+\}|\[待[^\]]*\]|待填写|待补充|暂名|尚无|用一句话说明|"
    r"在此填写|列出从弱到强|后续自定义|主角[A-Z甲乙丙丁](?:\b|$))"
)
_SETTING_REFERENCE_SECTION_RE = re.compile(r"(?:常见.+模板|示例|仅供参考|写作提示)")
_SETTING_REFERENCE_FIELD_RE = re.compile(r"^(?:详见|参考|示例)$")


@dataclass(frozen=True)
class StoryContractPaths:
    project_root: Path

    @classmethod
    def from_project_root(cls, project_root: str | Path) -> "StoryContractPaths":
        return cls(Path(project_root).expanduser().resolve())

    @property
    def root(self) -> Path:
        return self.project_root / ".story-system"

    @property
    def chapters_dir(self) -> Path:
        return self.root / "chapters"

    @property
    def volumes_dir(self) -> Path:
        return self.root / "volumes"

    @property
    def reviews_dir(self) -> Path:
        return self.root / "reviews"

    @property
    def commits_dir(self) -> Path:
        return self.root / "commits"

    @property
    def events_dir(self) -> Path:
        return self.root / "events"

    @property
    def master_json(self) -> Path:
        return self.root / "MASTER_SETTING.json"

    @property
    def anti_patterns_json(self) -> Path:
        return self.root / "anti_patterns.json"

    def chapter_json(self, chapter: int) -> Path:
        return self.chapters_dir / f"chapter_{chapter:03d}.json"

    def volume_json(self, volume: int) -> Path:
        return self.volumes_dir / f"volume_{volume:03d}.json"

    def review_json(self, chapter: int) -> Path:
        return self.reviews_dir / f"chapter_{chapter:03d}.review.json"

    def commit_json(self, chapter: int) -> Path:
        return self.commits_dir / f"chapter_{chapter:03d}.commit.json"

    def event_json(self, chapter: int) -> Path:
        return self.events_dir / f"chapter_{chapter:03d}.events.json"


def _setting_is_craft(*parts: Any) -> bool:
    text = " ".join(str(part or "") for part in parts)
    return bool(_SETTING_CRAFT_RE.search(text) or _SETTING_META_CONTROL_RE.search(text))


def _setting_is_placeholder(value: str) -> bool:
    text = str(value or "").strip()
    if not text or _SETTING_PLACEHOLDER_RE.search(text):
        return True
    if text.startswith(("（", "(")) and text.endswith(("）", ")")):
        inner = text[1:-1]
        if any(token in inner for token in ("/", "可选", "填写", "说明", "调整")):
            return True
    return False


def _setting_source_files(project_root: Path) -> List[Path]:
    root = Path(project_root).expanduser().resolve()
    settings_root = root / "设定集"
    if not settings_root.is_dir():
        return []
    paths: List[Path] = []
    for path in sorted(settings_root.rglob("*.md")):
        if path.is_symlink():
            raise ValueError(f"设定集同步拒绝符号链接：{path.relative_to(root).as_posix()}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if _setting_is_craft(relative, path.stem):
            continue
        paths.append(path)
    if len(paths) > _SETTING_SOURCE_LIMIT:
        raise ValueError(f"设定集文件超过上限：最多 {_SETTING_SOURCE_LIMIT} 个 Markdown 文件")
    return paths


def _clean_setting_value(raw: Any, *, source: str, line: int) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if _setting_is_placeholder(text) or _setting_is_craft(text):
        return ""
    if len(text) > _SETTING_VALUE_LIMIT:
        raise ValueError(f"设定事实过长，无法安全同步：{source}:{line}")
    cleaned = normalize_author_text(text, max_chars=_SETTING_VALUE_LIMIT)
    if text and not cleaned:
        raise ValueError(f"设定事实无法安全结构化：{source}:{line}")
    return cleaned


def _setting_fact(
    *,
    source: str,
    line: int,
    file_stem: str,
    section: str,
    subject: str,
    field: str,
    value: str,
) -> Dict[str, Any] | None:
    if _setting_is_craft(source, section, field, value):
        return None
    cleaned_value = _clean_setting_value(value, source=source, line=line)
    if not cleaned_value:
        return None
    cleaned_section = sanitize_fact_atom(section or file_stem, max_chars=160).strip()
    cleaned_subject = sanitize_fact_atom(subject or cleaned_section or file_stem, max_chars=160).strip()
    cleaned_field = sanitize_fact_atom(field or "事实", max_chars=160).strip()
    if not cleaned_section or not cleaned_subject or not cleaned_field:
        raise ValueError(f"设定事实字段无法安全结构化：{source}:{line}")
    identity = "\x00".join(
        (source, cleaned_section, cleaned_subject, cleaned_field, cleaned_value)
    )
    fact_id = f"setting-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
    scope_text = " ".join((cleaned_section, cleaned_field))
    if _SETTING_HARD_RE.search(scope_text):
        category = "world_rule"
    elif _SETTING_CHARACTER_FILE_RE.search(file_stem):
        category = "character_state"
    else:
        category = "story_fact"
    return {
        "id": fact_id,
        "category": category,
        "subject": cleaned_subject,
        "field": cleaned_field,
        "value": cleaned_value,
        "source": source,
        "section": cleaned_section,
        "line": int(line),
    }


def _table_cells(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_separator(cells: List[str]) -> bool:
    return bool(cells) and all(bool(re.fullmatch(r":?-{3,}:?", cell)) for cell in cells)


def _template_setting_lines(path: Path) -> set[str]:
    """返回同名内置模板的原始行，未填写的模板内容不提升为 canon。"""
    template = (
        Path(__file__).resolve().parents[2]
        / "templates"
        / "output"
        / f"设定集-{path.name}"
    )
    if not template.is_file():
        return set()
    try:
        return {
            line.strip()
            for line in template.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    except (OSError, UnicodeDecodeError):
        return set()


def _extract_setting_facts(path: Path, project_root: Path) -> List[Dict[str, Any]]:
    source = path.relative_to(project_root).as_posix()
    raw_bytes = path.read_bytes()
    if len(raw_bytes) > _SETTING_SOURCE_SIZE_LIMIT:
        raise ValueError(f"设定集文件过大，无法同步：{source}")
    try:
        lines = raw_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"设定集文件不是 UTF-8：{source}") from exc

    facts: List[Dict[str, Any]] = []
    section = path.stem
    table_headers: List[str] | None = None
    pending_headers: List[str] | None = None
    in_comment = False
    template_lines = _template_setting_lines(path)
    for line_no, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if stripped.startswith("<!--"):
            in_comment = "-->" not in stripped
            continue
        if not stripped or stripped == "---":
            continue
        if stripped in template_lines and not stripped.startswith(("#", "|")):
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            section = heading or path.stem
            table_headers = None
            pending_headers = None
            continue
        if stripped.startswith(">"):
            # 模板说明和项目元信息使用引用块；它们不是结构化设定事实。
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = _table_cells(stripped)
            if _is_table_separator(cells):
                table_headers = pending_headers
                pending_headers = None
                continue
            if table_headers is None:
                pending_headers = cells
                continue
            if len(cells) != len(table_headers):
                raise ValueError(f"设定表格列数不一致：{source}:{line_no}")
            if cells and _setting_is_placeholder(cells[0]):
                continue
            subject = next(
                (
                    value
                    for value in cells
                    if value and not _setting_is_placeholder(value)
                ),
                section,
            )
            for header, value in zip(table_headers, cells):
                fact = _setting_fact(
                    source=source,
                    line=line_no,
                    file_stem=path.stem,
                    section=section,
                    subject=subject,
                    field=header,
                    value=value,
                )
                if fact is not None:
                    facts.append(fact)
            continue

        labeled = _SETTING_LABELED_RE.match(raw_line)
        if labeled:
            field, value = labeled.groups()
            if _SETTING_REFERENCE_FIELD_RE.fullmatch(field.strip()):
                continue
            fact = _setting_fact(
                source=source,
                line=line_no,
                file_stem=path.stem,
                section=section,
                subject=section,
                field=field,
                value=value,
            )
            if fact is not None:
                facts.append(fact)
            continue
        listed = _SETTING_LIST_RE.match(raw_line)
        if listed:
            value = listed.group(1)
            fact = _setting_fact(
                source=source,
                line=line_no,
                file_stem=path.stem,
                section=section,
                subject=section,
                field="事实",
                value=value,
            )
            if fact is not None:
                facts.append(fact)
            continue
        if _SETTING_REFERENCE_SECTION_RE.search(section) or _setting_is_craft(section, stripped):
            continue
        if _setting_is_placeholder(stripped):
            continue
        raise ValueError(
            f"设定事实必须写成“字段：值”、项目符号或 Markdown 表格：{source}:{line_no}"
        )

    unique: Dict[str, Dict[str, Any]] = {}
    for fact in facts:
        unique.setdefault(str(fact["id"]), fact)
    return list(unique.values())


def build_setting_canon(project_root: Path) -> Dict[str, Any]:
    """从设定集构建闭合、可校验且不含创作技法的事实快照。"""
    root = Path(project_root).expanduser().resolve()
    sources: List[Dict[str, Any]] = []
    facts: List[Dict[str, Any]] = []
    for path in _setting_source_files(root):
        raw = path.read_bytes()
        source = path.relative_to(root).as_posix()
        sources.append(
            {
                "path": source,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        )
        facts.extend(_extract_setting_facts(path, root))
        if len(facts) > _SETTING_FACT_LIMIT:
            raise ValueError(f"设定事实超过上限：最多 {_SETTING_FACT_LIMIT} 条")
    facts.sort(key=lambda row: (row["source"], int(row["line"]), row["id"]))
    return {
        "schema_version": SETTING_CANON_SCHEMA_VERSION,
        "sources": sources,
        "facts": facts,
    }


def sanitize_setting_canon(value: Any) -> Dict[str, Any]:
    """只接受由设定同步器生成的闭合快照结构。"""
    if not isinstance(value, dict) or value.get("schema_version") != SETTING_CANON_SCHEMA_VERSION:
        return {}
    if set(value) != {"schema_version", "sources", "facts"}:
        return {}
    raw_sources = value.get("sources")
    raw_facts = value.get("facts")
    if not isinstance(raw_sources, list) or not isinstance(raw_facts, list):
        return {}
    if len(raw_sources) > _SETTING_SOURCE_LIMIT or len(raw_facts) > _SETTING_FACT_LIMIT:
        return {}

    sources: List[Dict[str, Any]] = []
    source_paths: set[str] = set()
    for row in raw_sources:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "bytes"}:
            return {}
        path = str(row.get("path") or "")
        digest = str(row.get("sha256") or "")
        size = row.get("bytes")
        pure = Path(path)
        if (
            not path.startswith("设定集/")
            or pure.is_absolute()
            or ".." in pure.parts
            or pure.suffix.lower() != ".md"
            or _setting_is_craft(path)
            or not _SETTING_HASH_RE.fullmatch(digest)
            or type(size) is not int
            or size < 0
            or size > _SETTING_SOURCE_SIZE_LIMIT
            or path in source_paths
        ):
            return {}
        source_paths.add(path)
        sources.append({"path": path, "sha256": digest, "bytes": size})

    facts: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    allowed_categories = {"world_rule", "story_fact", "character_state"}
    required_fact_fields = {
        "id", "category", "subject", "field", "value", "source", "section", "line"
    }
    for row in raw_facts:
        if not isinstance(row, dict) or set(row) != required_fact_fields:
            return {}
        fact_id = str(row.get("id") or "")
        category = str(row.get("category") or "")
        source = str(row.get("source") or "")
        line = row.get("line")
        if (
            not _SETTING_ID_RE.fullmatch(fact_id)
            or fact_id in seen_ids
            or category not in allowed_categories
            or source not in source_paths
            or type(line) is not int
            or line <= 0
        ):
            return {}
        subject = sanitize_fact_atom(row.get("subject"), max_chars=160).strip()
        field = sanitize_fact_atom(row.get("field"), max_chars=160).strip()
        section = sanitize_fact_atom(row.get("section"), max_chars=160).strip()
        raw_value = str(row.get("value") or "")
        cleaned_value = normalize_author_text(raw_value, max_chars=_SETTING_VALUE_LIMIT)
        if (
            not subject
            or not field
            or not section
            or not cleaned_value
            or cleaned_value != raw_value
            or _setting_is_craft(source, section, field, cleaned_value)
        ):
            return {}
        seen_ids.add(fact_id)
        facts.append(
            {
                "id": fact_id,
                "category": category,
                "subject": subject,
                "field": field,
                "value": cleaned_value,
                "source": source,
                "section": section,
                "line": line,
            }
        )
    return {
        "schema_version": SETTING_CANON_SCHEMA_VERSION,
        "sources": sources,
        "facts": facts,
    }


def verify_setting_canon(project_root: Path, value: Any) -> tuple[bool, str]:
    """校验设定事实快照与当前设定文件仍完全一致。"""
    try:
        current = build_setting_canon(project_root)
    except (OSError, ValueError):
        return False, "invalid_setting_canon_source"
    if not current["sources"] and not value:
        return True, ""
    if current["sources"] and not value:
        return False, "missing_setting_canon"
    cleaned = sanitize_setting_canon(value)
    if not cleaned or cleaned != value:
        return False, "invalid_setting_canon"
    if cleaned["sources"] != current["sources"]:
        return False, "stale_setting_canon"
    if cleaned["facts"] != current["facts"]:
        return False, "invalid_setting_canon"
    return True, ""


def synchronize_setting_canon(project_root: Path) -> Dict[str, Any]:
    """把当前设定集事实快照原子写入 MASTER_SETTING。"""
    paths = StoryContractPaths.from_project_root(project_root)
    master = read_json_if_exists(paths.master_json)
    if not isinstance(master, dict) or not master:
        raise ValueError("缺少有效的 MASTER_SETTING，无法同步设定事实")
    setting_canon = build_setting_canon(paths.project_root)
    master["setting_canon"] = setting_canon
    write_json(paths.master_json, master)
    return setting_canon


def _merge_append_only(master: Dict[str, Any], chapter: Dict[str, Any]) -> Dict[str, List[Any]]:
    merged: Dict[str, List[Any]] = {}
    for key in set(master) | set(chapter):
        seen: List[Any] = []
        for source_list in (master.get(key) or [], chapter.get(key) or []):
            for item in source_list:
                if item not in seen:
                    seen.append(item)
        merged[key] = seen
    return merged


def merge_contract_layers(master: Dict[str, Any], chapter: Dict[str, Any] | None) -> Dict[str, Any]:
    chapter = chapter or {}
    return {
        "locked": dict(master.get("locked") or {}),
        "append_only": _merge_append_only(
            master.get("append_only") or {},
            chapter.get("append_only") or {},
        ),
        "override_allowed": {
            **(master.get("override_allowed") or {}),
            **(chapter.get("override_allowed") or {}),
        },
    }


def merge_anti_patterns(*groups: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    merged: List[Dict[str, Any]] = []
    for group in groups:
        for row in group:
            text = str(row.get("text") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(dict(row))
    return merged


def read_json_if_exists(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Bad JSON in {path}") from exc


def write_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload, backup=True)


def write_marked_markdown(path: Path, generated_block: str) -> None:
    wrapped = f"{MARKER_BEGIN}\n{generated_block.rstrip()}\n{MARKER_END}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current.count(MARKER_BEGIN) > 1 or current.count(MARKER_END) > 1:
            raise ValueError(f"{path} contains multiple STORY-SYSTEM markers")
        if MARKER_BEGIN in current and MARKER_END in current:
            before, _, rest = current.partition(MARKER_BEGIN)
            _, _, after = rest.partition(MARKER_END)
            path.write_text(f"{before}{wrapped}{after.lstrip()}", encoding="utf-8")
            return
    path.write_text(wrapped, encoding="utf-8")


def render_master_markdown(master_payload: Dict[str, Any]) -> str:
    route = master_payload.get("route") or {}
    return "\n".join(
        [
            "# MASTER_SETTING",
            f"- 题材：{route.get('primary_genre', '')}",
        ]
    )


def render_anti_patterns_markdown(anti_patterns: List[Dict[str, Any]]) -> str:
    lines = ["# ANTI_PATTERNS"]
    for row in anti_patterns:
        lines.append(f"- {row.get('text', '')}")
    return "\n".join(lines)


def render_chapter_markdown(chapter_payload: Dict[str, Any]) -> str:
    directive = chapter_payload.get("chapter_directive") or {}
    focus = directive.get("goal") or (
        chapter_payload.get("override_allowed") or {}
    ).get("chapter_focus", "")
    return "\n".join(
        [
            f"# CHAPTER_{int(chapter_payload['meta']['chapter']):03d}",
            f"- 章节焦点：{focus}",
        ]
    )


def persist_story_seed(
    project_root: Path,
    master_payload: Dict[str, Any],
    chapter_payload: Dict[str, Any] | None,
    anti_patterns: List[Dict[str, Any]],
) -> None:
    # Story System may be regenerated many times.  The author-owned facts
    # captured by project initialization are append-preserved independently
    # from routing/table output so a later seed refresh cannot erase them.
    paths = StoryContractPaths.from_project_root(project_root)
    existing_master = read_json_if_exists(paths.master_json) or {}
    initial_canon = sanitize_initial_canon(
        (master_payload or {}).get("initial_canon")
        or (existing_master or {}).get("initial_canon")
    )
    cleaned = sanitize_story_contracts(
        {
            "master_setting": master_payload,
            "chapter_brief": chapter_payload,
            "anti_patterns": anti_patterns,
        }
    )
    master_payload = cleaned.get("master_setting") or master_payload
    if initial_canon:
        master_payload["initial_canon"] = initial_canon
    master_payload["setting_canon"] = build_setting_canon(paths.project_root)
    chapter_payload = cleaned.get("chapter_brief") if chapter_payload is not None else None
    anti_patterns = cleaned.get("anti_patterns") or []
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.chapters_dir.mkdir(parents=True, exist_ok=True)
    write_json(paths.master_json, master_payload)
    write_json(paths.anti_patterns_json, anti_patterns)
    write_marked_markdown(paths.master_json.with_suffix(".md"), render_master_markdown(master_payload))
    write_marked_markdown(
        paths.anti_patterns_json.with_suffix(".md"),
        render_anti_patterns_markdown(anti_patterns),
    )
    if chapter_payload is not None:
        chapter_num = int(chapter_payload["meta"]["chapter"])
        write_json(paths.chapter_json(chapter_num), chapter_payload)
        write_marked_markdown(
            paths.chapter_json(chapter_num).with_suffix(".md"),
            render_chapter_markdown(chapter_payload),
        )


def persist_runtime_contracts(
    project_root: Path,
    chapter: int,
    volume_brief: Dict[str, Any],
    review_contract: Dict[str, Any],
) -> None:
    paths = StoryContractPaths.from_project_root(project_root)
    # emit-runtime-contracts 也可能被独立调用；无论入口如何，运行时合同
    # 都必须与刚写回的设定集绑定到同一份可验证快照。
    synchronize_setting_canon(paths.project_root)
    chapter_brief = read_json_if_exists(paths.chapter_json(chapter)) or {}
    cleaned = sanitize_story_contracts(
        {
            "chapter_brief": chapter_brief,
            "volume_brief": volume_brief,
            "review_contract": review_contract,
        }
    )
    volume_brief = cleaned.get("volume_brief") or volume_brief
    review_contract = cleaned.get("review_contract") or review_contract
    volume = volume_num_for_chapter_from_state(paths.project_root, chapter) or 1
    paths.volumes_dir.mkdir(parents=True, exist_ok=True)
    paths.reviews_dir.mkdir(parents=True, exist_ok=True)
    write_json(paths.volume_json(volume), volume_brief)
    write_json(paths.review_json(chapter), review_contract)
