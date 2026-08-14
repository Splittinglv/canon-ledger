#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_content_binding import ChapterBindingError

ARTIFACT_FIELDS = (
    "review_result",
    "fulfillment_result",
    "disambiguation_result",
    "extraction_result",
)


def _read_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _artifacts_from_last_commit(project_root: Path, chapter: int) -> dict[str, dict]:
    """Replay inputs from the persisted commit envelope of this chapter.

    The envelope keeps all four source artifacts verbatim, so a later human
    decision can be re-applied without re-running extraction. The manuscript
    binding is still verified by ``build_commit``; edited prose fails closed.
    """
    commit_path = (
        project_root
        / ".story-system"
        / "commits"
        / f"chapter_{chapter:03d}.commit.json"
    )
    if not commit_path.is_file():
        raise SystemExit(
            f"错误：未找到第 {chapter} 章的提交文件（{commit_path}）。"
            "请先运行 /canon-ledger-write 完成一次完整提交。"
        )
    try:
        commit = json.loads(commit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"错误：第 {chapter} 章提交文件无法解析：{exc}") from exc
    if not isinstance(commit, dict):
        raise SystemExit(f"错误：第 {chapter} 章提交文件不是有效的提交结构。")
    artifacts: dict[str, dict] = {}
    for field in ARTIFACT_FIELDS:
        artifact = commit.get(field)
        if not isinstance(artifact, dict):
            raise SystemExit(
                f"错误：第 {chapter} 章提交文件缺少 {field}，无法重放。"
                "请重新运行 /canon-ledger-write。"
            )
        artifacts[field] = artifact
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Chapter commit CLI")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--review-result", default="")
    parser.add_argument("--fulfillment-result", default="")
    parser.add_argument("--disambiguation-result", default="")
    parser.add_argument("--extraction-result", default="")
    parser.add_argument(
        "--from-last-commit",
        action="store_true",
        help="从本章上次的提交文件重放四份 artifact（用于人工裁决后重新提交）",
    )
    args = parser.parse_args()

    artifact_paths = {
        "review_result": args.review_result,
        "fulfillment_result": args.fulfillment_result,
        "disambiguation_result": args.disambiguation_result,
        "extraction_result": args.extraction_result,
    }
    if args.from_last_commit:
        if any(artifact_paths.values()):
            parser.error("--from-last-commit 与四个 artifact 路径参数互斥")
        artifacts = _artifacts_from_last_commit(
            Path(args.project_root), args.chapter
        )
    else:
        missing = [name for name, value in artifact_paths.items() if not value]
        if missing:
            parser.error(
                "缺少 artifact 路径参数："
                + "、".join(f"--{name.replace('_', '-')}" for name in missing)
                + "；或使用 --from-last-commit 重放上次提交"
            )
        artifacts = {name: _read_json(value) for name, value in artifact_paths.items()}

    service = ChapterCommitService(Path(args.project_root))
    try:
        payload = service.build_commit(
            chapter=args.chapter,
            review_result=artifacts["review_result"],
            fulfillment_result=artifacts["fulfillment_result"],
            disambiguation_result=artifacts["disambiguation_result"],
            extraction_result=artifacts["extraction_result"],
        )
    except ChapterBindingError as exc:
        if args.from_last_commit:
            raise SystemExit(
                f"错误：第 {args.chapter} 章正文在上次提交后已改动（{exc.code}），"
                "旧裁决不能重放到新正文。请重跑 /canon-ledger-write 重新走完整写作链。"
            ) from exc
        raise
    service.persist_commit(payload)
    try:
        payload = service.apply_projections(payload)
    except Exception as exc:
        print(
            f"错误：第 {args.chapter} 章提交文件已保存，但事件库/投影写入中断（{exc}）。"
            f"正史与读模型当前不一致，请修复后运行："
            f"canon_ledger.py projections retry --chapter {args.chapter}。",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    print(json.dumps(payload, ensure_ascii=False))

    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    projection_status = (
        payload.get("projection_status")
        if isinstance(payload.get("projection_status"), dict)
        else {}
    )
    unhealthy = {
        name: str(value)
        for name, value in projection_status.items()
        if str(value) == "pending" or str(value).startswith("failed")
    }
    if str(meta.get("status") or "") == "accepted" and unhealthy:
        # An accepted commit is canonical the moment it is persisted.  If any
        # read model failed to follow, exit non-zero so the failure is visible
        # to the author instead of hiding inside the payload JSON.
        details = "、".join(
            f"{name}={value}" for name, value in sorted(unhealthy.items())
        )
        print(
            f"错误：第 {args.chapter} 章提交已保存，但读模型投影未完成（{details}）。"
            f"请运行 canon_ledger.py projections retry --chapter {args.chapter} "
            "修复后再继续写作。",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
