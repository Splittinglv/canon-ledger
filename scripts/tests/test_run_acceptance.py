from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from run_acceptance import (  # noqa: E402
    SMOKE_TESTS,
    acceptance_steps,
    frontend_dependency_error,
)


def test_smoke_acceptance_uses_only_current_authority_tests(tmp_path):
    steps = acceptance_steps(
        "smoke",
        python_executable="python-test",
        npm_executable="npm-test",
        base_temp=tmp_path / "base",
    )

    assert len(steps) == 1
    command = steps[0]
    assert command[:3] == ["python-test", "-m", "pytest"]
    assert tuple(command[3 : 3 + len(SMOKE_TESTS)]) == SMOKE_TESTS
    assert all("memory_contract_adapter" not in path for path in SMOKE_TESTS)
    assert all("projections_cli" not in path for path in SMOKE_TESTS)


def test_full_acceptance_runs_all_pytest_testpaths_and_release_independent_checks(
    tmp_path,
):
    steps = acceptance_steps(
        "full",
        python_executable="python-test",
        npm_executable="npm-test",
        base_temp=tmp_path / "base",
    )

    pytest_command = steps[0]
    assert pytest_command[:3] == ["python-test", "-m", "pytest"]
    assert "scripts/data_modules/tests" not in pytest_command
    assert "tests/adapter" not in pytest_command
    assert ["python-test", "scripts/run_behavior_evals.py", "--suite", "fast"] in steps
    assert ["python-test", "scripts/validate_document_links.py"] in steps
    assert [
        "python-test",
        "scripts/validate_plugin_package.py",
        "--strict",
        "--format",
        "json",
    ] in steps
    assert ["npm-test", "--prefix", "dashboard/frontend", "test"] in steps
    assert [
        "npm-test",
        "--prefix",
        "dashboard/frontend",
        "run",
        "build",
    ] in steps


def test_platform_wrappers_share_the_python_acceptance_runner():
    powershell = (SCRIPTS_DIR / "run_tests.ps1").read_text(encoding="utf-8")
    posix = (SCRIPTS_DIR / "run_tests.sh").read_text(encoding="utf-8")

    assert "run_acceptance.py" in powershell
    assert "run_acceptance.py" in posix
    assert "scripts/data_modules/tests" not in powershell
    assert "scripts/data_modules/tests" not in posix


def test_full_acceptance_missing_frontend_dependencies_has_actionable_setup_error(tmp_path):
    message = frontend_dependency_error(tmp_path)

    assert "Node.js 18+" in message
    assert "npm --prefix dashboard/frontend ci" in message
