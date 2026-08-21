from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest


_ORIGINAL_SQLITE_CONNECT = sqlite3.connect
_ORIGINAL_TEMPORARY_DIRECTORY = tempfile.TemporaryDirectory

# These modules exercise the removed v2 fact-writing product as an active
# workflow.  Canon v3 keeps dedicated read-only migration/recertification
# coverage instead; allowing these expectations to drive the default suite
# would require reopening the retired writers.  Set the documented environment
# switch only when studying the frozen v2 specification itself.
_RETIRED_V2_MODULES = frozenset(
    {
        "scripts/data_modules/tests/test_chapter_commit_service.py",
        "scripts/data_modules/tests/test_consistency_projection_p0.py",
        "scripts/data_modules/tests/test_context_canon_asof.py",
        "scripts/data_modules/tests/test_human_review.py",
        "scripts/data_modules/tests/test_memory_contract_adapter.py",
        "scripts/data_modules/tests/test_projection_log.py",
        "scripts/data_modules/tests/test_projection_rebuild.py",
        "scripts/data_modules/tests/test_projection_writers.py",
        "scripts/data_modules/tests/test_projections_cli.py",
        "scripts/data_modules/tests/test_rag_context.py",
        "scripts/data_modules/tests/test_story_runtime_health.py",
        "scripts/data_modules/tests/test_story_runtime_sources.py",
        "scripts/data_modules/tests/test_user_report.py",
        "scripts/data_modules/tests/test_write_gates.py",
    }
)

_RETIRED_V2_TESTS = frozenset(
    {
        "scripts/data_modules/tests/test_canon_ledger_cli.py::test_canon_ledger_commit_forwards",
        "scripts/data_modules/tests/test_canon_ledger_cli.py::test_human_review_cli_lists_and_records_decisions",
        "scripts/data_modules/tests/test_canon_ledger_cli.py::test_human_review_cli_routes_review_rewrite_back_to_write",
        "scripts/data_modules/tests/test_canon_ledger_cli.py::test_project_status_cli_outputs_json_without_reusing_status",
        "scripts/data_modules/tests/test_canon_ledger_cli.py::test_user_report_cli_outputs_json",
        "scripts/data_modules/tests/test_canon_ledger_cli.py::test_write_gate_cli_runs_prewrite",
        "scripts/data_modules/tests/test_canon_ledger_cli.py::test_projections_retry_cli_runs",
        "scripts/data_modules/tests/test_canon_ledger_cli.py::test_review_pipeline_forwards_with_resolved_project_root",
        "scripts/data_modules/tests/test_canon_ledger_cli.py::test_canon_ledger_skill_flow_runs_story_contract_context_and_review_pipeline_with_stubbed_vector_model",
        "scripts/data_modules/tests/test_coverage_boost.py::test_canon_ledger_passthrough_entity",
        "scripts/data_modules/tests/test_coverage_boost.py::test_canon_ledger_passthrough_update_state_script",
        "scripts/data_modules/tests/test_dashboard_app.py::test_dashboard_chapter_trend_endpoint_returns_recent_window",
        "scripts/data_modules/tests/test_dashboard_app.py::test_dashboard_commits_and_contract_summary_endpoints",
        "scripts/data_modules/tests/test_dashboard_app.py::test_dashboard_env_status_endpoints_report_local_rag_state",
        "scripts/data_modules/tests/test_memory_cli.py::test_query_entity_found",
        "scripts/data_modules/tests/test_memory_cli.py::test_read_summary_exists",
        "scripts/data_modules/tests/test_memory_cli.py::test_export_asof_empty_project",
        "scripts/tests/test_backup_manager.py::test_backup_with_required_accepted_binding_succeeds",
        "scripts/tests/test_backup_manager.py::test_backup_with_required_binding_rejects_changed_manuscript",
        "scripts/tests/test_backup_manager.py::test_backup_never_moves_existing_chapter_tag",
        "scripts/tests/test_backup_manager.py::test_strict_git_backup_forces_recovery_files_ignored_by_project",
        "scripts/tests/test_backup_manager.py::test_strict_local_backup_contains_complete_consistency_state",
        "scripts/tests/test_backup_manager.py::test_local_rollback_restores_old_chapter_and_removes_later_facts",
        "scripts/tests/test_backup_manager.py::test_local_rollback_rejects_tampered_snapshot",
        "scripts/tests/test_backup_manager.py::test_local_rollback_rejects_external_receipt_and_empty_directory",
    }
)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    plugin_root = here.parent.parent
    if (plugin_root / "scripts" / "canon_ledger.py").is_file():
        return plugin_root
    return here.parents[2]


def _tmp_root() -> Path:
    root = _repo_root() / ".tmp" / "pytest"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_mkdtemp(suffix: str | None = None, prefix: str | None = None, dir: str | os.PathLike[str] | None = None) -> str:
    """Avoid WindowsApps Python creating inaccessible 0o700 temp dirs."""
    suffix = "" if suffix is None else suffix
    prefix = "tmp" if prefix is None else prefix
    root = Path(dir) if dir is not None else _tmp_root()
    root.mkdir(parents=True, exist_ok=True)

    for _ in range(100):
        path = root / f"{prefix}{uuid.uuid4().hex}{suffix}"
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return str(path.resolve())

    raise FileExistsError(f"Unable to create unique temporary directory under {root}")


def _install_safe_tempfile() -> None:
    root = _tmp_root()
    for name in ("TMP", "TEMP", "TMPDIR"):
        os.environ[name] = str(root)
    os.environ["CANON_LEDGER_TEST_RELAX_ATOMIC_REPLACE"] = "1"
    tempfile.tempdir = str(root)
    tempfile.mkdtemp = _safe_mkdtemp
    tempfile.TemporaryDirectory = _SafeTemporaryDirectory


class _SafeTemporaryDirectory(_ORIGINAL_TEMPORARY_DIRECTORY):
    def __init__(self, suffix=None, prefix=None, dir=None, ignore_cleanup_errors=True, *, delete=True):
        super().__init__(
            suffix=suffix,
            prefix=prefix,
            dir=dir,
            ignore_cleanup_errors=ignore_cleanup_errors,
            delete=delete,
        )


def _safe_sqlite_connect(*args, **kwargs):
    conn = _ORIGINAL_SQLITE_CONNECT(*args, **kwargs)
    try:
        conn.execute("PRAGMA journal_mode=MEMORY")
    except sqlite3.DatabaseError:
        pass
    return conn


def _install_safe_sqlite() -> None:
    sqlite3.connect = _safe_sqlite_connect


def pytest_configure(config: pytest.Config) -> None:
    _install_safe_tempfile()
    _install_safe_sqlite()
    config.addinivalue_line(
        "markers",
        "retired_v2: frozen specification for the removed v2 fact-writing workflow",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Keep removed v2 writer specs outside the current-product acceptance run."""

    include_retired = os.environ.get("CANON_LEDGER_INCLUDE_RETIRED_V2_TESTS") == "1"
    active: list[pytest.Item] = []
    retired: list[pytest.Item] = []
    root = _repo_root()
    for item in items:
        try:
            relative = Path(str(item.path)).resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            active.append(item)
            continue
        base_node = str(item.name).split("[", 1)[0]
        key = f"{relative}::{base_node}"
        is_retired = relative in _RETIRED_V2_MODULES or key in _RETIRED_V2_TESTS
        if not is_retired:
            active.append(item)
            continue
        item.add_marker(pytest.mark.retired_v2)
        if include_retired:
            active.append(item)
        else:
            retired.append(item)
    if retired:
        config.hook.pytest_deselected(items=retired)
        items[:] = active


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in request.node.name)
    path = _tmp_root() / f"{safe_name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        if os.environ.get("CANON_LEDGER_KEEP_TEST_TMP") != "1":
            shutil.rmtree(path, ignore_errors=True)


_install_safe_tempfile()
_install_safe_sqlite()
