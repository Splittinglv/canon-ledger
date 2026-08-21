#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path

from .test_project_phase import _make_contracts, _make_init_ready, _write_json


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from data_modules.write_gates import run_write_gate  # noqa: E402
from data_modules.chapter_content_binding import build_chapter_binding  # noqa: E402
from data_modules.human_review import HumanReviewService  # noqa: E402
from data_modules.projection_log import append_projection_run  # noqa: E402
from .review_test_helpers import standard_review  # noqa: E402


def _make_current_contracts(project_root: Path, chapter: int = 1) -> None:
    """创建包含当前必达节点与禁区字段的章合同。"""
    _make_contracts(project_root, chapter=chapter)
    path = (
        project_root
        / ".story-system"
        / "chapters"
        / f"chapter_{chapter:03d}.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    directive = payload.setdefault("chapter_directive", {})
    directive["must_cover_nodes"] = []
    directive["forbidden_zones"] = []
    _write_json(path, payload)


def _queue_human_review_items(
    project_root: Path,
    chapter: int,
    items: list[dict],
) -> tuple[HumanReviewService, dict, list[dict]]:
    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(f"第 {chapter} 章正文。", encoding="utf-8")
    binding = build_chapter_binding(project_root, chapter)
    service = HumanReviewService(project_root)
    service.persist_queue(chapter, binding, items)
    return service, binding, service.list_items(chapter)


def _fact_review_item(decision_id: str) -> dict:
    return {
        "decision_id": decision_id,
        "source": "fact_extraction",
        "category": "presence",
        "dimension": "presence",
        "candidate_event_id": decision_id,
        "reason": "候选事实需要作者确认",
    }


def _prose_rewrite_item(decision_id: str) -> dict:
    return {
        "decision_id": decision_id,
        "source": "review_manual_check",
        "category": "timeline",
        "dimension": "presence",
        "candidate_event_id": decision_id,
        "reason": "作者需要判断这是否是时间线穿帮",
        "options": ["confirm", "rewrite"],
    }


def _write_valid_artifacts(project_root: Path) -> None:
    binding = build_chapter_binding(project_root, 1)
    _write_json(
        project_root / ".canon-ledger" / "tmp" / "review_results.json",
        standard_review(binding),
    )
    _write_json(
        project_root / ".canon-ledger" / "tmp" / "fulfillment_result.json",
        {
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": binding,
        },
    )
    _write_json(
        project_root / ".canon-ledger" / "tmp" / "disambiguation_result.json",
        {"pending": [], "chapter_binding": binding},
    )
    _write_json(
        project_root / ".canon-ledger" / "tmp" / "extraction_result.json",
        {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "摘要",
            "chapter_binding": binding,
        },
    )


def _valid_commit_payload(project_root: Path, projection_status: dict) -> dict:
    binding = build_chapter_binding(project_root, 1)
    return {
        "meta": {
            "schema_version": "story-system/v1",
            "chapter": 1,
            "status": "accepted",
        },
        "chapter_binding": binding,
        "provenance": {"chapter_binding": binding},
        "review_result": standard_review(binding),
        "fulfillment_result": {
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": binding,
        },
        "disambiguation_result": {"pending": [], "chapter_binding": binding},
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "摘要",
            "chapter_binding": binding,
        },
        "projection_status": projection_status,
    }


def test_prewrite_gate_does_not_treat_contract_ready_as_canon_ready(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)

    report = run_write_gate(tmp_path, chapter=1, stage="prewrite")

    assert report["ok"] is False
    assert report["stage"] == "prewrite"
    assert report["phase"] == "canon_v3:migration_required"
    assert report["details"]["workflow_snapshot"]["primary_action"]["code"] == (
        "initialize_v3"
    )


def test_prewrite_gate_blocks_when_persisted_chapter_goal_is_empty(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    _write_json(
        tmp_path / ".story-system" / "chapters" / "chapter_001.json",
        {
            "meta": {"chapter": 1},
            "chapter_directive": {
                "goal": "",
                "must_cover_nodes": [],
                "forbidden_zones": [],
            },
        },
    )

    report = run_write_gate(tmp_path, chapter=1, stage="prewrite")

    assert report["ok"] is False
    goal_error = next(
        item
        for item in report["errors"]
        if item["code"] == "chapter_contract.goal_invalid"
    )
    assert goal_error["details"]["validation_code"] == "chapter_contract_missing_goal"


def test_prewrite_gate_uses_only_current_must_cover_nodes(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    _write_json(
        tmp_path / ".story-system" / "chapters" / "chapter_001.json",
        {
            "meta": {"chapter": 1},
            "chapter_directive": {
                "goal": "查清封蜡缺口与账房暗号的联系",
                "must_cover_nodes": ["识别封蜡缺口"],
                "forbidden_zones": [],
                "mandatory_nodes": ["这个未知字段不得进入章合同"],
            },
        },
    )

    report = run_write_gate(tmp_path, chapter=1, stage="prewrite")

    assert report["details"]["prewrite_validation"]["fulfillment_seed"][
        "planned_nodes"
    ] == ["识别封蜡缺口"]


def test_prewrite_gate_rejects_invalid_current_forbidden_zones(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    _write_json(
        tmp_path / ".story-system" / "chapters" / "chapter_001.json",
        {
            "meta": {"chapter": 1},
            "chapter_directive": {
                "goal": "确认封蜡缺口的来源",
                "must_cover_nodes": ["识别封蜡缺口"],
                "forbidden_zones": "不可提前揭露掌柜身份",
            },
        },
    )

    report = run_write_gate(tmp_path, chapter=1, stage="prewrite")

    assert report["ok"] is False
    assert any(
        item["code"] == "chapter_contract.forbidden_zones_invalid"
        and item["details"]["validation_code"]
        == "chapter_contract_forbidden_zones_must_be_list"
        for item in report["errors"]
    )


def test_prewrite_gate_reports_ordinary_pending_as_advisory(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    state_path = tmp_path / ".canon-ledger" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["disambiguation_pending"] = [{"mention": "宗主"}]
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    report = run_write_gate(tmp_path, chapter=1, stage="prewrite")

    assert report["ok"] is True
    assert any(
        item["code"] == "disambiguation_pending_advisory"
        for item in report["warnings"]
    )
    assert report["details"]["prewrite_validation"]["disambiguation_domain"]["pending_count"] == 1


def test_prewrite_gate_blocks_only_explicit_blocking_pending(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    state_path = tmp_path / ".canon-ledger" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["disambiguation_pending"] = [
        {"mention": "遗失钥匙", "blocking": True}
    ]
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    report = run_write_gate(tmp_path, chapter=1, stage="prewrite")

    assert report["ok"] is False
    assert any(
        item["code"] == "prewrite_validator_blocking"
        for item in report["errors"]
    )


def test_prewrite_blocks_prior_pending_but_allows_reworking_same_chapter(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    _queue_human_review_items(
        tmp_path,
        1,
        [_fact_review_item("chapter-one-pending")],
    )

    same_chapter = run_write_gate(tmp_path, chapter=1, stage="prewrite")
    assert not any(
        item["code"].startswith("human_review_")
        for item in same_chapter["errors"]
    )

    _make_current_contracts(tmp_path, chapter=2)
    next_chapter = run_write_gate(tmp_path, chapter=2, stage="prewrite")
    blocker = next(
        item
        for item in next_chapter["errors"]
        if item["code"] == "human_review_pending"
    )
    assert "/canon-ledger-confirm 1" in blocker["repair"]


def test_prewrite_allows_advisory_human_review_with_warning(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=2)
    _queue_human_review_items(
        tmp_path,
        1,
        [
            {
                **_fact_review_item("chapter-one-advisory"),
                "disposition": "advisory",
                "required": False,
                "review_kind": "ambiguity",
                "materiality": "low",
                "evidence_quote": "第 1 章正文。",
            }
        ],
    )

    report = run_write_gate(tmp_path, chapter=2, stage="prewrite")

    assert not any(
        item["code"].startswith("human_review_")
        for item in report["errors"]
    )
    warning = next(
        item for item in report["warnings"] if item["code"] == "human_review_advisory"
    )
    assert warning["details"]["summary"]["counts"]["advisory_pending"] == 1


def test_prewrite_warns_for_advisory_confirm_not_replayed(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=2)
    service, _binding, items = _queue_human_review_items(
        tmp_path,
        1,
        [
            {
                **_fact_review_item("chapter-one-advisory-confirm"),
                "disposition": "advisory",
                "required": False,
                "review_kind": "ambiguity",
                "materiality": "low",
                "evidence_quote": "第 1 章正文。",
            }
        ],
    )
    service.record(
        {
            "decisions": [
                {"decision_id": items[0]["decision_id"], "action": "confirm"}
            ]
        }
    )

    report = run_write_gate(tmp_path, chapter=2, stage="prewrite")

    assert not any(
        item["code"].startswith("human_review_")
        for item in report["errors"]
    )
    warning = next(
        item
        for item in report["warnings"]
        if item["code"] == "human_review_advisory"
    )
    summary = warning["details"]["summary"]
    assert summary["counts"]["advisory_not_replayed"] == 1
    assert summary["advisory_not_replayed"][0]["decision_action"] == "confirm"


def test_prewrite_blocks_canon_changing_advisory_until_replayed(tmp_path):
    for action in ("ignore",):
        root = tmp_path / action
        _make_init_ready(root)
        _make_current_contracts(root, chapter=2)
        event = {
            "event_id": f"advisory-{action}-event",
            "chapter": 1,
            "sequence": 1,
            "event_type": "presence_observed",
            "subject": "hero",
            "payload": {
                "location_id": "north-city",
                "presence_kind": "physical",
                "evidence_quote": "第 1 章正文。",
            },
        }
        service, _binding, items = _queue_human_review_items(
            root,
            1,
            [
                {
                    **_fact_review_item(f"chapter-one-advisory-{action}"),
                    "candidate_event_id": event["event_id"],
                    "candidate_event": event,
                    "disposition": "advisory",
                    "required": False,
                    "review_kind": "ambiguity",
                    "materiality": "low",
                    "evidence_quote": "第 1 章正文。",
                }
            ],
        )
        decision = {"decision_id": items[0]["decision_id"], "action": action}
        service.record({"decisions": [decision]})

        report = run_write_gate(root, chapter=2, stage="prewrite")

        blocker = next(
            item
            for item in report["errors"]
            if item["code"] == "human_review_decisions_not_replayed"
        )
        queued = blocker["details"]["items"][0]
        assert queued["disposition"] == "advisory"
        assert queued["decision_action"] == action


def test_prewrite_blocks_advisory_rewrite(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=2)
    service, _binding, items = _queue_human_review_items(
        tmp_path,
        1,
        [
            {
                **_prose_rewrite_item("chapter-one-advisory-rewrite"),
                "disposition": "advisory",
                "required": False,
                "review_kind": "ambiguity",
                "materiality": "low",
                "evidence_quote": "第 1 章正文。",
            }
        ],
    )
    service.record(
        {
            "decisions": [
                {"decision_id": items[0]["decision_id"], "action": "rewrite"}
            ]
        }
    )

    report = run_write_gate(tmp_path, chapter=2, stage="prewrite")

    blocker = next(
        item
        for item in report["errors"]
        if item["code"] == "human_review_rewrite_required"
    )
    assert blocker["details"]["items"][0]["disposition"] == "advisory"


def test_prewrite_blocks_required_checkpoint(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=2)
    _queue_human_review_items(
        tmp_path,
        1,
        [
            {
                **_fact_review_item("volume-end-checkpoint"),
                "review_kind": "checkpoint",
                "trigger_kind": "volume_end",
                "materiality": "critical",
                "disposition": "human_required",
                "required": True,
            }
        ],
    )

    report = run_write_gate(tmp_path, chapter=2, stage="prewrite")

    blocker = next(
        item for item in report["errors"] if item["code"] == "human_review_pending"
    )
    queued = blocker["details"]["items"][0]
    assert queued["review_kind"] == "checkpoint"
    assert queued["trigger_kind"] == "volume_end"
    assert queued["required"] is True


def test_prewrite_routes_prior_rewrite_required_back_to_write(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=2)
    service, _binding, items = _queue_human_review_items(
        tmp_path,
        1,
        [_prose_rewrite_item("chapter-one-rewrite")],
    )
    service.record(
        {
            "decisions": [
                {"decision_id": items[0]["decision_id"], "action": "rewrite"}
            ]
        }
    )

    report = run_write_gate(tmp_path, chapter=2, stage="prewrite")

    blocker = next(
        item
        for item in report["errors"]
        if item["code"] == "human_review_rewrite_required"
    )
    assert "/canon-ledger-write 1" in blocker["repair"]
    assert "/canon-ledger-confirm" not in blocker["repair"]


def test_prewrite_routes_resolved_but_not_replayed_to_confirm(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=2)
    service, binding, items = _queue_human_review_items(
        tmp_path,
        1,
        [_fact_review_item("chapter-one-resolved")],
    )
    decision_id = items[0]["decision_id"]
    service.record(
        {"decisions": [{"decision_id": decision_id, "action": "ignore"}]}
    )
    decision_sha256 = service.list_items(1)[0]["decision_sha256"]

    saved_only = run_write_gate(tmp_path, chapter=2, stage="prewrite")
    blocker = next(
        item
        for item in saved_only["errors"]
        if item["code"] == "human_review_decisions_not_replayed"
    )
    assert "/canon-ledger-confirm 1" in blocker["repair"]

    # 一旦同一正文绑定的裁决 ID 已进入 commit provenance，人工门禁应放行；
    # 其它投影健康检查仍可独立报告自己的问题。
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        {
            "meta": {"chapter": 1, "status": "accepted"},
            "chapter_binding": binding,
            "provenance": {
                "chapter_binding": binding,
                "human_review": {
                    "resolved_decision_ids": [decision_id],
                    "decision_receipts": [
                        {
                            "decision_id": decision_id,
                            "decision_sha256": decision_sha256,
                        }
                    ],
                },
            },
        },
    )
    replayed = run_write_gate(tmp_path, chapter=2, stage="prewrite")
    assert not any(
        item["code"] == "human_review_decisions_not_replayed"
        for item in replayed["errors"]
    )

    # 同一 decision_id 被改成另一种动作后，旧 commit 的 receipt 不得继续
    # 冒充最新裁决已经生效。
    service.record(
        {"decisions": [{"decision_id": decision_id, "action": "confirm"}]}
    )
    changed = run_write_gate(tmp_path, chapter=2, stage="prewrite")
    assert any(
        item["code"] == "human_review_decisions_not_replayed"
        for item in changed["errors"]
    )


def test_prewrite_uses_earliest_human_review_chapter_before_state_priority(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=3)
    _queue_human_review_items(
        tmp_path,
        1,
        [_fact_review_item("chapter-one-pending")],
    )
    service, _binding, items = _queue_human_review_items(
        tmp_path,
        2,
        [_prose_rewrite_item("chapter-two-rewrite")],
    )
    service.record(
        {
            "decisions": [
                {"decision_id": items[0]["decision_id"], "action": "rewrite"}
            ]
        }
    )

    report = run_write_gate(tmp_path, chapter=3, stage="prewrite")

    human_blockers = [
        item for item in report["errors"] if item["code"].startswith("human_review_")
    ]
    assert [item["code"] for item in human_blockers] == ["human_review_pending"]
    assert "/canon-ledger-confirm 1" in human_blockers[0]["repair"]


def test_prewrite_prioritizes_rewrite_over_pending_in_same_chapter(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=2)
    service, _binding, items = _queue_human_review_items(
        tmp_path,
        1,
        [
            _fact_review_item("chapter-one-pending"),
            _prose_rewrite_item("chapter-one-rewrite"),
        ],
    )
    rewrite = next(item for item in items if item["source"] == "review_manual_check")
    service.record(
        {
            "decisions": [
                {"decision_id": rewrite["decision_id"], "action": "rewrite"}
            ]
        }
    )

    report = run_write_gate(tmp_path, chapter=2, stage="prewrite")

    human_blockers = [
        item for item in report["errors"] if item["code"].startswith("human_review_")
    ]
    assert [item["code"] for item in human_blockers] == [
        "human_review_rewrite_required"
    ]
    assert "/canon-ledger-write 1" in human_blockers[0]["repair"]


def test_prewrite_fails_closed_when_prior_human_review_queue_is_corrupt(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=2)
    queue_path = (
        tmp_path
        / ".canon-ledger"
        / "human-review"
        / "queue"
        / "chapter_0001.json"
    )
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text("{not-json", encoding="utf-8")

    report = run_write_gate(tmp_path, chapter=2, stage="prewrite")

    blocker = next(
        item
        for item in report["errors"]
        if item["code"] == "human_review_state_invalid"
    )
    assert "/canon-ledger-doctor" in blocker["repair"]


def test_prewrite_ignores_corrupt_future_human_review_queue(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=2)
    queue_path = (
        tmp_path
        / ".canon-ledger"
        / "human-review"
        / "queue"
        / "chapter_0003.json"
    )
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text("{not-json", encoding="utf-8")

    report = run_write_gate(tmp_path, chapter=2, stage="prewrite")

    assert not any(
        item["code"] == "human_review_state_invalid"
        for item in report["errors"]
    )


def test_precommit_gate_reports_missing_artifacts(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")

    report = run_write_gate(tmp_path, chapter=1, stage="precommit")

    assert report["ok"] is False
    assert any(item["code"] == "artifact.missing_artifact" for item in report["errors"])


def test_precommit_gate_accepts_valid_artifacts(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    _write_valid_artifacts(tmp_path)

    report = run_write_gate(tmp_path, chapter=1, stage="precommit")

    assert report["ok"] is True
    assert report["details"]["artifact_report"]["ok"] is True


def test_precommit_gate_rejects_empty_fulfillment_for_authoritative_nodes(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    _write_json(
        tmp_path / ".story-system" / "chapters" / "chapter_001.json",
        {
            "meta": {"chapter": 1},
            "chapter_directive": {
                "goal": "确认封蜡缺口的来源",
                "must_cover_nodes": ["识别封蜡缺口"]
            },
        },
    )
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    _write_valid_artifacts(tmp_path)

    report = run_write_gate(tmp_path, chapter=1, stage="precommit")

    assert report["ok"] is False
    assert any(
        item["code"] == "artifact.fulfillment_planned_nodes_mismatch"
        for item in report["errors"]
    )


def test_precommit_gate_rejects_malformed_authoritative_nodes(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    _write_json(
        tmp_path / ".story-system" / "chapters" / "chapter_001.json",
        {
            "meta": {"chapter": 1},
            "chapter_directive": {
                "goal": "确认封蜡缺口的来源",
                "must_cover_nodes": "识别封蜡缺口",
            },
        },
    )
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    _write_valid_artifacts(tmp_path)

    report = run_write_gate(tmp_path, chapter=1, stage="precommit")

    assert report["ok"] is False
    assert any(
        item["code"] == "chapter_contract.must_cover_nodes_invalid"
        for item in report["errors"]
    )


def test_precommit_gate_rejects_outline_nodes_dropped_from_contract(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    (tmp_path / "大纲").mkdir(exist_ok=True)
    (tmp_path / "大纲" / "第1章-账簿.md").write_text(
        "### 第一章：账簿\n- 必须覆盖节点：识别封蜡缺口",
        encoding="utf-8",
    )
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    _write_valid_artifacts(tmp_path)

    report = run_write_gate(tmp_path, chapter=1, stage="precommit")

    assert report["ok"] is False
    assert any(
        item["code"] == "chapter_contract.must_cover_nodes_invalid"
        and item["details"]["validation_code"]
        == "chapter_contract_outline_nodes_mismatch"
        for item in report["errors"]
    )


def test_precommit_gate_rejects_artifacts_after_manuscript_changed(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文 v1\n", encoding="utf-8")
    _write_valid_artifacts(tmp_path)
    chapter_file.write_text("正文 v2\n", encoding="utf-8")

    report = run_write_gate(tmp_path, chapter=1, stage="precommit")

    assert report["ok"] is False
    binding_errors = [
        item for item in report["errors"]
        if item["code"] == "artifact.chapter_binding_invalid"
    ]
    assert len(binding_errors) == 4
    assert all(
        item["details"]["binding_code"] == "chapter_content_hash_mismatch"
        for item in binding_errors
    )


def test_precommit_gate_rejects_fulfillment_missing_missed_nodes(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    _write_valid_artifacts(tmp_path)
    _write_json(
        tmp_path / ".canon-ledger" / "tmp" / "fulfillment_result.json",
        {"planned_nodes": [], "covered_nodes": [], "extra_nodes": []},
    )

    report = run_write_gate(tmp_path, chapter=1, stage="precommit")

    assert report["ok"] is False
    assert any(item["code"] == "artifact.schema_error" for item in report["errors"])
    assert any("missed_nodes" in item["message"] for item in report["errors"])


def test_precommit_gate_rejects_disambiguation_missing_pending(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    _write_valid_artifacts(tmp_path)
    _write_json(tmp_path / ".canon-ledger" / "tmp" / "disambiguation_result.json", {"warnings": []})

    report = run_write_gate(tmp_path, chapter=1, stage="precommit")

    assert report["ok"] is False
    assert any(item["code"] == "artifact.schema_error" for item in report["errors"])
    assert any("pending" in item["message"] for item in report["errors"])


def test_precommit_gate_rejects_extraction_missing_accepted_events(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    _write_valid_artifacts(tmp_path)
    _write_json(
        tmp_path / ".canon-ledger" / "tmp" / "extraction_result.json",
        {"state_deltas": [], "entity_deltas": [], "summary_text": "摘要"},
    )

    report = run_write_gate(tmp_path, chapter=1, stage="precommit")

    assert report["ok"] is False
    assert any(item["code"] == "artifact.schema_error" for item in report["errors"])
    assert any("accepted_events" in item["message"] for item in report["errors"])


def test_precommit_gate_blocks_projection_failed_phase(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    _write_valid_artifacts(tmp_path)
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        _valid_commit_payload(
            tmp_path,
            {"state": "done", "index": "failed:locked"},
        ),
    )

    report = run_write_gate(tmp_path, chapter=1, stage="precommit")

    assert report["ok"] is False
    assert any(item["code"] == "phase_not_ready_for_precommit" for item in report["errors"])


def test_postcommit_gate_reports_projection_failure(tmp_path):
    _make_init_ready(tmp_path)
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        {
            "meta": {"chapter": 1, "status": "accepted"},
            "review_result": {"blocking_count": 0},
            "fulfillment_result": {
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            "disambiguation_result": {"pending": []},
            "extraction_result": {
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                "summary_text": "摘要",
            },
            "projection_status": {"state": "done", "index": "failed:locked", "summary": "skipped"},
        },
    )

    report = run_write_gate(tmp_path, chapter=1, stage="postcommit")

    assert report["ok"] is False
    assert any(item["code"] == "commit.projection_failure" for item in report["errors"])


def test_postcommit_gate_prefers_projection_log_failure(tmp_path):
    _make_init_ready(tmp_path)
    commit_payload = {
        "meta": {"chapter": 1, "status": "accepted"},
        "review_result": {"blocking_count": 0},
        "fulfillment_result": {
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        "disambiguation_result": {"pending": []},
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "摘要",
        },
        "projection_status": {"state": "done", "index": "done", "vector": "done"},
    }
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    _write_json(commit_path, commit_payload)
    append_projection_run(
        tmp_path,
        commit_payload,
        {"vector": {"status": "failed:timeout", "error": "timeout"}},
        commit_path=commit_path,
    )

    report = run_write_gate(tmp_path, chapter=1, stage="postcommit")

    assert report["ok"] is False
    assert any(item["code"] == "projection_failure" for item in report["errors"])
    assert report["details"]["projection_source"] == "projection_log"


def test_postcommit_gate_ignores_projection_log_for_replaced_commit(tmp_path):
    _make_init_ready(tmp_path)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    old_payload = {
        "meta": {"chapter": 1, "status": "accepted"},
        "projection_status": {"state": "done", "index": "failed:old"},
    }
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    append_projection_run(
        tmp_path,
        old_payload,
        {"index": {"status": "failed:old"}},
        commit_path=commit_path,
    )
    current_payload = _valid_commit_payload(
        tmp_path,
        {
            "state": "done", "index": "skipped", "summary": "skipped",
            "memory": "skipped", "vector": "skipped"
        },
    )
    _write_json(commit_path, current_payload)

    report = run_write_gate(tmp_path, chapter=1, stage="postcommit")
    assert report["ok"] is True
    assert report["details"]["projection_source"] == "commit"


def test_postcommit_gate_requires_five_projection_statuses_from_projection_log(tmp_path):
    _make_init_ready(tmp_path)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    commit_payload = _valid_commit_payload(
        tmp_path,
        {
            "state": "done",
            "index": "done",
            "summary": "skipped",
            "memory": "skipped",
            "vector": "done",
        },
    )
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    _write_json(commit_path, commit_payload)
    append_projection_run(
        tmp_path,
        commit_payload,
        {"vector": {"status": "done"}},
        commit_path=commit_path,
    )

    report = run_write_gate(tmp_path, chapter=1, stage="postcommit")

    assert report["ok"] is False
    assert report["details"]["projection_source"] == "projection_log"
    assert any(item["code"] == "projection_status_missing" for item in report["errors"])
    assert any("state" in item["message"] for item in report["errors"])


def test_postcommit_gate_accepts_done_or_skipped_projection(tmp_path):
    _make_init_ready(tmp_path)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        _valid_commit_payload(
            tmp_path,
            {
                "state": "done",
                "index": "skipped",
                "summary": "skipped",
                "memory": "skipped",
                "vector": "skipped",
            },
        ),
    )

    report = run_write_gate(tmp_path, chapter=1, stage="postcommit")

    assert report["ok"] is True


def test_postcommit_gate_rejects_current_commit_without_binding(tmp_path):
    _make_init_ready(tmp_path)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        {
            "meta": {"chapter": 1, "status": "accepted"},
            "review_result": {"blocking_count": 0},
            "fulfillment_result": {
                "planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []
            },
            "disambiguation_result": {"pending": []},
            "extraction_result": {
                "accepted_events": [], "state_deltas": [], "entity_deltas": [], "summary_text": ""
            },
            "projection_status": {
                "state": "done", "index": "skipped", "summary": "skipped",
                "memory": "skipped", "vector": "skipped",
            },
        },
    )

    report = run_write_gate(tmp_path, chapter=1, stage="postcommit")

    assert report["ok"] is False
    assert any(
        item["code"] == "commit_chapter_binding_missing"
        for item in report["errors"]
    )


def test_postcommit_gate_rejects_commit_with_unbound_nested_artifact(tmp_path):
    _make_init_ready(tmp_path)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")
    payload = _valid_commit_payload(
        tmp_path,
        {
            "state": "done", "index": "skipped", "summary": "skipped",
            "memory": "skipped", "vector": "skipped",
        },
    )
    payload["extraction_result"].pop("chapter_binding")
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        payload,
    )

    report = run_write_gate(tmp_path, chapter=1, stage="postcommit")

    assert report["ok"] is False
    assert any(
        item["code"] == "commit_chapter_binding_invalid"
        and item["details"]["binding_code"] == "commit_schema_invalid"
        for item in report["errors"]
    )


def test_prewrite_blocks_when_prior_chapter_needs_revalidation(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=2)
    commits = tmp_path / ".story-system" / "commits"
    commits.mkdir(parents=True, exist_ok=True)
    (commits / "chapter_001.commit.json").write_text(
        json.dumps(
            {
                "meta": {
                    "schema_version": "story-system/v1",
                    "chapter": 1,
                    "status": "accepted",
                    "validation_status": "needs_revalidation",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_write_gate(tmp_path, chapter=2, stage="prewrite")

    assert report["ok"] is False
    assert any(
        item["code"] == "prior_chapter_needs_revalidation" for item in report["errors"]
    )


def test_prewrite_blocks_when_prior_commit_never_reached_read_models(tmp_path):
    """前章 accepted commit 未写入读模型（投影缺口）时，新章写作必须被拦。"""
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=2)
    chapter_path = tmp_path / "正文" / "第0001章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("第一章正文。", encoding="utf-8")
    binding = build_chapter_binding(tmp_path, 1)
    commits = tmp_path / ".story-system" / "commits"
    commits.mkdir(parents=True, exist_ok=True)
    (commits / "chapter_001.commit.json").write_text(
        json.dumps(
            {
                "meta": {
                    "schema_version": "story-system/v1",
                    "chapter": 1,
                    "status": "accepted",
                    "validation_status": "valid",
                },
                "chapter_binding": binding,
                "provenance": {"chapter_binding": binding},
                "extraction_result": {
                    "accepted_events": [],
                    "state_deltas": [],
                    "entity_deltas": [],
                    "chapter_binding": binding,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_write_gate(tmp_path, chapter=2, stage="prewrite")

    assert report["ok"] is False
    gap_errors = [
        item for item in report["errors"] if item["code"] == "projection_coverage_gap"
    ]
    assert gap_errors
    assert "projections retry" in gap_errors[0]["repair"]


def test_prewrite_allows_revalidating_the_earliest_stale_chapter(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    commits = tmp_path / ".story-system" / "commits"
    commits.mkdir(parents=True, exist_ok=True)
    (commits / "chapter_001.commit.json").write_text(
        json.dumps(
            {
                "meta": {
                    "schema_version": "story-system/v1",
                    "chapter": 1,
                    "status": "accepted",
                    "validation_status": "needs_revalidation",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_write_gate(tmp_path, chapter=1, stage="prewrite")

    assert not any(
        item["code"] == "prior_chapter_needs_revalidation" for item in report["errors"]
    )


def test_prewrite_blocks_when_prior_chapter_binding_is_stale(tmp_path):
    _make_init_ready(tmp_path)
    _make_current_contracts(tmp_path, chapter=1)
    chapter_path = tmp_path / "正文" / "第0001章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("第一章原文。\n", encoding="utf-8")
    from data_modules.chapter_commit_service import ChapterCommitService

    service = ChapterCommitService(tmp_path)
    binding = build_chapter_binding(tmp_path, 1)
    payload = service.build_commit(
        chapter=1,
        review_result=standard_review(binding),
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
            "chapter_binding": binding,
        },
    )
    service.persist_commit(payload)
    service.apply_projections(payload)

    chapter_path.write_text("第一章被改写且未重提。\n", encoding="utf-8")
    _make_current_contracts(tmp_path, chapter=3)
    report = run_write_gate(tmp_path, chapter=3, stage="prewrite")
    assert report["ok"] is False
    assert any(
        item["code"] == "prior_chapter_binding_stale" for item in report["errors"]
    )

    _make_current_contracts(tmp_path, chapter=1)
    binding = build_chapter_binding(tmp_path, 1)
    payload = service.build_commit(
        chapter=1,
        review_result=standard_review(binding),
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
            "chapter_binding": binding,
        },
    )
    service.persist_commit(payload)
    service.apply_projections(payload)

    _make_current_contracts(tmp_path, chapter=3)
    report = run_write_gate(tmp_path, chapter=3, stage="prewrite")
    assert not any(
        item["code"] == "prior_chapter_binding_stale" for item in report["errors"]
    )
