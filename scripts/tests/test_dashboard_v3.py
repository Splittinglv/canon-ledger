from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from scripts.data_modules.chapter_content_binding import build_chapter_binding
from scripts.data_modules.canon_v3.evidence import candidate_digest
from scripts.data_modules.canon_v3.projection import projection_path
from scripts.data_modules.canon_v3.schema import FactCandidate, OpenLoopCreatedClaim
from scripts.data_modules.canon_v3.service import CanonV3Service
from scripts.data_modules.tests.canon_v3_protocol_helpers import (
    finalize,
    proposal_authority,
)


def _dashboard_module(monkeypatch):
    plugin_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(plugin_root))
    sys.modules.pop("dashboard.app", None)
    module = importlib.import_module("dashboard.app")
    # macOS watchdog's native FSEvents teardown is unstable on Python 3.14.
    # API tests do not need SSE delivery, so keep lifespan deterministic while
    # still exercising every Dashboard GET handler.
    monkeypatch.setattr(module._watcher, "start", lambda **_kwargs: None)
    monkeypatch.setattr(module._watcher, "stop", lambda: None)
    return module


def _initialized_project(tmp_path: Path) -> Path:
    root = tmp_path / "book"
    CanonV3Service(root).initialize_new_project()
    return root


def _file_fingerprint(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_dashboard_fact_surfaces_share_exact_head_binding_and_are_read_only(
    monkeypatch,
    tmp_path,
):
    root = _initialized_project(tmp_path)
    module = _dashboard_module(monkeypatch)
    for lock_path in root.rglob("*.lock"):
        lock_path.unlink()
    before = _file_fingerprint(root)

    with TestClient(module.create_app(root)) as client:
        workflow_response = client.get("/api/canon-v3/workflow")
        history_response = client.get("/api/canon-v3/history")
        character_response = client.get("/api/canon-v3/characters")
        obligation_response = client.get("/api/canon-v3/obligations")
        commit_response = client.get("/api/commits")

    assert workflow_response.status_code == 200
    workflow = workflow_response.json()
    assert workflow["state"] == "ready"

    for response in (
        history_response,
        character_response,
        obligation_response,
        commit_response,
    ):
        assert response.status_code == 200
        payload = response.json()
        assert payload["source"] == "canon_v3_head"
        assert payload["binding"]["authority"] == "canon_v3"
        assert payload["binding"]["head_hash"] == workflow["head_hash"]
        assert payload["binding"]["generation"] == workflow["generation"]
        assert payload["binding"]["workflow_digest"] == workflow["workflow_digest"]
        assert len(payload["binding"]["projection_digest"]) == 64

    assert _file_fingerprint(root) == before


def test_dashboard_facts_fail_closed_when_projection_is_stale(
    monkeypatch,
    tmp_path,
):
    root = _initialized_project(tmp_path)
    projection_path(root).unlink()
    legacy_dir = root / ".canon-ledger"
    legacy_dir.mkdir(exist_ok=True)
    (legacy_dir / "index.db").write_bytes(b"not authoritative")
    module = _dashboard_module(monkeypatch)

    with TestClient(module.create_app(root)) as client:
        response = client.get("/api/canon-v3/obligations")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "projection_rebuild_required"
    assert detail["authority"] == "canon_v3"
    assert detail["usable_for_writing"] is False
    assert detail["workflow"]["projection_fresh"] is False
    assert detail["primary_action"] == detail["workflow"]["primary_action"]
    assert "items" not in detail


def test_dashboard_obligations_are_derived_from_a_real_canon_commit(
    monkeypatch,
    tmp_path,
):
    root = tmp_path / "book"
    manuscript = root / "正文" / "第0001章.md"
    manuscript.parent.mkdir(parents=True)
    quote = "谁偷走了灵钥，仍是谜。"
    manuscript.write_text(f"开场。\n{quote}\n", encoding="utf-8")
    binding = build_chapter_binding(root, 1)
    raw = manuscript.read_bytes()
    quote_raw = quote.encode("utf-8")
    start = raw.index(quote_raw)
    candidate = FactCandidate(
        candidate_id="loop-stolen-key",
        claim=OpenLoopCreatedClaim(loop="谁偷走了灵钥"),
        sources=(
            {
                "source_type": "manuscript_span",
                "source_id": "span-loop",
                "document_sha256": binding["sha256"],
                "chapter": 1,
                "start": start,
                "end": start + len(quote_raw),
                "quote": quote,
                "quote_sha256": hashlib.sha256(quote_raw).hexdigest(),
            },
        ),
        support_map={"loop": ("span-loop",)},
    )
    service = CanonV3Service(root)
    authority = proposal_authority(service, 1)
    digest = candidate_digest(candidate)
    service.prepare(
        {
            **authority,
            "chapter": 1,
            "chapter_binding": binding,
            "candidates": [candidate.model_dump(mode="json")],
            "observations": [],
            "scan_attestations": [
                {
                    "attestation_id": "dashboard-complete-fact-scan",
                    "scanner": "reviewer",
                    "scanner_version": "dashboard-test",
                    "chapter_sha256": binding["sha256"],
                    "parent_head": authority["parent_head"],
                    "author_axiom_digest": authority["author_axiom_digest"],
                    "entity_registry_digest": authority["entity_registry_digest"],
                    "dimensions": [
                        "setting",
                        "timeline",
                        "continuity",
                        "character",
                        "logic",
                    ],
                    "status": "complete",
                    "checked_candidate_digests": [digest],
                },
            ],
        }
    )
    finalize(service)
    module = _dashboard_module(monkeypatch)

    with TestClient(module.create_app(root)) as client:
        response = client.get("/api/canon-v3/obligations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "canon_v3_head"
    assert payload["items"][0]["category"] == "open_loop_created"
    assert payload["items"][0]["payload"]["loop"] == "谁偷走了灵钥"
    assert payload["binding"]["as_of_chapter"] == 1


def test_dashboard_legacy_index_analytics_are_explicitly_retired(
    monkeypatch,
    tmp_path,
):
    root = _initialized_project(tmp_path)
    module = _dashboard_module(monkeypatch)

    with TestClient(module.create_app(root)) as client:
        response = client.get("/api/stats/chapter-trend")

    assert response.status_code == 410
    assert response.json()["detail"] == {
        "schema_version": "canon-v3/dashboard-error/v1",
        "code": "legacy_dashboard_endpoint_retired",
        "endpoint": "/api/stats/chapter-trend",
        "authority": "legacy_read_only",
        "usable_for_writing": False,
        "replacement": "/api/canon-v3/history",
    }


def test_dashboard_exposes_no_human_decision_write_route(monkeypatch, tmp_path):
    root = _initialized_project(tmp_path)
    module = _dashboard_module(monkeypatch)
    app = module.create_app(root)

    assert all(
        not (
            {"POST", "PUT", "PATCH", "DELETE"}
            & set(getattr(route, "methods", None) or set())
        )
        for route in app.routes
    )

    with TestClient(app) as client:
        response = client.post("/api/canon-v3/decide", json={})
    assert response.status_code in {404, 405}


def test_dashboard_primary_frontend_does_not_call_legacy_fact_surfaces():
    root = Path(__file__).resolve().parents[2]
    frontend = root / "dashboard" / "frontend" / "src"
    primary_sources = "\n".join(
        (frontend / relative).read_text(encoding="utf-8")
        for relative in (
            "App.jsx",
            "api.js",
            "pages/OverviewPage.jsx",
            "pages/SystemPage.jsx",
            "pages/ForeshadowingPage.jsx",
        )
    )

    assert "/api/stats/chapter-trend" not in primary_sources
    assert "/api/story-runtime/health" not in primary_sources
    assert "/api/contracts/summary" not in primary_sources
    assert "Mainline" not in primary_sources
    assert "Fallback" not in primary_sources
    assert "projectInfo?.plot_threads" not in primary_sources
    assert "/api/canon-v3/obligations" in primary_sources
    assert "allowed_actions" in primary_sources
    assert "primary_action" in primary_sources
