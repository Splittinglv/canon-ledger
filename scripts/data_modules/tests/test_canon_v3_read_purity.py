#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from data_modules.canon_v3.service import CanonV3Service
from data_modules.canon_v3.author_axiom import AuthorAxiomChannel
from data_modules.canon_v3.query import CanonQueryFacade
from data_modules.canon_v3.projection import read_projection
from data_modules.doctor import build_doctor_report
from data_modules.workflow_authority import WorkflowAuthority


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _recognized(root: Path) -> None:
    ledger = root / ".canon-ledger"
    ledger.mkdir(parents=True)
    (ledger / "state.json").write_text(
        json.dumps(
            {"project_info": {"title": "只读测试"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_workflow_snapshot_does_not_create_v3_layout_or_locks(tmp_path: Path) -> None:
    project = tmp_path / "book"
    _recognized(project)
    before = _tree(project)

    snapshot = WorkflowAuthority(project).snapshot()

    assert snapshot["state"] == "migration_required"
    assert _tree(project) == before
    assert not (project / ".story-system" / "v3").exists()


def test_uninitialized_doctor_and_axiom_reads_do_not_create_v3_layout(
    tmp_path: Path,
) -> None:
    project = tmp_path / "book"
    _recognized(project)
    before = _tree(project)
    service = CanonV3Service(project)

    service.author_axiom_status()
    service.active_author_axioms()
    build_doctor_report(project, deep=True)

    assert _tree(project) == before
    assert not (project / ".story-system" / "v3").exists()


def test_constructing_author_axiom_reader_has_no_filesystem_side_effect(
    tmp_path: Path,
) -> None:
    project = tmp_path / "book"
    _recognized(project)
    before = _tree(project)

    channel = AuthorAxiomChannel(project)
    assert channel.status()["state"] == "ready"

    assert _tree(project) == before
    assert not (project / ".story-system" / "v3").exists()


def test_healthy_workflow_read_does_not_recreate_staging_lock(tmp_path: Path) -> None:
    project = tmp_path / "book"
    _recognized(project)
    CanonV3Service(project).initialize_new_project()
    stage_lock = project / ".story-system" / "v3" / "STAGING.json.lock"
    stage_lock.unlink(missing_ok=True)
    before = _tree(project)

    snapshot = WorkflowAuthority(project).snapshot()

    assert snapshot["state"] == "ready"
    assert _tree(project) == before
    assert not stage_lock.exists()


def test_all_public_read_surfaces_do_not_create_authority_locks(
    tmp_path: Path,
) -> None:
    project = tmp_path / "book"
    _recognized(project)
    CanonV3Service(project).initialize_new_project()
    v3_root = project / ".story-system" / "v3"
    stage_lock = v3_root / "STAGING.json.lock"
    publish_lock = v3_root / ".publish.lock"
    stage_lock.unlink(missing_ok=True)
    publish_lock.unlink(missing_ok=True)
    before = _tree(project)

    WorkflowAuthority(project).snapshot()
    read_projection(project, require_fresh=True)
    CanonQueryFacade(project).snapshot()
    build_doctor_report(project, deep=True)

    assert _tree(project) == before
    assert not stage_lock.exists()
    assert not publish_lock.exists()
