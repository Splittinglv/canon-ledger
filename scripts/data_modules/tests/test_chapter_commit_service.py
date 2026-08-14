#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path

import pytest

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_content_binding import (
    ChapterBindingError,
    build_chapter_binding,
)
from data_modules.config import DataModulesConfig
from data_modules.index_manager import IndexManager


_ARTIFACT_KEYS = (
    "review_result",
    "fulfillment_result",
    "disambiguation_result",
    "extraction_result",
)


def _chapter_binding(project_root, chapter, content=None):
    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    if not chapter_path.exists():
        chapter_path.write_text(
            content or f"第{chapter}章最终正文\n",
            encoding="utf-8",
        )
    return build_chapter_binding(project_root, chapter)


def _build_commit(service, project_root, **kwargs):
    binding = _chapter_binding(project_root, int(kwargs["chapter"]))
    bound = dict(kwargs)
    for key in _ARTIFACT_KEYS:
        artifact = bound.get(key)
        if isinstance(artifact, dict):
            artifact = dict(artifact)
            artifact.setdefault("chapter_binding", dict(binding))
            bound[key] = artifact
    return service.build_commit(**bound)


def test_commit_service_rejects_when_missed_nodes_exist(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(service, tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": [],
            "missed_nodes": ["发现陷阱"],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "rejected"


def test_commit_service_accepts_when_all_checks_pass(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(service, tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "accepted"
    assert payload["contract_refs"]["master"] == "MASTER_SETTING.json"
    assert payload["contract_refs"]["volume"] == "volume_001.json"
    assert payload["contract_refs"]["chapter"] == "chapter_003.json"
    assert payload["outline_snapshot"]["covered_nodes"] == ["发现陷阱"]
    assert payload["extraction_result"]["accepted_events"] == []
    assert "accepted_events" not in payload
    assert "state_deltas" not in payload
    assert "entity_deltas" not in payload


def test_commit_service_rejects_empty_fulfillment_for_authoritative_nodes(tmp_path):
    contract_path = tmp_path / ".story-system" / "chapters" / "chapter_003.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "meta": {"chapter": 3},
                "chapter_directive": {
                    "must_cover_nodes": ["识别封蜡缺口"]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="fulfillment_planned_nodes_mismatch"):
        _build_commit(
            service,
            tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "state_deltas": [],
                "entity_deltas": [],
                "accepted_events": [],
            },
        )


def test_commit_service_rejects_outline_nodes_missing_from_contract(tmp_path):
    outline_path = tmp_path / "大纲" / "第3章-账簿.md"
    outline_path.parent.mkdir(parents=True, exist_ok=True)
    outline_path.write_text(
        "### 第三章：账簿\n- 必须覆盖节点：识别封蜡缺口",
        encoding="utf-8",
    )
    service = ChapterCommitService(tmp_path)

    with pytest.raises(
        ValueError,
        match="chapter_contract_missing_must_cover_nodes",
    ):
        _build_commit(
            service,
            tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "state_deltas": [],
                "entity_deltas": [],
                "accepted_events": [],
            },
        )


def test_commit_service_includes_volume_ref_and_write_fact_provenance(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(service, tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )

    assert payload["contract_refs"]["volume"] == "volume_001.json"
    assert payload["provenance"]["write_fact_role"] == "chapter_commit"
    assert payload["provenance"]["projection_role"] == "derived_read_models"
    assert payload["chapter_binding"] == payload["provenance"]["chapter_binding"]
    for artifact_name in _ARTIFACT_KEYS:
        assert payload[artifact_name]["chapter_binding"] == payload["chapter_binding"]


def test_build_commit_rejects_artifacts_bound_to_another_chapter(tmp_path):
    service = ChapterCommitService(tmp_path)
    chapter_one_binding = _chapter_binding(tmp_path, 1)
    _chapter_binding(tmp_path, 2)
    common = {"chapter_binding": chapter_one_binding}

    with pytest.raises(ChapterBindingError) as exc_info:
        service.build_commit(
            chapter=2,
            review_result={"blocking_count": 0, **common},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
                **common,
            },
            disambiguation_result={"pending": [], **common},
            extraction_result={
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                **common,
            },
        )

    assert exc_info.value.code == "artifact_chapter_mismatch"


def test_build_commit_rejects_one_stale_artifact_binding(tmp_path):
    service = ChapterCommitService(tmp_path)
    binding = _chapter_binding(tmp_path, 3)
    stale = {**binding, "sha256": "f" * 64}

    with pytest.raises(ChapterBindingError) as exc_info:
        service.build_commit(
            chapter=3,
            review_result={"blocking_count": 0, "chapter_binding": binding},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
                "chapter_binding": binding,
            },
            disambiguation_result={"pending": [], "chapter_binding": binding},
            extraction_result={
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                "chapter_binding": stale,
            },
        )

    assert exc_info.value.code == "chapter_content_hash_mismatch"


def test_commit_service_rejects_malformed_gate_artifacts(tmp_path):
    service = ChapterCommitService(tmp_path)
    valid_fulfillment = {
        "planned_nodes": [],
        "covered_nodes": [],
        "missed_nodes": [],
        "extra_nodes": [],
    }
    valid_disambiguation = {"pending": []}
    valid_extraction = {"state_deltas": [], "entity_deltas": [], "accepted_events": []}

    with pytest.raises(ValueError, match="blocking_count"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={},
            fulfillment_result=valid_fulfillment,
            disambiguation_result=valid_disambiguation,
            extraction_result=valid_extraction,
        )

    with pytest.raises(ValueError, match="fulfillment_result"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={"fulfillment": {"missed_nodes": ["遗漏节点"]}},
            disambiguation_result=valid_disambiguation,
            extraction_result=valid_extraction,
        )

    with pytest.raises(ValueError, match="disambiguation_result"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result=valid_fulfillment,
            disambiguation_result={"disambiguation": {"pending": ["宗主"]}},
            extraction_result=valid_extraction,
        )


def test_commit_service_rejects_nested_extraction_result_shape(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="top-level"):
        _build_commit(service, tmp_path,
            chapter=76,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "chapter": 76,
                "extraction": {
                    "scenes": [{"summary": "场景切分"}],
                    "unresolved_threads": ["未解线索"],
                },
            },
        )


def test_commit_service_rejects_extraction_wrapper_even_with_empty_core_fields(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="nested under extraction"):
        _build_commit(service, tmp_path,
            chapter=76,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                "extraction": {
                    "scenes": [{"summary": "真实场景却被包错层"}],
                    "summary_text": "真实摘要却被包错层",
                },
            },
        )


def test_commit_service_rejects_extraction_result_missing_core_fields(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="accepted_events"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={"summary_text": "摘要"},
        )


def test_commit_service_rejects_non_object_extraction_items(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match=r"state_deltas\[0\]"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "accepted_events": [],
                "state_deltas": ["realm changed"],
                "entity_deltas": [],
            },
        )


def test_commit_service_rejects_non_object_accepted_event_items(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match=r"accepted_events\[0\]"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "accepted_events": ["not-a-json-object"],
                "state_deltas": [],
                "entity_deltas": [],
            },
        )


def test_commit_service_normalizes_accepted_events_before_projection(tmp_path):
    service = ChapterCommitService(tmp_path)

    payload = _build_commit(service, tmp_path,
        chapter=76,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [
                {
                    "type": "mystery_introduction",
                    "characters": ["xiaoyan"],
                    "payload": {"content": "萧炎发现石门背后的新疑点"},
                }
            ],
        },
    )

    event = payload["extraction_result"]["accepted_events"][0]
    assert event["event_id"].startswith("evt-ch076-001-")
    assert event["chapter"] == 76
    assert event["event_type"] == "open_loop_created"
    assert event["subject"] == "xiaoyan"
    assert "accepted_events" not in payload


def test_apply_projections_normalizes_events_before_router_inspection(
    tmp_path, monkeypatch
):
    captured = {}

    class SpyRouter:
        def required_writers(self, payload):
            captured["events"] = list(payload.get("extraction_result", {}).get("accepted_events") or [])
            return []

    monkeypatch.setattr(
        "data_modules.chapter_commit_service.EventProjectionRouter",
        lambda: SpyRouter(),
    )

    service = ChapterCommitService(tmp_path)
    payload = _build_commit(
        service,
        tmp_path,
        chapter=76,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "accepted_events": [
                {
                    "type": "scene_open",
                    "characters": ["xiaoyan"],
                    "payload": {"content": "萧炎推开石门，新的悬念出现"},
                }
            ],
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "",
        },
    )

    service.apply_projections(payload)

    event = captured["events"][0]
    assert event["event_id"].startswith("evt-ch076-001-")
    assert event["chapter"] == 76
    assert event["event_type"] == "open_loop_created"
    assert event["subject"] == "xiaoyan"
    assert payload["extraction_result"]["accepted_events"] == captured["events"]


def test_apply_projections_updates_state_for_rejected_commit(tmp_path):
    import json

    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    service = ChapterCommitService(tmp_path)
    payload = _build_commit(service, tmp_path,
        chapter=7,
        review_result={"blocking_count": 1},
        fulfillment_result={
            "planned_nodes": ["进入坊市"],
            "covered_nodes": ["进入坊市"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )

    projected = service.apply_projections(payload)

    state = json.loads((tmp_path / ".webnovel" / "state.json").read_text(encoding="utf-8"))
    assert projected["projection_status"]["state"] == "done"
    assert state["progress"]["chapter_status"]["7"] == "chapter_rejected"


def test_chapter_commit_cli_builds_and_persists_commit(tmp_path, monkeypatch):
    review_path = tmp_path / "review.json"
    fulfillment_path = tmp_path / "fulfillment.json"
    disambiguation_path = tmp_path / "disambiguation.json"
    extraction_path = tmp_path / "extraction.json"
    binding = _chapter_binding(tmp_path, 3)
    _write_artifact = lambda path, payload: path.write_text(
        json.dumps({**payload, "chapter_binding": binding}, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_artifact(review_path, {"blocking_count": 0})
    _write_artifact(
        fulfillment_path,
        {
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": ["发现陷阱"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
    )
    _write_artifact(disambiguation_path, {"pending": []})
    _write_artifact(
        extraction_path,
        {"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )

    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from chapter_commit import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "chapter_commit",
            "--project-root",
            str(tmp_path),
            "--chapter",
            "3",
            "--review-result",
            str(review_path),
            "--fulfillment-result",
            str(fulfillment_path),
            "--disambiguation-result",
            str(disambiguation_path),
            "--extraction-result",
            str(extraction_path),
        ],
    )
    main()

    assert (tmp_path / ".story-system" / "commits" / "chapter_003.commit.json").is_file()


def test_apply_projections_writes_events_and_amend_proposals(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(service, tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": ["发现陷阱"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "",
            "accepted_events": [
                {
                    "event_id": "evt-001",
                    "chapter": 3,
                    "event_type": "world_rule_broken",
                    "subject": "金手指",
                    "payload": {
                        "field": "world_rule",
                        "base_value": "每日一次",
                        "proposed_value": "短时失控突破",
                    },
                }
            ],
        },
    )

    service.apply_projections(payload)

    assert (tmp_path / ".story-system" / "events" / "chapter_003.events.json").is_file()
    manager = IndexManager(DataModulesConfig.from_project_root(tmp_path))
    with manager._get_conn() as conn:
        row = conn.execute(
            """
            SELECT record_type, field, override_value, status
            FROM override_contracts
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row["record_type"] == "amend_proposal"
    assert row["field"] == "world_rule"
    assert row["override_value"] == "短时失控突破"
    assert row["status"] == "pending"


def test_apply_projections_fails_closed_when_chapter_changed(
    tmp_path, monkeypatch
):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(
        service,
        tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [
                {
                    "event_type": "open_loop_created",
                    "subject": "石门",
                    "payload": {"content": "石门后的新疑点"},
                }
            ],
        },
    )
    (tmp_path / "正文" / "第0003章.md").write_text("审查后正文已变更\n", encoding="utf-8")
    writer_calls = []
    monkeypatch.setattr(
        service,
        "_projection_writers",
        lambda: writer_calls.append(True) or {},
    )

    projected = service.apply_projections(payload)

    assert writer_calls == []
    assert not (
        tmp_path / ".story-system" / "events" / "chapter_003.events.json"
    ).exists()
    for writer in ("state", "index", "memory", "vector"):
        assert projected["projection_status"][writer] == (
            "failed:chapter_content_changed"
        )


def test_apply_projection_writers_rechecks_binding_before_loading_writers(
    tmp_path, monkeypatch
):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(
        service,
        tmp_path,
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
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [],
        },
    )
    (tmp_path / "正文" / "第0004章.md").write_text("当前正文不再匹配\n", encoding="utf-8")
    writer_calls = []
    monkeypatch.setattr(
        service,
        "_projection_writers",
        lambda: writer_calls.append(True) or {},
    )

    projected = service.apply_projection_writers(payload)

    assert writer_calls == []
    for writer in ("state", "index", "vector"):
        assert projected["projection_status"][writer] == (
            "failed:chapter_content_changed"
        )


def test_legacy_commit_without_binding_cannot_be_projected_as_trusted(
    tmp_path, monkeypatch
):
    service = ChapterCommitService(tmp_path)
    legacy = {
        "meta": {"schema_version": "story-system/v1", "chapter": 5, "status": "accepted"},
        "projection_status": {},
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
        },
    }
    writer_calls = []
    monkeypatch.setattr(
        service,
        "_projection_writers",
        lambda: writer_calls.append(True) or {},
    )

    projected = service.apply_projection_writers(legacy)

    assert writer_calls == []
    for writer in ("state", "index", "vector"):
        assert projected["projection_status"][writer] == (
            "failed:chapter_content_changed"
        )


def test_projection_chain_rechecks_binding_between_writers(tmp_path, monkeypatch):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(
        service,
        tmp_path,
        chapter=6,
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
                {"entity_id": "hero", "field": "realm", "new": "foundation"}
            ],
            "entity_deltas": [],
            "accepted_events": [],
        },
    )
    chapter_path = tmp_path / "正文" / "第0006章.md"
    calls = []

    class MutatingWriter:
        def apply(self, _payload):
            calls.append("state")
            chapter_path.write_text("投影期间正文已改\n", encoding="utf-8")
            return {"applied": True}

    class ShouldNotRunWriter:
        def apply(self, _payload):
            calls.append("index")
            return {"applied": True}

    monkeypatch.setattr(
        service,
        "_projection_writers",
        lambda: {"state": MutatingWriter(), "index": ShouldNotRunWriter()},
    )
    monkeypatch.setattr(
        "data_modules.chapter_commit_service.EventProjectionRouter.required_writers",
        lambda _self, _payload: {"state", "index"},
    )

    projected = service.apply_projection_writers(payload)

    assert calls == ["state"]
    assert projected["projection_status"]["state"] == "done"
    assert projected["projection_status"]["index"] == (
        "failed:chapter_content_changed"
    )
