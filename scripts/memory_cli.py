#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_cli.py — MemoryContract CLI 入口。

提供 load-context / query-entity / query-rules / read-summary /
get-open-loops / get-timeline 六个子命令，输出 JSON。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio


def _ensure_scripts_path() -> None:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_path()

from data_modules.config import DataModulesConfig
from data_modules.context_delivery import (
    ContextPageError, ContextPager, compact_context, paginate_context,
)
from data_modules.memory_contract_adapter import (
    CanonMemoryReadUnavailable,
    MemoryContractAdapter,
)


def _adapter(project_root: str) -> MemoryContractAdapter:
    cfg = DataModulesConfig.from_project_root(project_root)
    return MemoryContractAdapter(cfg)


def _json_out(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_load_context(args: argparse.Namespace) -> None:
    expected_output = Path(".canon-ledger/tmp/context_pages.json")
    if args.out:
        from security_utils import resolve_exact_project_role_path

        resolve_exact_project_role_path(
            args.project_root, args.out, expected_relative=expected_output
        )
    adapter = _adapter(args.project_root)
    if args.all_pages or args.paged:
        # Do not repeat optional remote retrieval on each page. All facts are
        # already in the exact Canon context; retrieval remains a separate aid.
        pack = adapter.load_context(
            args.chapter, budget_tokens=args.budget_tokens, include_retrieval=False,
            all_sections=args.all_pages,
        )
        if args.all_pages:
            result = ContextPager(
                compact_context(pack), budget_tokens=args.budget_tokens
            ).all_pages()
            if args.out:
                from security_utils import atomic_write_project_json_role

                atomic_write_project_json_role(
                    args.project_root, args.out, result, expected_relative=expected_output
                )
                result = {key: value for key, value in result.items() if key != "pages"}
                result["path"] = expected_output.as_posix()
            _json_out(result)
        else:
            _json_out(paginate_context(pack, budget_tokens=args.budget_tokens, cursor=args.cursor))
    else:
        pack = adapter.load_context(args.chapter, budget_tokens=args.budget_tokens)
        _json_out(pack.to_dict())


def cmd_query_entity(args: argparse.Namespace) -> None:
    adapter = _adapter(args.project_root)
    snap = adapter.query_entity(args.id, as_of_chapter=args.as_of_chapter)
    if snap is None:
        _json_out({"error": "not_found", "entity_id": args.id})
    else:
        _json_out(snap.to_dict())


def cmd_query_rules(args: argparse.Namespace) -> None:
    adapter = _adapter(args.project_root)
    rules = adapter.query_rules(
        domain=args.domain or "",
        as_of_chapter=args.as_of_chapter,
    )
    _json_out([r.to_dict() for r in rules])


def cmd_read_summary(args: argparse.Namespace) -> None:
    adapter = _adapter(args.project_root)
    text = adapter.read_summary(args.chapter)
    _json_out({"chapter": args.chapter, "summary": text})


def cmd_get_open_loops(args: argparse.Namespace) -> None:
    adapter = _adapter(args.project_root)
    loops = adapter.get_open_loops(
        status=args.status or "active",
        as_of_chapter=args.as_of_chapter,
    )
    _json_out([l.to_dict() for l in loops])


def cmd_get_obligations(args: argparse.Namespace) -> None:
    adapter = _adapter(args.project_root)
    obligations = adapter.get_lifecycle_obligations(
        status=args.status or "active",
        as_of_chapter=args.as_of_chapter,
    )
    _json_out([item.to_dict() for item in obligations])


def cmd_export_asof(args: argparse.Namespace) -> None:
    expected_output = Path(".canon-ledger") / "tmp" / "asof_snapshot.json"
    if args.out:
        # Validate the complete output capability before loading any runtime
        # store.  An unsafe target must never be masked by an unrelated project
        # health error, and direct memory_cli.py invocation remains fail-closed.
        from security_utils import resolve_exact_project_role_path

        resolve_exact_project_role_path(
            args.project_root,
            args.out,
            expected_relative=expected_output,
        )
    adapter = _adapter(args.project_root)
    snapshot = adapter.export_asof_snapshot(
        chapter=args.chapter,
        as_of_chapter=args.as_of_chapter,
    )
    if args.out:
        from security_utils import atomic_write_project_json_role

        atomic_write_project_json_role(
            args.project_root,
            args.out,
            snapshot,
            expected_relative=expected_output,
        )
    _json_out(snapshot)


def cmd_get_timeline(args: argparse.Namespace) -> None:
    adapter = _adapter(args.project_root)
    events = adapter.get_timeline(
        args.from_ch,
        args.to_ch,
        as_of_chapter=args.as_of_chapter,
    )
    _json_out([e.to_dict() for e in events])


def main() -> None:
    parser = argparse.ArgumentParser(description="MemoryContract CLI")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    sub = parser.add_subparsers(dest="command")

    p_load = sub.add_parser("load-context", help="加载章节上下文基础包")
    p_load.add_argument("--chapter", type=int, required=True)
    p_load.add_argument(
        "--budget-tokens",
        type=int,
        default=4000,
        help="上下文交付目标预算；超限不代表事实缺失，硬事实不裁剪",
    )
    p_load.add_argument("--paged", action="store_true", help="按同一版本分页读取全部上下文")
    p_load.add_argument("--cursor", default=None, help="原样使用上一页 next_cursor；必须配合 --paged")
    p_load.add_argument("--all-pages", action="store_true", help="一次读取，生成全部精简上下文页")
    p_load.add_argument("--out", default="", help="仅 --all-pages 可写 .canon-ledger/tmp/context_pages.json")

    p_entity = sub.add_parser("query-entity", help="查询实体快照")
    p_entity.add_argument("--id", required=True, help="实体 ID")
    p_entity.add_argument(
        "--as-of-chapter", type=int, default=None, help="只读取截至该章的事实"
    )

    p_rules = sub.add_parser("query-rules", help="查询世界规则")
    p_rules.add_argument("--domain", default="", help="按 domain 过滤")
    p_rules.add_argument(
        "--as-of-chapter", type=int, default=None, help="只读取截至该章的事实"
    )

    p_summary = sub.add_parser("read-summary", help="读取章节摘要")
    p_summary.add_argument("--chapter", type=int, required=True)

    p_loops = sub.add_parser("get-open-loops", help="查询未闭合伏笔")
    p_loops.add_argument("--status", default="active", help="状态过滤")
    p_loops.add_argument(
        "--as-of-chapter", type=int, default=None, help="按该章时点计算闭合状态"
    )

    p_obligations = sub.add_parser(
        "get-obligations", help="查询可由闭环事件引用的稳定伏笔/承诺 ID"
    )
    p_obligations.add_argument("--status", default="active", help="状态过滤")
    p_obligations.add_argument(
        "--as-of-chapter", type=int, default=None, help="按该章时点计算闭合状态"
    )

    p_timeline = sub.add_parser("get-timeline", help="查询时间线事件")
    p_timeline.add_argument("--from", type=int, required=True, dest="from_ch", help="起始章节")
    p_timeline.add_argument("--to", type=int, required=True, dest="to_ch", help="结束章节")
    p_timeline.add_argument(
        "--as-of-chapter", type=int, default=None, help="禁止读取该章之后的事实"
    )

    p_asof = sub.add_parser(
        "export-asof",
        help="导出截至第 N-1 章的不可变事实快照",
    )
    p_asof.add_argument("--chapter", type=int, default=None, help="正在审查/抽取的章节号")
    p_asof.add_argument(
        "--as-of-chapter",
        type=int,
        default=None,
        help="直接指定截止章节；缺省为 chapter-1",
    )
    p_asof.add_argument(
        "--out",
        default="",
        help="可选：仅可写入 .canon-ledger/tmp/asof_snapshot.json",
    )

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    if args.command == "load-context":
        if args.all_pages and args.cursor is not None:
            parser.error("--all-pages cannot use --cursor")
        if args.out and not args.all_pages:
            parser.error("--out requires --all-pages")
        if args.cursor is not None and not args.paged:
            parser.error("--cursor requires --paged")
        if args.budget_tokens < 1:
            parser.error("--budget-tokens must be positive")

    dispatch = {
        "load-context": cmd_load_context,
        "query-entity": cmd_query_entity,
        "query-rules": cmd_query_rules,
        "read-summary": cmd_read_summary,
        "get-open-loops": cmd_get_open_loops,
        "get-obligations": cmd_get_obligations,
        "get-timeline": cmd_get_timeline,
        "export-asof": cmd_export_asof,
    }
    if args.command == "export-asof":
        if args.chapter is None and args.as_of_chapter is None:
            parser.error("export-asof 需要 --chapter 或 --as-of-chapter")
    try:
        dispatch[args.command](args)
    except (CanonMemoryReadUnavailable, ContextPageError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "authority": "canon_v3",
                    "usable_for_writing": False,
                    "replacement": "canon_ledger.py canon-v3 status",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
