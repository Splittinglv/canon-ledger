#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import sys
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from run_behavior_evals import run_behavior_evals  # noqa: E402


def test_run_behavior_evals_fast_suite_passes_for_current_package():
    root = Path(__file__).resolve().parents[2]

    report = run_behavior_evals(root, suite="fast")

    assert report["ok"] is True
    assert report["total"] >= 5


def test_fast_behavior_eval_descriptions_use_chinese_sentences():
    root = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (root / "evals" / "fixtures" / "behavior" / "fast.json").read_text(
            encoding="utf-8"
        )
    )

    descriptions = [str(case.get("description") or "") for case in payload["cases"]]
    assert descriptions
    assert all(any("\u4e00" <= char <= "\u9fff" for char in text) for text in descriptions)
    for description in descriptions:
        for sentence in re.split(r"[。！？!?]+", description):
            has_english_phrase = re.search(r"[A-Za-z]{2,}\s+[A-Za-z]{2,}", sentence)
            has_chinese = any("\u4e00" <= char <= "\u9fff" for char in sentence)
            assert not (has_english_phrase and not has_chinese), description
