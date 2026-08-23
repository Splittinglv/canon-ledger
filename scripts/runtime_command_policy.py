#!/usr/bin/env python3
"""Closed public-command policy shared by the CLI and runtime-write Hook.

The unified CLI intentionally keeps a few compatibility parsers so old
installations receive an actionable refusal instead of an argparse surprise.
That compatibility surface is not permission to execute a retired writer.
This module is deliberately dependency-free so the pre-execution Hook can
apply the exact same policy before trusting ``scripts/canon_ledger.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


LEGACY_READ_COMMANDS: dict[str, frozenset[str]] = {
    "memory": frozenset({"stats", "query", "dump", "conflicts"}),
    "rag": frozenset({"stats", "search"}),
    "entity": frozenset({"lookup", "lookup-all", "list-aliases"}),
    "state": frozenset({"get-progress", "get-entity", "list-entities"}),
    "index": frozenset(
        {
            "stats",
            "get-chapter",
            "recent-appearances",
            "entity-appearances",
            "search-scenes",
            "get-entity",
            "get-core-entities",
            "get-protagonist",
            "get-entities-by-type",
            "get-by-alias",
            "get-aliases",
            "get-relationships",
            "get-relationship-events",
            "get-relationship-graph",
            "get-relationship-timeline",
            "get-state-changes",
            "list-invalid",
            "get-recent-review-metrics",
            "get-review-trend-stats",
            "get-writing-checklist-score",
            "get-recent-writing-checklist-scores",
            "get-writing-checklist-score-trend",
            "get-debt-summary",
            "get-recent-reading-power",
            "get-chapter-reading-power",
            "get-pattern-usage-stats",
            "get-hook-type-stats",
            "get-reader-signals",
            "get-pending-overrides",
            "get-overdue-overrides",
            "get-active-debts",
            "get-overdue-debts",
        }
    ),
}


_UNCONDITIONALLY_ALLOWED_TOOLS = frozenset(
    {
        # Clean-only bootstrap; init_project enforces the empty-target contract.
        "init",
        # Canon v3 is the sole factual mutation plane, including its bounded
        # planning facade and detached migration/recertification workflows.
        # HEAD-bound/read-only diagnostics and v3 gate helpers.
        "where",
        "preflight",
        "project-status",
        "doctor",
        "write-gate",
        "chapter-binding",
        "user-report",
        "run-log",
        "subagent-models",
        "use",
        "status",
        "story-events",
        "placeholder-scan",
    }
)

_CANON_V3_ACTIONS = frozenset(
    {
        "initialize",
        "prepare",
        "decide",
        "finalize",
        "archive-staging",
        "cancel",
        "author-axiom-prepare",
        "author-axiom-decide",
        "author-axiom-finalize",
        "author-axiom-status",
        "author-axioms",
        "status",
        "rebuild-projection",
        "history",
        "query",
        "agent-schema",
        "validate-agent-output",
        "assemble-proposal",
        "planning",
        "historical-export",
        "migrate",
        "audit-cutover",
        "repair-cutover",
    }
)

_EXACT_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "human-review": frozenset({"list"}),
    "run-ledger": frozenset({"record-write-step", "write-resume"}),
    "memory-contract": frozenset(
        {
            "load-context",
            "query-entity",
            "query-rules",
            "read-summary",
            "get-open-loops",
            "get-obligations",
            "get-timeline",
            "export-asof",
        }
    ),
    "style-memory": frozenset({"add-item", "show"}),
    "knowledge": frozenset({"query-entity-state", "query-relationships"}),
}

_KNOWN_PUBLIC_TOOLS = frozenset(
    set(_UNCONDITIONALLY_ALLOWED_TOOLS)
    | set(_EXACT_SUBCOMMANDS)
    | set(LEGACY_READ_COMMANDS)
    | {
        "canon-v3",
        "backup",
        "archive",
        "story-system",
        "update-state",
        "projections",
        "chapter-commit",
        "review-pipeline",
        "master-outline-sync",
    }
)


@dataclass(frozen=True)
class PublicCommandDecision:
    allowed: bool
    tool: str = ""
    operation: str = ""
    error: str = ""
    replacement: str = "canon_ledger.py canon-v3 status"


def _strip_global_options(argv: Sequence[str]) -> tuple[list[str], bool]:
    """Remove only wrapper-owned global options, wherever compatibility permits."""

    out: list[str] = []
    legacy_read_only = False
    index = 0
    values = [str(item) for item in argv]
    while index < len(values):
        token = values[index]
        if token == "--legacy-read-only":
            legacy_read_only = True
            index += 1
            continue
        if token == "--project-root":
            if index + 1 >= len(values):
                # Let argparse report the malformed invocation.  It is never
                # treated as an allowed runtime capability by the Hook.
                return [], legacy_read_only
            index += 2
            continue
        if token.startswith("--project-root="):
            index += 1
            continue
        out.append(token)
        index += 1
    return out, legacy_read_only


def _operation(arguments: Sequence[str]) -> str:
    for token in arguments:
        value = str(token).strip()
        if value and not value.startswith("-"):
            return value.lower()
    for token in arguments:
        value = str(token).strip().lower()
        if value.startswith("--"):
            return value[2:]
    return ""


def _subcommand(arguments: Sequence[str]) -> str:
    if not arguments:
        return ""
    value = str(arguments[0]).strip().lower()
    return "" if value.startswith("-") else value


def _first_option(arguments: Sequence[str]) -> str:
    for token in arguments:
        value = str(token).strip().lower()
        if value.startswith("--"):
            return value[2:].split("=", 1)[0]
    return ""


def _story_system_is_render_only(arguments: Sequence[str]) -> bool:
    """Accept only the exact stdout-render grammar, with no argparse aliases."""

    value_options = {"--genre", "--chapter", "--csv-dir", "--format"}
    positional_count = 0
    index = 0
    values = [str(item) for item in arguments]
    while index < len(values):
        token = values[index]
        if token in value_options:
            if index + 1 >= len(values):
                return False
            index += 2
            continue
        if any(token.startswith(option + "=") for option in value_options):
            index += 1
            continue
        if token.startswith("-"):
            return False
        positional_count += 1
        index += 1
    return positional_count == 1


def _deny(tool: str, operation: str = "") -> PublicCommandDecision:
    replacement = "canon_ledger.py canon-v3 status"
    if tool in {"story-system", "master-outline-sync"}:
        replacement = "canon_ledger.py canon-v3 planning refresh-contracts"
    return PublicCommandDecision(
        allowed=False,
        tool=tool,
        operation=operation,
        error="canon_v3_public_command_disabled",
        replacement=replacement,
    )


def evaluate_public_command(argv: Sequence[str]) -> PublicCommandDecision:
    """Classify arguments following ``canon_ledger.py`` using a semantic closure.

    A result of ``allowed`` means only that this public capability is part of
    the production surface.  Argparse and the called implementation still
    validate its complete schema, project binding, digests and CAS tokens.
    """

    normalized, _legacy_read_only = _strip_global_options(argv)
    if not normalized:
        return _deny("", "missing-command")
    tool = normalized[0].lower()
    rest = normalized[1:]

    if tool in _KNOWN_PUBLIC_TOOLS and rest in (["-h"], ["--help"]):
        return PublicCommandDecision(allowed=True, tool=tool, operation="help")

    if tool == "canon-v3":
        operation = _subcommand(rest)
        if operation in _CANON_V3_ACTIONS:
            return PublicCommandDecision(True, tool, operation)
        return _deny(tool, operation or "missing-action")

    if tool in _EXACT_SUBCOMMANDS:
        operation = _subcommand(rest)
        if operation in _EXACT_SUBCOMMANDS[tool]:
            return PublicCommandDecision(True, tool, operation)
        return _deny(tool, operation or "missing-action")

    if tool in _UNCONDITIONALLY_ALLOWED_TOOLS:
        return PublicCommandDecision(
            allowed=True,
            tool=tool,
            operation=_operation(rest),
        )

    if tool in LEGACY_READ_COMMANDS:
        operation = _subcommand(rest)
        # These adapters were historically labelled read-only, but opening
        # their stores can create SQLite files, initialize schemas or record
        # observations.  Migration diagnostics must use the pure Canon v3
        # audit-cutover / repair-cutover --dry-run paths instead.
        return _deny(tool, operation or "missing-read-command")

    if tool == "backup":
        # Even list/diff construct GitBackupManager, which auto-initializes a
        # missing repository and writes .gitignore.  No operation is a pure
        # production diagnostic.
        return _deny(tool, _first_option(rest) or "mutation")

    if tool == "archive":
        # ArchiveManager's constructor creates archive directories and opens an
        # IndexManager that initializes SQLite schemas.  "stats" and dry-run
        # therefore are not read-only capabilities either.
        return _deny(tool, _first_option(rest) or "mutation")

    if tool == "story-system":
        # Generation to stdout is advisory planning.  Both persistence flags
        # write legacy story-system state and therefore have no production path.
        if _story_system_is_render_only(rest):
            return PublicCommandDecision(True, tool, "render")
        first_option = _first_option(rest)
        option_names = [
            str(item).strip().lower()[2:].split("=", 1)[0]
            for item in rest
            if str(item).strip().startswith("--")
        ]
        if any(name.startswith("p") for name in option_names):
            operation = "persist"
        elif any(name.startswith("e") for name in option_names):
            operation = "emit-runtime-contracts"
        else:
            operation = first_option or "invalid-render"
        return _deny(tool, operation)

    # Explicitly includes update-state, chapter-commit, projections,
    # review-pipeline and master-outline-sync.  Their parsers remain only to
    # return the stable retirement error from the unified entrypoint.
    return _deny(tool, _operation(rest) or "retired")


__all__ = [
    "LEGACY_READ_COMMANDS",
    "PublicCommandDecision",
    "evaluate_public_command",
]
