#!/usr/bin/env python3
"""Closed public-command policy shared by the CLI and runtime-write Hook.

The unified CLI intentionally keeps a few compatibility parsers so old
installations receive an actionable refusal instead of an argparse surprise.
That compatibility surface is not permission to execute a retired writer.
This module is deliberately dependency-free so the pre-execution Hook can
apply the exact same policy before trusting ``scripts/canon_ledger.py``.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
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
        "story-events",
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


def _strip_global_options(argv: Sequence[str]) -> tuple[list[str], bool, str]:
    """Remove only wrapper-owned global options, wherever compatibility permits."""

    out: list[str] = []
    legacy_read_only = False
    project_root = ""
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
                return [], legacy_read_only, project_root
            if project_root:
                return [], legacy_read_only, project_root
            project_root = values[index + 1]
            index += 2
            continue
        if token.startswith("--project-root="):
            if project_root:
                return [], legacy_read_only, project_root
            project_root = token.split("=", 1)[1]
            if not project_root:
                return [], legacy_read_only, project_root
            index += 1
            continue
        out.append(token)
        index += 1
    return out, legacy_read_only, project_root


def _option_values(arguments: Sequence[str], name: str) -> list[str] | None:
    """Return exact values for one option, or ``None`` for malformed syntax."""

    flag = f"--{name}"
    values: list[str] = []
    index = 0
    args = [str(item) for item in arguments]
    while index < len(args):
        token = args[index]
        if token == flag:
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                return None
            values.append(args[index + 1])
            index += 2
            continue
        if token.startswith(flag + "="):
            value = token.split("=", 1)[1]
            if not value:
                return None
            values.append(value)
        index += 1
    return values


def _normalized_parts(value: str) -> tuple[str, ...]:
    normalized = str(value or "").strip().replace("\\", "/")
    return tuple(part for part in normalized.split("/") if part not in {"", "."})


def _path_has_parent_escape(value: str) -> bool:
    return ".." in _normalized_parts(value)


def _matches_project_path(
    raw_path: str,
    project_root: str,
    *,
    expected_relative: str | None = None,
    relative_prefix: str | None = None,
) -> bool:
    """Authorize one project path role without trusting string prefixes.

    The Hook may see the canonical Skill spelling ``${PROJECT_ROOT}/...``
    before the shell expands it.  The runtime sees the concrete path and
    repeats the same role check before any filesystem access.
    """

    raw = str(raw_path or "").strip()
    if not raw or "\x00" in raw or _path_has_parent_escape(raw):
        return False
    normalized = raw.replace("\\", "/")
    expected = str(expected_relative or "").strip("/")
    prefix = str(relative_prefix or "").strip("/")

    for root_token in ("${PROJECT_ROOT}", "$PROJECT_ROOT"):
        token_prefix = root_token + "/"
        if normalized.startswith(token_prefix):
            normalized = normalized[len(token_prefix) :]
            break

    relative = normalized[2:] if normalized.startswith("./") else normalized
    if expected and relative == expected:
        return True
    if prefix and (relative == prefix or relative.startswith(prefix + "/")):
        return bool(relative != prefix and relative.rsplit("/", 1)[-1])

    root_raw = str(project_root or "").strip()
    if not root_raw or root_raw in {"${PROJECT_ROOT}", "$PROJECT_ROOT"}:
        return False
    if "\x00" in root_raw or _path_has_parent_escape(root_raw):
        return False

    # Windows paths must be compared with Windows path semantics even when the
    # Hook itself happens to run on POSIX (for example in package tests).
    windows_style = bool(
        PureWindowsPath(raw).drive
        or PureWindowsPath(root_raw).drive
        or raw.startswith("\\\\")
        or root_raw.startswith("\\\\")
    )
    try:
        if windows_style:
            root_norm = ntpath.normcase(ntpath.abspath(root_raw))
            path_norm = ntpath.normcase(ntpath.abspath(raw))
            rel = ntpath.relpath(path_norm, root_norm).replace("\\", "/")
        else:
            root_norm = posixpath.abspath(os.path.expanduser(root_raw))
            path_norm = posixpath.abspath(os.path.expanduser(raw))
            rel = posixpath.relpath(path_norm, root_norm)
    except (OSError, ValueError):
        return False
    if rel == ".." or rel.startswith("../") or _path_has_parent_escape(rel):
        return False
    if expected:
        return rel == expected
    return bool(prefix and rel.startswith(prefix + "/") and rel.rsplit("/", 1)[-1])


def _project_path_options_are_safe(
    arguments: Sequence[str],
    project_root: str,
    option_roles: dict[str, tuple[str, str]],
) -> bool:
    """Validate every occurrence of registered project path options."""

    for option, (role_kind, role_value) in option_roles.items():
        exact_flag = f"--{option}"
        for raw_token in arguments:
            token_name = str(raw_token).split("=", 1)[0]
            if (
                token_name.startswith("--")
                and token_name != exact_flag
                and exact_flag.startswith(token_name)
            ):
                # argparse normally accepts long-option abbreviations.  The
                # shared Hook policy cannot let an abbreviated path flag evade
                # role validation even though the runtime writer is also safe.
                return False
        values = _option_values(arguments, option)
        if values is None or len(values) > 1:
            return False
        for value in values:
            if role_kind == "exact":
                ok = _matches_project_path(
                    value,
                    project_root,
                    expected_relative=role_value,
                )
            else:
                ok = _matches_project_path(
                    value,
                    project_root,
                    relative_prefix=role_value,
                )
            if not ok:
                return False
    return True


_INIT_FORBIDDEN_COMPONENTS = frozenset(
    {".story-system", ".canon-ledger", ".cursor", ".git"}
)
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_INIT_VALUE_OPTIONS = frozenset(
    {
        "--protagonist-name",
        "--target-words",
        "--target-chapters",
        "--golden-finger-name",
        "--golden-finger-type",
        "--golden-finger-style",
        "--core-selling-points",
        "--protagonist-structure",
        "--heroine-config",
        "--heroine-names",
        "--heroine-role",
        "--co-protagonists",
        "--co-protagonist-roles",
        "--antagonist-tiers",
        "--world-scale",
        "--factions",
        "--power-system-type",
        "--social-class",
        "--resource-distribution",
        "--gf-visibility",
        "--gf-irreversible-cost",
        "--currency-system",
        "--currency-exchange",
        "--sect-hierarchy",
        "--cultivation-chain",
        "--cultivation-subtiers",
        "--protagonist-desire",
        "--protagonist-flaw",
        "--protagonist-archetype",
        "--antagonist-level",
        "--target-reader",
        "--platform",
    }
)


def _init_target_is_safe(arguments: Sequence[str]) -> bool:
    positionals: list[str] = []
    values = [str(item) for item in arguments]
    index = 0
    while index < len(values):
        token = values[index]
        option_name = token.split("=", 1)[0]
        if option_name in _INIT_VALUE_OPTIONS:
            if "=" not in token:
                if index + 1 >= len(values):
                    return False
                index += 2
            else:
                if not token.split("=", 1)[1]:
                    return False
                index += 1
            continue
        if token.startswith("--"):
            # Unknown or abbreviated init options are not part of the public
            # capability grammar; argparse must not reinterpret their values.
            return False
        positionals.append(token)
        index += 1
    if len(positionals) != 3:
        return False
    target = str(positionals[0] or "").strip()
    if not target or "\x00" in target or _path_has_parent_escape(target):
        return False
    if not any(marker in target for marker in ("$", "%", "<", ">")):
        try:
            candidate = Path(target).expanduser().resolve(strict=False)
        except OSError:
            return False
        try:
            candidate.relative_to(_PLUGIN_ROOT)
        except ValueError:
            pass
        else:
            return False
    return not any(
        part.casefold() in _INIT_FORBIDDEN_COMPONENTS
        for part in _normalized_parts(target)
    )


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

    normalized, _legacy_read_only, project_root = _strip_global_options(argv)
    if not normalized:
        return _deny("", "missing-command")
    tool = normalized[0].lower()
    rest = normalized[1:]

    if tool in _KNOWN_PUBLIC_TOOLS and rest in (["-h"], ["--help"]):
        return PublicCommandDecision(allowed=True, tool=tool, operation="help")

    if tool == "canon-v3":
        operation = _subcommand(rest)
        if operation in _CANON_V3_ACTIONS:
            # Every public JSON input is an untrusted, non-authoritative
            # scratch artifact.  It may never alias CURRENT, an immutable
            # object, a projection, author content or a path outside the book.
            if not _project_path_options_are_safe(
                rest,
                project_root,
                {
                    "input-file": ("prefix", ".canon-ledger/tmp"),
                    "candidate-file": ("prefix", ".canon-ledger/tmp"),
                    "reviewer-file": ("prefix", ".canon-ledger/tmp"),
                },
            ):
                return _deny(tool, f"{operation}:unsafe-path")
            return PublicCommandDecision(True, tool, operation)
        return _deny(tool, operation or "missing-action")

    if tool in _EXACT_SUBCOMMANDS:
        operation = _subcommand(rest)
        if operation in _EXACT_SUBCOMMANDS[tool]:
            if tool == "memory-contract" and operation == "export-asof":
                if not _project_path_options_are_safe(
                    rest,
                    project_root,
                    {
                        "out": (
                            "exact",
                            ".canon-ledger/tmp/asof_snapshot.json",
                        )
                    },
                ):
                    return _deny(tool, f"{operation}:unsafe-output")
            if tool == "style-memory" and operation == "add-item":
                if not _project_path_options_are_safe(
                    rest,
                    project_root,
                    {"input-file": ("prefix", ".canon-ledger/tmp")},
                ):
                    return _deny(tool, f"{operation}:unsafe-input")
            return PublicCommandDecision(True, tool, operation)
        return _deny(tool, operation or "missing-action")

    if tool == "init":
        if project_root:
            return _deny(tool, "ambiguous-project-root")
        if _init_target_is_safe(rest):
            return PublicCommandDecision(True, tool, "initialize")
        return _deny(tool, "unsafe-target")

    if tool == "chapter-binding":
        if not _project_path_options_are_safe(
            rest,
            project_root,
            {
                "out": (
                    "exact",
                    ".canon-ledger/tmp/chapter_binding.json",
                )
            },
        ):
            return _deny(tool, "unsafe-output")
        return PublicCommandDecision(True, tool, _operation(rest))

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

    if tool == "story-events":
        replacement = (
            "canon_ledger.py canon-v3 status"
            if "--health" in rest
            else "canon_ledger.py canon-v3 query snapshot"
        )
        return PublicCommandDecision(
            allowed=False,
            tool=tool,
            operation="retired",
            error="canon_v3_public_command_disabled",
            replacement=replacement,
        )

    # Explicitly includes update-state, chapter-commit, projections,
    # review-pipeline and master-outline-sync.  Their parsers remain only to
    # return the stable retirement error from the unified entrypoint.
    return _deny(tool, _operation(rest) or "retired")


__all__ = [
    "LEGACY_READ_COMMANDS",
    "PublicCommandDecision",
    "evaluate_public_command",
]
