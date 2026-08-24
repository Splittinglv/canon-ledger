#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys

from runtime_compat import enable_windows_utf8_stdio


def main() -> None:
    parser = argparse.ArgumentParser(description="Story events CLI")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--chapter", type=int, default=0)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    replacement = (
        "canon_ledger.py canon-v3 status"
        if args.health
        else "canon_ledger.py canon-v3 query snapshot"
    )
    print(
        json.dumps(
            {
                "ok": False,
                "error": "canon_v3_story_events_public_read_retired",
                "message": (
                    "story-events 是无 HEAD 绑定的 legacy 事件读取面，已从生产闭集退役；"
                    "请使用 Canon v3 HEAD-bound 查询。"
                ),
                "replacement": replacement,
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
