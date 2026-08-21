#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from data_modules.error_catalog import classify_issue, format_author_error, load_catalog


def test_error_catalog_loads_known_entries_and_fallback():
    entries, fallback = load_catalog()

    codes = {entry.code for entry in entries}
    assert "exact-version-conflict" in codes
    assert "migration_required" in codes
    assert "awaiting_human" in codes
    assert "ready_to_finalize" in codes
    assert "mainline_ready=false" in codes
    assert "projection pending" in codes
    assert fallback.matched is False
    assert fallback.severity == "must_handle"


def test_error_catalog_classifies_schema_error_by_code():
    result = classify_issue(
        {
            "code": "artifact.schema_error",
            "message": "field required: accepted_events",
        }
    )

    assert result.code == "artifact.schema_error"
    assert result.severity == "must_handle"
    assert result.auto_handle is False
    assert "中间结果格式不完整" in format_author_error(result)


def test_error_catalog_distinguishes_projection_pending_from_failed():
    pending = classify_issue(
        {
            "code": "projection_status_missing",
            "message": "projection pending: vector is missing",
        }
    )
    failed = classify_issue(
        {
            "code": "projection_failure",
            "message": "projection failed: vector timeout",
        }
    )

    assert pending.code == "projection pending"
    assert pending.severity == "needs_confirmation"
    assert failed.code == "projection failed"
    assert failed.severity == "must_handle"


def test_error_catalog_routes_exact_version_conflicts_to_refresh_only():
    for issue in (
        {
            "code": "canon_v3_decision_stage_precondition_failed",
            "message": "expected stage no longer current",
        },
        {
            "code": "canon_v3_head_conflict",
            "message": "expected old HEAD",
        },
        "canon_v3_author_axiom_finalize_token_precondition_failed",
    ):
        result = classify_issue(issue)
        assert result.code == "exact-version-conflict"
        assert result.severity == "needs_confirmation"
        assert result.command == "canon_ledger.py canon-v3 status"
        assert "不要自动重试" in result.next_action


def test_error_catalog_routes_each_v3_workflow_state_to_v3_action():
    cases = {
        "migration_required": ("migration_required", "canon-v3 status"),
        "awaiting_human": ("awaiting_human", "/canon-ledger-confirm"),
        "rewrite_required": ("rewrite_required", "/canon-ledger-write"),
        "recompile_required": ("recompile_required", "canon-v3 status"),
        "ready_to_finalize": ("ready_to_finalize", "/canon-ledger-confirm"),
        "projection_rebuild_required": (
            "projection_rebuild_required",
            "canon-v3 rebuild-projection",
        ),
    }
    for state, (expected_code, command_fragment) in cases.items():
        result = classify_issue({"code": state, "message": f"state={state}"})
        assert result.code == expected_code
        assert command_fragment in result.command


def test_error_catalog_routes_author_axiom_recovery_away_from_chapter_write():
    rewrite = classify_issue(
        {
            "code": "rewrite_required",
            "transaction_kind": "author_axiom",
            "message": "state=rewrite_required",
        }
    )
    recompile = classify_issue(
        {
            "code": "recompile_required",
            "workflow_snapshot": {"transaction_kind": "author_axiom"},
        }
    )
    exact_error = classify_issue(
        {"code": "canon_v3_author_axiom_finalize_rewrite_required"}
    )

    assert rewrite.code == "author_axiom_rewrite_required"
    assert rewrite.command == "/canon-ledger-plan"
    assert "章节正文" in rewrite.impact
    assert recompile.code == "author_axiom_recompile_required"
    assert recompile.command == "/canon-ledger-plan"
    assert exact_error.code == "author_axiom_rewrite_required"
    assert exact_error.command == "/canon-ledger-plan"


def test_error_catalog_preserves_legacy_aliases_but_uses_v3_recovery():
    rejected = classify_issue(
        {"code": "chapter-commit rejected", "message": "legacy writer failed"}
    )
    pending = classify_issue(
        {"code": "projection_status_missing", "message": "projection pending"}
    )

    assert rejected.code == "chapter-commit rejected"
    assert rejected.command == "canon_ledger.py canon-v3 status"
    assert "chapter-commit" not in rejected.command
    assert pending.code == "projection pending"
    assert pending.command == "canon_ledger.py canon-v3 status"
    assert "replay" not in pending.next_action


def test_error_catalog_routes_corrupt_head_or_staging_to_deep_doctor():
    for code in ("canon_v3_current_invalid", "canon_v3_staging_invalid"):
        result = classify_issue({"code": code, "message": code})
        assert result.code == "canon_v3_authority_invalid"
        assert result.command == "canon_ledger.py doctor --deep"


def test_error_catalog_classifies_rag_fallback_as_auto_handled():
    result = classify_issue("RAG fallback used because vector search timed out")

    assert result.code == "rag degraded"
    assert result.severity == "auto_handled"
    assert result.auto_handle is True


def test_error_catalog_unknown_error_honestly_falls_back():
    result = classify_issue({"code": "new.runtime.error", "message": "unexpected traceback"})

    assert result.matched is False
    assert result.code == "unknown"
    assert "/canon-ledger-doctor" in result.next_action
