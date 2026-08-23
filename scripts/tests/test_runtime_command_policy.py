#!/usr/bin/env python3
"""Current-authority tests for the unified CLI/Hook capability closure."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime_command_policy import evaluate_public_command


@pytest.mark.current_authority
@pytest.mark.parametrize(
    "argv",
    [
        ["canon-v3", "status"],
        ["--project-root", "/book", "canon-v3", "prepare", "--input-file", "proposal.json"],
        ["canon-v3", "planning", "refresh-contracts", "--chapter", "3"],
        ["init", "/new-book", "书名", "玄幻"],
        ["where"],
        ["preflight", "--format", "json"],
        ["project-status", "--format", "json"],
        ["doctor", "--deep"],
        ["write-gate", "--chapter", "3", "--stage", "prewrite"],
        ["chapter-binding", "--chapter", "3", "--out", "binding.json"],
        ["run-ledger", "record-write-step", "--chapter", "3"],
        ["run-log", "--event", "test"],
        ["subagent-models"],
        ["use", "/book"],
        ["status"],
        ["story-events", "--health"],
        ["memory-contract", "export-asof", "--chapter", "3", "--out", "snapshot.json"],
        ["style-memory", "add-item", "--input-file", "style.json"],
        ["style-memory", "show"],
        ["placeholder-scan"],
        ["knowledge", "query-entity-state", "--entity", "林默", "--at-chapter", "3"],
        ["human-review", "list", "--chapter", "3"],
        ["story-system", "玄幻", "--genre", "东方玄幻", "--format=json"],
    ],
)
def test_current_production_capabilities_are_explicitly_allowed(argv: list[str]) -> None:
    decision = evaluate_public_command(argv)

    assert decision.allowed is True, decision
    assert decision.error == ""


@pytest.mark.current_authority
@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--project-root"],
        ["unknown", "--help"],
        ["canon-v3", "unknown-action"],
        ["human-review", "resolve", "--input-file", "decisions.json"],
        ["style-memory", "delete-all"],
        ["memory-contract", "commit-chapter"],
        ["backup", "--list"],
        ["backup", "--diff", "1", "2"],
        ["backup", "--rollback", "3"],
        ["backup", "--chapter", "3"],
        ["archive", "--stats"],
        ["archive", "--auto-check", "--dry-run"],
        ["archive", "--force"],
        ["archive", "--restore-character", "林默"],
        ["story-system", "玄幻", "--persist"],
        ["story-system", "玄幻", "--pers"],
        ["story-system", "玄幻", "--emit-runtime-contracts", "--chapter", "3"],
        ["story-system", "玄幻", "--emit", "--chapter", "3"],
        ["update-state", "add-review"],
        ["chapter-commit", "--chapter", "3"],
        ["projections", "retry", "--chapter", "3"],
        ["review-pipeline", "--chapter", "3"],
        ["master-outline-sync", "--volume", "2"],
        ["index", "stats"],
        ["--legacy-read-only", "index", "stats"],
        ["state", "--legacy-read-only", "get-progress"],
        ["rag", "stats", "--legacy-read-only"],
        ["entity", "list-aliases", "--legacy-read-only"],
        ["memory", "--legacy-read-only", "stats"],
    ],
)
def test_retired_or_side_effecting_capabilities_fail_closed(argv: list[str]) -> None:
    decision = evaluate_public_command(argv)

    assert decision.allowed is False
    assert decision.error == "canon_v3_public_command_disabled"
    assert decision.replacement.startswith("canon_ledger.py canon-v3")


@pytest.mark.current_authority
def test_wrapper_global_options_do_not_change_the_selected_capability() -> None:
    front = evaluate_public_command(
        ["--project-root", "/book", "--legacy-read-only", "index", "stats"]
    )
    trailing = evaluate_public_command(
        ["index", "stats", "--legacy-read-only", "--project-root=/book"]
    )

    assert front.allowed is False
    assert trailing.allowed is False
    assert (front.tool, front.operation) == ("index", "stats")
    assert (trailing.tool, trailing.operation) == ("index", "stats")
