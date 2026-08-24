#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-platform current-product acceptance runner.

PowerShell and POSIX wrappers delegate here so ``full`` cannot accidentally
exercise only one test directory.  The runner deliberately excludes release
tag/version checks, which need explicit release inputs; it covers the complete
current product, behavioral/package semantics, documentation links, and the
Dashboard source/build pair.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Sequence


SMOKE_TESTS = (
    "scripts/data_modules/tests/test_canon_v3_public_protocol.py",
    "scripts/data_modules/tests/test_canon_v3_agent_protocol.py",
    "scripts/data_modules/tests/test_canon_v3_public_query.py",
    "scripts/data_modules/tests/test_canon_v3_read_purity.py",
    "tests/adapter/test_runtime_smoke.py",
)


def frontend_dependency_error(root: Path) -> str:
    """Return an actionable setup error before a long full run starts."""

    bin_root = root / "dashboard" / "frontend" / "node_modules" / ".bin"
    if not any((bin_root / name).is_file() for name in ("vite", "vite.cmd")):
        return (
            "Dashboard dependencies are missing; install Node.js 18+ and run "
            "`npm --prefix dashboard/frontend ci`"
        )
    return ""


def acceptance_steps(
    mode: str,
    *,
    python_executable: str,
    npm_executable: str | None,
    base_temp: Path,
) -> list[list[str]]:
    pytest_command = [
        python_executable,
        "-m",
        "pytest",
    ]
    if mode == "smoke":
        return [
            [
                *pytest_command,
                *SMOKE_TESTS,
                "--basetemp",
                str(base_temp),
                "-p",
                "no:cacheprovider",
            ]
        ]
    if mode != "full":
        raise ValueError(f"unknown acceptance mode: {mode}")
    if not npm_executable:
        raise RuntimeError("full acceptance requires npm on PATH")
    return [
        [
            *pytest_command,
            "--basetemp",
            str(base_temp),
            "-p",
            "no:cacheprovider",
        ],
        [python_executable, "scripts/run_behavior_evals.py", "--suite", "fast"],
        [python_executable, "scripts/validate_document_links.py"],
        [
            python_executable,
            "scripts/validate_plugin_package.py",
            "--strict",
            "--format",
            "json",
        ],
        [npm_executable, "--prefix", "dashboard/frontend", "test"],
        [npm_executable, "--prefix", "dashboard/frontend", "run", "build"],
    ]


def _run(command: Sequence[str], *, root: Path, environment: dict[str, str]) -> None:
    rendered = subprocess.list2cmdline([str(item) for item in command])
    print(f"\n>>> {rendered}", flush=True)
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=root,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def _resolve_root(raw: str | None) -> Path:
    root = (
        Path(raw).expanduser()
        if raw
        else Path(__file__).resolve().parent.parent
    ).resolve()
    if not (root / "pytest.ini").is_file() or not (
        root / "scripts" / "canon_ledger.py"
    ).is_file():
        raise ValueError(f"not a CanonLedger repository root: {root}")
    return root


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run cross-platform CanonLedger current-product acceptance"
    )
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--project-root", default="")
    args = parser.parse_args()

    try:
        root = _resolve_root(args.project_root or None)
        identity = uuid.uuid5(uuid.NAMESPACE_URL, str(root)).hex[:12]
        temp_root = (
            Path(tempfile.gettempdir()).resolve()
            / f"canon-ledger-acceptance-{identity}"
        )
        temp_root.mkdir(parents=True, exist_ok=True)
        base_temp = temp_root / f"acceptance-{args.mode}-{uuid.uuid4().hex}"
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if args.mode == "full":
            dependency_error = frontend_dependency_error(root)
            if dependency_error:
                raise RuntimeError(dependency_error)
        steps = acceptance_steps(
            args.mode,
            python_executable=sys.executable,
            npm_executable=npm,
            base_temp=base_temp,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ACCEPTANCE_SETUP_FAILED: {exc}", file=sys.stderr)
        return 2

    environment = dict(os.environ)
    for key in ("TMP", "TEMP", "TMPDIR"):
        environment[key] = str(temp_root)
    environment["CANON_LEDGER_TEST_TEMP_ROOT"] = str(temp_root)
    scripts_path = str(root / "scripts")
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        scripts_path
        if not existing_pythonpath
        else scripts_path + os.pathsep + existing_pythonpath
    )
    if args.mode == "full":
        environment["CANON_LEDGER_REQUIRE_CURRENT_AUTHORITY"] = "1"

    print(f"ProjectRoot: {root}")
    print(f"Mode: {args.mode}")
    print(f"Python: {sys.executable}")
    print(f"TempRoot: {temp_root}")
    for command in steps:
        _run(command, root=root, environment=environment)
    print(f"\nAcceptance {args.mode}: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
