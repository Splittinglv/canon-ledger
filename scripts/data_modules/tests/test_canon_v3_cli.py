from __future__ import annotations

import json
import sys

import pytest

from scripts.data_modules import canon_ledger
from scripts.data_modules.canon_v3 import migration
from scripts.data_modules.canon_v3.service import CanonV3Service


def _project(tmp_path):
    root = tmp_path / "book"
    state = root / ".canon-ledger" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")
    return root


def test_canon_v3_initialize_and_status_share_workflow_snapshot(
    tmp_path, monkeypatch, capsys
) -> None:
    root = _project(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(root),
            "canon-v3",
            "initialize",
        ],
    )
    with pytest.raises(SystemExit) as initialized:
        canon_ledger.main()
    assert initialized.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"]["state"] == "ready"
    assert payload["workflow"]["can_write_next"] is True

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "canon-v3",
            "status",
            "--project-root",
            str(root),
        ],
    )
    with pytest.raises(SystemExit) as status:
        canon_ledger.main()
    assert status.value.code == 0
    workflow = json.loads(capsys.readouterr().out)
    assert workflow == payload["workflow"]


def test_canon_v3_prepare_rejects_input_outside_project(
    tmp_path, monkeypatch, capsys
) -> None:
    root = _project(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(root),
            "canon-v3",
            "prepare",
            "--input-file",
            str(outside),
        ],
    )
    with pytest.raises(SystemExit) as failed:
        canon_ledger.main()
    assert failed.value.code == 1
    error = json.loads(capsys.readouterr().err)
    assert error["ok"] is False
    assert "项目内" in error["message"]


@pytest.mark.parametrize(
    ("action", "method", "needs_input"),
    [
        ("author-axiom-prepare", "prepare_author_axioms", True),
        ("author-axiom-decide", "record_author_axiom_decisions", True),
        ("author-axiom-finalize", "finalize_author_axioms", True),
        ("author-axiom-status", "author_axiom_status", False),
        ("author-axioms", "active_author_axioms", False),
    ],
)
def test_author_axiom_service_actions_are_exposed_by_cli(
    tmp_path, monkeypatch, capsys, action, method, needs_input
) -> None:
    root = _project(tmp_path)
    input_path = root / ".canon-ledger/tmp/request.json"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text('{"probe":"exact"}', encoding="utf-8")
    called = {}

    def fake(self, payload=None):
        called["root"] = self.project_root
        called["payload"] = payload
        return {"schema_version": f"test/{action}", "ok": True}

    monkeypatch.setattr(CanonV3Service, method, fake)
    argv = [
        "canon-ledger",
        "--project-root",
        str(root),
        "canon-v3",
        action,
    ]
    if needs_input:
        argv.extend(["--input-file", ".canon-ledger/tmp/request.json"])
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as completed:
        canon_ledger.main()
    assert completed.value.code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert called["root"] == root
    assert called["payload"] == ({"probe": "exact"} if needs_input else None)


@pytest.mark.parametrize(
    ("action", "extra", "target"),
    [
        ("audit-cutover", [], "audit_cutover"),
        ("repair-cutover", ["--dry-run"], "repair_cutover_dry_run"),
    ],
)
def test_canon_v3_cutover_diagnostics_are_exposed_as_read_only_cli_actions(
    tmp_path, monkeypatch, capsys, action, extra, target
) -> None:
    root = _project(tmp_path)
    called = {}

    def fake(project_root, cutover_chapter=None):
        called["root"] = project_root
        called["chapter"] = cutover_chapter
        return {
            "schema_version": f"test/{target}",
            "read_only": True,
            "writes_performed": False,
        }

    monkeypatch.setattr(migration, target, fake)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(root),
            "canon-v3",
            action,
            "--cutover-chapter",
            "3",
            *extra,
        ],
    )
    with pytest.raises(SystemExit) as completed:
        canon_ledger.main()
    assert completed.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["read_only"] is True
    assert payload["writes_performed"] is False
    assert called == {"root": root, "chapter": 3}


def test_canon_v3_repair_cutover_apply_consumes_project_bound_request(
    tmp_path, monkeypatch, capsys
) -> None:
    root = _project(tmp_path)
    request_path = root / ".canon-ledger" / "tmp" / "recertify.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request = {
        "schema_version": "canon-v3/legacy-recertification-publish-request/v1",
        "expected_current_head": "a" * 64,
        "detached_plan_digest": "b" * 64,
        "publish_token": "c" * 64,
        "decisions": [],
    }
    request_path.write_text(json.dumps(request), encoding="utf-8")
    called = {}

    def fake(project_root, payload):
        called["root"] = project_root
        called["payload"] = payload
        return {"published": True, "head_hash": "d" * 64}

    monkeypatch.setattr(migration, "repair_cutover_apply", fake)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(root),
            "canon-v3",
            "repair-cutover",
            "--apply",
            "--input-file",
            str(request_path.relative_to(root)),
        ],
    )
    with pytest.raises(SystemExit) as completed:
        canon_ledger.main()
    assert completed.value.code == 0
    assert json.loads(capsys.readouterr().out)["published"] is True
    assert called == {"root": root, "payload": request}
