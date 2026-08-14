#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CanonLedger 当前状态投影的只读查询入口。

章节事实只能通过绑定的 chapter commit 写入，再由投影写入器生成
``state.json`` 与 ``index.db``。本模块不提供直接章节写入或结构转换。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from runtime_compat import enable_windows_utf8_stdio

from .cli_args import normalize_global_project_root
from .cli_output import print_error, print_success
from .config import DataModulesConfig, get_config


REMOVED_STATE_FIELDS = {
    "entities_v3",
    "alias_index",
    "state_changes",
    "structured_relationships",
    "_migrated_to_sqlite",
}


class StateManager:
    """只读访问 CanonLedger 7 当前状态与索引投影。"""

    def __init__(self, config: DataModulesConfig | None = None):
        self.config = config or get_config()
        self._state: Dict[str, Any] = {}
        self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        path = Path(self.config.state_file)
        if not path.is_file():
            self._state = {}
            return self._state
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("state.json 不是有效的 UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("state.json 顶层必须是对象")
        removed = sorted(REMOVED_STATE_FIELDS.intersection(payload))
        if removed:
            raise ValueError(
                "state.json 含不受支持的字段：" + "、".join(removed)
            )
        self._state = payload
        return self._state

    def get_current_chapter(self) -> int:
        progress = self._state.get("progress")
        if not isinstance(progress, dict):
            return 0
        try:
            return max(0, int(progress.get("current_chapter") or 0))
        except (TypeError, ValueError):
            return 0

    def _index_manager(self):
        from .index_manager import IndexManager

        return IndexManager(self.config)

    def get_entity(
        self,
        entity_id: str,
        entity_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        entity = self._index_manager().get_entity(str(entity_id or "").strip())
        if entity_type and entity and entity.get("type") != entity_type:
            return None
        return entity

    @staticmethod
    def _keyed(entities: list[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return {
            str(entity.get("id")): entity
            for entity in entities
            if isinstance(entity, dict) and str(entity.get("id") or "").strip()
        }

    def get_entities_by_type(self, entity_type: str) -> Dict[str, Dict[str, Any]]:
        return self._keyed(
            self._index_manager().get_entities_by_type(entity_type)
        )

    def get_entities_by_tier(self, tier: str) -> Dict[str, Dict[str, Any]]:
        return self._keyed(self._index_manager().get_entities_by_tier(tier))

    def get_all_entities(self) -> Dict[str, Dict[str, Any]]:
        manager = self._index_manager()
        with manager._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM entities WHERE is_archived = 0 ORDER BY last_appearance DESC"
            ).fetchall()
        return self._keyed(
            [manager._row_to_dict(row, parse_json=["current_json"]) for row in rows]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="查询 CanonLedger 当前状态投影")
    parser.add_argument("--project-root", type=str, help="项目根目录")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("get-progress", help="读取当前进度投影")

    get_entity = commands.add_parser("get-entity", help="读取实体投影")
    get_entity.add_argument("--id", required=True)

    list_entities = commands.add_parser("list-entities", help="列出实体投影")
    list_entities.add_argument("--type", help="按类型过滤")
    list_entities.add_argument("--tier", help="按层级过滤")

    args = parser.parse_args(normalize_global_project_root(sys.argv[1:]))
    config = None
    if args.project_root:
        from project_locator import resolve_project_root

        try:
            config = DataModulesConfig.from_project_root(
                resolve_project_root(args.project_root)
            )
        except FileNotFoundError as exc:
            print_error(
                "INVALID_PROJECT_ROOT",
                str(exc),
                suggestion="请传入包含 .canon-ledger/state.json 的 CanonLedger 项目根目录。",
            )
            raise SystemExit(1) from exc

    try:
        manager = StateManager(config)
    except ValueError as exc:
        print_error("UNSUPPORTED_STATE_SCHEMA", str(exc))
        raise SystemExit(1) from exc

    if args.command == "get-progress":
        progress = manager._state.get("progress")
        print_success(progress if isinstance(progress, dict) else {}, message="progress")
        return
    if args.command == "get-entity":
        entity = manager.get_entity(args.id)
        if entity:
            print_success(entity, message="entity")
        else:
            print_error("NOT_FOUND", f"未找到实体：{args.id}")
            raise SystemExit(1)
        return
    if args.command == "list-entities":
        if args.type:
            entities = manager.get_entities_by_type(args.type)
        elif args.tier:
            entities = manager.get_entities_by_tier(args.tier)
        else:
            entities = manager.get_all_entities()
        print_success(
            [{"id": entity_id, **entity} for entity_id, entity in entities.items()],
            message="entities",
        )
        return

    print_error("UNKNOWN_COMMAND", "未指定有效命令", suggestion="请查看 --help")
    raise SystemExit(2)


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
