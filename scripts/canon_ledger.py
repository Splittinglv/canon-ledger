#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""叙典 CanonLedger 统一 CLI 入口。

用法示例：
  python "<SCRIPTS_DIR>/canon_ledger.py" preflight
  python "<SCRIPTS_DIR>/canon_ledger.py" where
  python "<SCRIPTS_DIR>/canon_ledger.py" index stats

该入口负责设置插件脚本路径，并把命令转发给 CanonLedger 数据模块。
"""

from __future__ import annotations

import sys
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio


def main() -> None:
    scripts_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(scripts_dir))

    from data_modules.canon_ledger import main as _main

    _main()


if __name__ == "__main__":
    enable_windows_utf8_stdio(skip_in_pytest=True)
    main()
