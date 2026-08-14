#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSV 数据校验工具。

基于 CSV_CONFIG 和 canonical genre 枚举校验 references/csv/ 下所有表的数据质量。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


sys.path.insert(0, str(Path(__file__).resolve().parent))
from reference_search import CSV_CONFIG, GENRE_CANONICAL, split_multi_value
from genre_taxonomy import default_taxonomy_path, load_genre_taxonomy


_CHINESE_COMMA_RE = re.compile(r"，")
_MULTI_VALUE_COLUMNS = ("适用技能", "关键词", "意图与同义词", "适用题材")
_VALID_SKILLS = {"init", "plan", "write", "review", "query", "learn", "dashboard", "story-system"}
_VALID_LEVELS = {"提醒", "缺陷补偿", "知识补充"}


def _split_multi_value(cell: str) -> List[str]:
    return split_multi_value(cell)


def _default_csv_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "csv"


def _read_csv(path: Path) -> tuple[List[str], List[Dict[str, Any]]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = list(reader.fieldnames or [])
    return headers, rows


def _validate_genre_taxonomy(errors: List[str], warnings: List[str]) -> None:
    taxonomy_path = default_taxonomy_path()
    if not taxonomy_path.exists():
        errors.append(f"[genre-index] 文件不存在: {taxonomy_path}")
        return
    try:
        taxonomy = load_genre_taxonomy(str(taxonomy_path))
    except Exception as exc:
        errors.append(f"[genre-index] 加载失败: {exc}")
        return

    for entry in taxonomy.entries:
        if entry.canonical_genre != "全部" and entry.canonical_genre not in GENRE_CANONICAL:
            errors.append(f"[genre-index] {entry.label} canonical_genre 不合法: {entry.canonical_genre}")


def validate(csv_dir: Path) -> Dict[str, List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    all_ids: Dict[str, str] = {}
    valid_genres = GENRE_CANONICAL | {"全部"}

    _validate_genre_taxonomy(errors, warnings)

    for table_name, config in CSV_CONFIG.items():
        csv_path = csv_dir / config["file"]
        if not csv_path.exists():
            errors.append(f"[{table_name}] 文件不存在: {config['file']}")
            continue

        headers, rows = _read_csv(csv_path)
        header_set = set(headers)
        prefix = str(config.get("prefix", "")).strip()
        required_cols = list(config.get("required_cols", []))

        declared_cols = set(config.get("search_cols", {}).keys())
        declared_cols.update(config.get("output_cols", []))
        declared_cols.update(required_cols)
        poison_col = str(config.get("poison_col", "")).strip()
        if poison_col:
            declared_cols.add(poison_col)

        missing_headers = declared_cols - header_set
        if missing_headers:
            joined = ", ".join(sorted(missing_headers))
            errors.append(f"[{table_name}] CSV 缺少列头: {joined}")

        for line_no, row in enumerate(rows, start=2):
            row_id = (row.get("编号") or "").strip()

            if None in row:
                extras = row.get(None) or []
                errors.append(
                    f"[{table_name}] 行{line_no} ({row_id or '无编号'}) 字段数超过表头: {extras}"
                )

            if row_id:
                if row_id in all_ids:
                    errors.append(
                        f"[{table_name}] 行{line_no} 编号 {row_id} 重复（首次出现于 {all_ids[row_id]}）"
                    )
                else:
                    all_ids[row_id] = table_name

            if prefix and row_id and not row_id.startswith(f"{prefix}-"):
                errors.append(f"[{table_name}] 行{line_no} 编号 {row_id} 应以 {prefix}- 开头")

            for col in required_cols:
                value = (row.get(col) or "").strip()
                if not value:
                    errors.append(f"[{table_name}] 行{line_no} ({row_id}) 必填列 {col} 为空")

            for col in _MULTI_VALUE_COLUMNS:
                value = row.get(col) or ""
                if _CHINESE_COMMA_RE.search(value):
                    errors.append(
                        f"[{table_name}] 行{line_no} ({row_id}) {col} 含中文逗号，应使用 |"
                    )

            skill_cell = (row.get("适用技能") or "").strip()
            if "适用技能" in header_set:
                skill_tokens = _split_multi_value(skill_cell)
                if not skill_tokens:
                    errors.append(f"[{table_name}] 行{line_no} ({row_id}) 适用技能为空")
                for skill in skill_tokens:
                    if skill not in _VALID_SKILLS:
                        errors.append(f"[{table_name}] 行{line_no} ({row_id}) 适用技能值 '{skill}' 不合法")

            if "层级" in header_set:
                level = (row.get("层级") or "").strip()
                if not level:
                    errors.append(f"[{table_name}] 行{line_no} ({row_id}) 层级为空")
                elif level not in _VALID_LEVELS:
                    errors.append(f"[{table_name}] 行{line_no} ({row_id}) 层级值 '{level}' 不合法")

            genre_cell = (row.get("适用题材") or "").strip()
            if genre_cell:
                for genre in _split_multi_value(genre_cell):
                    if genre not in valid_genres:
                        warnings.append(
                            f"[{table_name}] 行{line_no} ({row_id}) 适用题材值 '{genre}' 不在 canonical 枚举中"
                        )

    return {"errors": errors, "warnings": warnings}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate reference CSV files")
    parser.add_argument("--csv-dir", default=None, help="Override CSV directory")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    csv_dir = Path(args.csv_dir) if args.csv_dir else _default_csv_dir()
    result = validate(csv_dir)

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for error in result["errors"]:
            print(f"ERROR: {error}")
        for warning in result["warnings"]:
            print(f"WARN:  {warning}")
        print(f"\n--- {len(result['errors'])} error(s), {len(result['warnings'])} warning(s) ---")

    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
