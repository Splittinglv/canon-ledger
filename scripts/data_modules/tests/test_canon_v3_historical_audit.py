#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from data_modules.canon_v3.evidence import candidate_digest
from data_modules.canon_v3.historical_audit import (
    HISTORICAL_EXPORT_SCHEMA,
    HistoricalAuditExportError,
    archive_bound_manuscript,
    export_historical_revision,
    revision_archive_path,
)
from data_modules.canon_v3.repository import CanonV3Repository
from data_modules.canon_v3.schema import FactCandidate, WorldRuleRevealedClaim
from data_modules.canon_v3.service import (
    CanonV3Service,
    FinalizeBlockedError,
)
from data_modules.chapter_content_binding import build_chapter_binding
from data_modules.tests.canon_v3_protocol_helpers import (
    finalize,
    proposal_authority,
    record_decisions,
)


SCAN_DIMENSIONS = ["setting", "timeline", "continuity", "character", "logic"]


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _stage_chapter(
    root: Path,
    text: str,
) -> tuple[bytes, str, CanonV3Service, dict]:
    ledger = root / ".canon-ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / "state.json").write_text(
        json.dumps(
            {"project_info": {"title": "历史导出测试"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manuscript = root / "正文" / "第0001章.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text(text, encoding="utf-8")
    original = manuscript.read_bytes()
    binding = build_chapter_binding(root, 1)
    quote = "灵钥只能在月圆之夜开启。"
    quote_raw = quote.encode("utf-8")
    start = original.index(quote_raw)
    candidate = FactCandidate(
        candidate_id="rule-moon-key",
        claim=WorldRuleRevealedClaim(rule="灵钥只能在月圆之夜开启"),
        sources=(
            {
                "source_type": "manuscript_span",
                "source_id": "span-open-loop",
                "document_sha256": binding["sha256"],
                "chapter": 1,
                "start": start,
                "end": start + len(quote_raw),
                "quote": quote,
                "quote_sha256": hashlib.sha256(quote_raw).hexdigest(),
            },
        ),
        support_map={"rule": ("span-open-loop",)},
    )
    service = CanonV3Service(root)
    authority = proposal_authority(service, 1)
    digest = candidate_digest(candidate)
    prepared = service.prepare(
        {
            **authority,
            "chapter": 1,
            "chapter_binding": binding,
            "candidates": [candidate.model_dump(mode="json")],
            "observations": [],
            "scan_attestations": [
                {
                    "attestation_id": "complete-historical-export-test",
                    "scanner": "reviewer",
                    "scanner_version": "v3-test",
                    "chapter_sha256": binding["sha256"],
                    "parent_head": authority["parent_head"],
                    "author_axiom_digest": authority["author_axiom_digest"],
                    "entity_registry_digest": authority["entity_registry_digest"],
                    "dimensions": SCAN_DIMENSIONS,
                    "status": "complete",
                    "checked_candidate_digests": [digest],
                }
            ],
        }
    )
    assert prepared["cases"]
    ready = record_decisions(
        service,
        [
            {"case_key": case["case_key"], "action": "approve"}
            for case in prepared["cases"]
        ],
        snapshot=prepared,
    )
    return original, digest, service, ready


def _publish_chapter(root: Path, text: str) -> tuple[bytes, str]:
    original, digest, service, ready = _stage_chapter(root, text)
    finalize(service, snapshot=ready)
    return original, digest


def test_historical_export_is_head_bound_complete_and_read_only(
    tmp_path: Path,
) -> None:
    project = tmp_path / "book"
    original, candidate = _publish_chapter(
        project,
        "开场。\n灵钥只能在月圆之夜开启。\n",
    )
    before = _tree(project)

    result = export_historical_revision(project, 1, 1)

    assert result["schema_version"] == HISTORICAL_EXPORT_SCHEMA
    assert result["disposition"] == "read_only"
    assert result["authority"]["audited_head"]
    assert result["authority"]["parent_head"]
    assert result["authority"]["commit_hash"]
    assert result["commit"]["object_hash"] == result["authority"]["commit_hash"]
    assert result["transaction"]["object_hash"] == result["authority"][
        "transaction_hash"
    ]
    assert result["decisions"]
    assert result["decisions"][0]["object_hash"] in result["authority"][
        "decision_hashes"
    ]
    assert result["chapter_binding"]["sha256"] == hashlib.sha256(
        original
    ).hexdigest()
    assert result["source"]["status"] == "source_available"
    assert result["source"]["kind"] == "current_manuscript"
    assert result["source"]["content"].encode("utf-8") == original
    assert result["candidate_digests"] == [candidate]
    assert len(result["candidates"]) == 1
    assert len(result["effects"]) == 1
    assert result["author_axioms"]["author_axiom_digest"]
    assert result["entity_registry"]["registry_digest"] == result[
        "entity_registry"
    ]["transaction_registry_digest"]
    assert result["export_digest"]
    assert _tree(project) == before


def test_historical_export_reports_unavailable_instead_of_guessing_current_text(
    tmp_path: Path,
) -> None:
    project = tmp_path / "book"
    original, _candidate = _publish_chapter(
        project,
        "开场。\n灵钥只能在月圆之夜开启。\n",
    )
    manuscript = project / "正文" / "第0001章.md"
    manuscript.write_text("这是后来改写的正文。\n", encoding="utf-8")
    missing_archive = revision_archive_path(
        project, hashlib.sha256(original).hexdigest()
    )
    assert missing_archive.exists()
    missing_archive.unlink()
    before = _tree(project)

    result = export_historical_revision(project, 1, 1)

    assert result["source"]["status"] == "source_unavailable"
    assert result["source"]["content"] is None
    assert result["source"]["reason"] == "current_manuscript_sha_mismatch"
    assert "灵钥只能在月圆之夜开启" not in str(result["source"])
    assert not revision_archive_path(
        project, hashlib.sha256(original).hexdigest()
    ).exists()
    assert _tree(project) == before


def test_historical_export_reads_only_exact_content_addressed_revision_archive(
    tmp_path: Path,
) -> None:
    project = tmp_path / "book"
    original, _candidate = _publish_chapter(
        project,
        "开场。\n灵钥只能在月圆之夜开启。\n",
    )
    head_before = CanonV3Service(project).repository.current_head(validate=True)
    binding = build_chapter_binding(project, 1)
    archive = revision_archive_path(project, binding["sha256"])
    assert archive.is_file()
    archived = archive_bound_manuscript(project, binding)
    assert archived["created"] is False
    assert CanonV3Service(project).repository.current_head(validate=True) == head_before
    manuscript = project / "正文" / "第0001章.md"
    manuscript.write_text("这是后来改写的正文。\n", encoding="utf-8")
    before = _tree(project)

    result = export_historical_revision(project, 1, 1)

    assert result["source"]["status"] == "source_available"
    assert result["source"]["kind"] == "revision_archive"
    assert result["source"]["path"] == archive.relative_to(project).as_posix()
    assert result["source"]["content"].encode("utf-8") == original
    assert _tree(project) == before


def test_historical_export_refuses_ambiguous_republished_numeric_revision(
    tmp_path: Path,
) -> None:
    project = tmp_path / "book"
    repository = CanonV3Repository(project)
    head = repository._initialize_objects(  # noqa: SLF001 - storage fixture.
        genesis_metadata={
            "schema_version": "canon-v3/genesis-metadata/v1",
            "source": "new_project",
            "cutover_chapter": 0,
        }
    )
    first = repository._seal_objects(  # noqa: SLF001 - storage fixture.
        chapter=1,
        transaction={"chapter": 1, "marker": "first-chapter"},
        expected_head=head,
        canon_effects=[],
    )
    old_suffix = repository._seal_objects(  # noqa: SLF001 - storage fixture.
        chapter=2,
        transaction={"chapter": 2, "marker": "old-suffix"},
        expected_head=first.head_hash,
        canon_effects=[],
    )
    rewritten = repository._seal_objects(  # noqa: SLF001 - storage fixture.
        chapter=1,
        transaction={"chapter": 1, "marker": "rewritten-prefix"},
        expected_head=old_suffix.head_hash,
        canon_effects=[],
    )
    repository._seal_objects(  # noqa: SLF001 - storage fixture.
        chapter=2,
        transaction={"chapter": 2, "marker": "new-suffix"},
        expected_head=rewritten.head_hash,
        canon_effects=[],
    )

    with pytest.raises(
        HistoricalAuditExportError,
        match="historical_revision_ambiguous",
    ):
        export_historical_revision(project, 2, 1)


def test_historical_export_real_cli_import_path(tmp_path: Path) -> None:
    project = tmp_path / "book"
    _publish_chapter(
        project,
        "开场。\n灵钥只能在月圆之夜开启。\n",
    )
    repository_root = Path(__file__).resolve().parents[3]

    completed = subprocess.run(
        [
            sys.executable,
            str(repository_root / "scripts" / "canon_ledger.py"),
            "--project-root",
            str(project),
            "canon-v3",
            "historical-export",
            "--chapter",
            "1",
            "--revision",
            "1",
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == HISTORICAL_EXPORT_SCHEMA
    assert payload["source"]["status"] == "source_available"


def test_finalize_archive_failure_keeps_head_and_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import data_modules.canon_v3.historical_audit as historical

    project = tmp_path / "book"
    _original, _digest, service, ready = _stage_chapter(
        project,
        "开场。\n灵钥只能在月圆之夜开启。\n",
    )
    head_before = service.repository.current_head(validate=True)
    staging = project / ".story-system" / "v3" / "STAGING.json"
    assert staging.is_file()

    def fail_archive(*_args, **_kwargs):
        raise HistoricalAuditExportError("archive-fault-injected")

    monkeypatch.setattr(historical, "archive_bound_manuscript", fail_archive)
    with pytest.raises(
        FinalizeBlockedError,
        match="canon_v3_revision_archive_failed:archive-fault-injected",
    ):
        finalize(service, snapshot=ready)

    assert service.repository.current_head(validate=True) == head_before
    assert staging.is_file()


def test_finalize_returns_idempotent_revision_archive_result(tmp_path: Path) -> None:
    project = tmp_path / "book"
    _original, _digest, service, ready = _stage_chapter(
        project,
        "开场。\n灵钥只能在月圆之夜开启。\n",
    )

    first = finalize(service, snapshot=ready)
    second = finalize(service, snapshot=ready)

    assert first["revision_archive"]["created"] is True
    assert second["revision_archive"]["created"] is False
    assert first["revision_archive"]["archive_path"] == second[
        "revision_archive"
    ]["archive_path"]
    archive = project / first["revision_archive"]["archive_path"]
    assert archive.is_file()
