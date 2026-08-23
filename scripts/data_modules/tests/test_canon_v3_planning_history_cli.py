from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def _recognized_project(root: Path) -> Path:
    ledger = root / ".canon-ledger"
    ledger.mkdir(parents=True)
    (ledger / "state.json").write_text(
        json.dumps(
            {
                "project_info": {"title": "CLI facade test"},
                "progress": {"current_chapter": 0},
            }
        ),
        encoding="utf-8",
    )
    return root


def test_planning_refresh_cli_dispatches_closed_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import data_modules.canon_ledger as cli
    import data_modules.planning_facade as planning

    project = _recognized_project(tmp_path / "book")
    observed: dict[str, object] = {}

    def _refresh(root, chapter, *, dry_run):
        observed.update(root=Path(root), chapter=chapter, dry_run=dry_run)
        return {
            "schema_version": "canon-v3/planning-contract-refresh/v1",
            "authority": "planning_only",
            "applied": False,
        }

    monkeypatch.setattr(planning, "refresh_contracts", _refresh)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(project),
            "canon-v3",
            "planning",
            "refresh-contracts",
            "--chapter",
            "7",
            "--dry-run",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert int(exc.value.code or 0) == 0
    assert payload["authority"] == "planning_only"
    assert observed == {"root": project, "chapter": 7, "dry_run": True}


def test_historical_export_cli_dispatches_exact_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import data_modules.canon_ledger as cli
    import data_modules.canon_v3.historical_audit as historical

    project = _recognized_project(tmp_path / "book")
    commit_hash = "a" * 64

    monkeypatch.setattr(
        historical,
        "export_historical_revision",
        lambda root, chapter, revision, *, commit_hash: {
            "schema_version": "canon-v3/historical-revision-export/v1",
            "disposition": "read_only",
            "chapter": chapter,
            "revision": revision,
            "commit_hash": commit_hash,
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(project),
            "canon-v3",
            "historical-export",
            "--chapter",
            "3",
            "--revision",
            "2",
            "--commit-hash",
            commit_hash,
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert int(exc.value.code or 0) == 0
    assert payload["disposition"] == "read_only"
    assert payload["chapter"] == 3
    assert payload["revision"] == 2
    assert payload["commit_hash"] == commit_hash


@pytest.mark.parametrize(
    ("action", "error_code"),
    [
        ("planning", "planning_chapter_outline_missing"),
        ("historical-export", "historical_revision_not_reachable"),
    ],
)
def test_facade_cli_errors_are_machine_readable_and_exit_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    action: str,
    error_code: str,
) -> None:
    import data_modules.canon_ledger as cli
    import data_modules.planning_facade as planning
    import data_modules.canon_v3.historical_audit as historical

    project = _recognized_project(tmp_path / "book")
    if action == "planning":
        monkeypatch.setattr(
            planning,
            "refresh_contracts",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                planning.PlanningContractRefreshError(
                    error_code, chapter=1
                )
            ),
        )
        tail = ["planning", "refresh-contracts", "--chapter", "1"]
    else:
        monkeypatch.setattr(
            historical,
            "export_historical_revision",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                historical.HistoricalAuditExportError(
                    error_code, chapter=1, revision=1
                )
            ),
        )
        tail = ["historical-export", "--chapter", "1", "--revision", "1"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(project),
            "canon-v3",
            *tail,
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()
    payload = json.loads(capsys.readouterr().err)
    assert int(exc.value.code or 0) == 2
    assert payload == {
        "schema_version": "canon-v3/cli-error/v1",
        "ok": False,
        "error": error_code,
        "details": {"chapter": 1, **({"revision": 1} if action != "planning" else {})},
    }
