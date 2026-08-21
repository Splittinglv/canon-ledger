#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sqlite3
import sys
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from data_modules.chapter_content_binding import build_chapter_binding  # noqa: E402
from data_modules.chapter_commit_service import ChapterCommitService  # noqa: E402
from data_modules.projection_log import read_projection_runs  # noqa: E402
from data_modules.projections import replay_projections, retry_projection  # noqa: E402
from .review_test_helpers import (  # noqa: E402
    inject_hard_evidence_quotes,
    standard_review,
    write_current_chapter_contract,
)


def _build_bound_commit(service: ChapterCommitService, **kwargs):
    chapter = int(kwargs["chapter"])
    chapter_path = service.project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        chapter_path.read_text(encoding="utf-8")
        if chapter_path.exists()
        else f"第{chapter}章测试正文\n"
    )
    extraction, chapter_text = inject_hard_evidence_quotes(
        dict(kwargs.get("extraction_result") or {}),
        chapter=chapter,
        chapter_text=existing,
    )
    kwargs["extraction_result"] = extraction
    chapter_path.write_text(chapter_text, encoding="utf-8")
    binding = build_chapter_binding(service.project_root, chapter)
    write_current_chapter_contract(
        service.project_root,
        chapter,
        planned_nodes=list(kwargs["fulfillment_result"].get("planned_nodes") or []),
    )
    if "blocking_count" in kwargs["review_result"]:
        kwargs["review_result"] = standard_review(
            binding,
            blocking_count=int(kwargs["review_result"].get("blocking_count") or 0),
        )
    for artifact_name in (
        "review_result",
        "fulfillment_result",
        "disambiguation_result",
        "extraction_result",
    ):
        kwargs[artifact_name] = {
            **kwargs[artifact_name],
            "chapter_binding": dict(binding),
        }
    return service.build_commit(**kwargs)


def _make_rejected_commit(project_root: Path, chapter: int) -> None:
    (project_root / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (project_root / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    service = ChapterCommitService(project_root)
    payload = _build_bound_commit(
        service,
        chapter=chapter,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["本章必须完成"],
            "covered_nodes": [],
            "missed_nodes": ["本章必须完成"],
            "extra_nodes": [],
            "enforcement": "strict",
        },
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    service.persist_commit(payload)


def _make_accepted_commit_with_event(project_root: Path, chapter: int) -> None:
    (project_root / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (project_root / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    service = ChapterCommitService(project_root)
    payload = _build_bound_commit(
        service,
        chapter=chapter,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [
                {"entity_id": "medicine_box", "field": "owner", "new": "shopkeeper"}
            ],
            "entity_deltas": [],
            "accepted_events": [
                {
                    "event_id": "evt-open-loop",
                    "event_type": "open_loop_created",
                    "chapter": chapter,
                    "subject": "韩立",
                    "payload": {"description": "神秘玉佩为何发热"},
                }
            ],
        },
    )
    service.persist_commit(payload)


def test_retry_projection_replays_existing_commit(tmp_path):
    _make_rejected_commit(tmp_path, chapter=3)

    report = retry_projection(tmp_path, chapter=3)

    assert report["ok"] is True
    assert report["projection_status"]["state"] == "done"
    state = json.loads((tmp_path / ".canon-ledger" / "state.json").read_text(encoding="utf-8"))
    assert state["progress"]["chapter_status"]["3"] == "chapter_rejected"
    assert read_projection_runs(tmp_path, chapter=3)


def test_retry_projection_rebuilds_event_read_model_from_commit(tmp_path):
    _make_accepted_commit_with_event(tmp_path, chapter=3)
    event_path = tmp_path / ".story-system" / "events" / "chapter_003.events.json"
    assert not event_path.exists()

    report = retry_projection(tmp_path, chapter=3)

    assert report["ok"] is True
    assert report["projection_status"]["memory"] in {"done", "skipped"}
    assert event_path.is_file()
    assert json.loads(event_path.read_text(encoding="utf-8"))[0]["subject"] == "韩立"
    assert read_projection_runs(tmp_path, chapter=3)


def test_retry_projection_reports_missing_commit(tmp_path):
    report = retry_projection(tmp_path, chapter=99)

    assert report["ok"] is False
    assert report["error"] == "missing_commit"


def test_retry_projection_rejects_filename_meta_chapter_mismatch(tmp_path):
    _make_rejected_commit(tmp_path, chapter=10)
    source = tmp_path / ".story-system" / "commits" / "chapter_010.commit.json"
    target = tmp_path / ".story-system" / "commits" / "chapter_003.commit.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    report = retry_projection(tmp_path, chapter=3)

    assert report["ok"] is False
    assert report["error"] == "commit_chapter_mismatch"


def test_retry_projection_rejects_commit_after_manuscript_changed(tmp_path):
    _make_accepted_commit_with_event(tmp_path, chapter=3)
    chapter_path = tmp_path / "正文" / "第0003章.md"
    chapter_path.write_text("第3章正文已修改\n", encoding="utf-8")

    report = retry_projection(tmp_path, chapter=3)

    assert report["ok"] is False
    assert report["error"] == "chapter_content_hash_mismatch"


def test_retry_projection_backfills_only_vector_after_key_is_configured(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("EMBED_API_KEY", "")
    (tmp_path / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    service = ChapterCommitService(tmp_path)
    payload = _build_bound_commit(
        service,
        chapter=4,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [
                {"entity_id": "旧印", "field": "holder", "new": "主角"}
            ],
            "entity_deltas": [],
            "accepted_events": [],
            "summary_text": "主角在古井旁找到一枚旧印。",
        },
    )
    service.persist_commit(payload)
    payload = service.apply_projections(payload)
    assert payload["projection_status"]["vector"] == "skipped"
    monkeypatch.setenv("EMBED_API_KEY", "configured-later")
    selected = []

    def _apply_only_vector(self, commit_payload, *, only_writers=None):
        selected.append(set(only_writers or set()))
        commit_payload["projection_status"]["vector"] = "done"
        return commit_payload

    monkeypatch.setattr(
        ChapterCommitService,
        "apply_projection_writers",
        _apply_only_vector,
    )

    report = retry_projection(tmp_path, chapter=4)

    assert selected == [{"vector"}]
    assert report["ok"] is True
    assert report["projection_status"]["vector"] == "done"


def test_retry_projection_refreshes_legacy_fact_filter_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBED_API_KEY", "")
    (tmp_path / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    service = ChapterCommitService(tmp_path)
    payload = _build_bound_commit(
        service,
        chapter=4,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [
                {"entity_id": "medicine_box", "field": "owner", "new": "shopkeeper"}
            ],
            "entity_deltas": [],
            "accepted_events": [],
            "summary_text": "药箱仍由掌柜保管。",
        },
    )
    service.persist_commit(payload)
    payload = service.apply_projections(payload)
    assert payload["projection_status"]["vector"] == "skipped"

    vector_db = tmp_path / ".canon-ledger" / "vectors.db"
    with sqlite3.connect(vector_db) as conn:
        conn.execute(
            "UPDATE vectors SET source_file = ? WHERE chapter = 4",
            ("commit:chapter_004:legacy-fact-only-v1",),
        )

    selected = []

    def _apply_only_vector(self, commit_payload, *, only_writers=None):
        selected.append(set(only_writers or set()))
        commit_payload["projection_status"]["vector"] = "done"
        return commit_payload

    monkeypatch.setattr(
        ChapterCommitService,
        "apply_projection_writers",
        _apply_only_vector,
    )

    report = retry_projection(tmp_path, chapter=4)

    assert selected == [{"vector"}]
    assert report["ok"] is True


def test_replay_projections_runs_range(tmp_path):
    _make_rejected_commit(tmp_path, chapter=1)
    _make_rejected_commit(tmp_path, chapter=2)

    report = replay_projections(tmp_path, start_chapter=1, end_chapter=2)

    assert report["ok"] is True
    assert [item["chapter"] for item in report["results"]] == [1, 2]
