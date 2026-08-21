"""Test-client helpers for the strict Canon v3 transaction protocol."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from scripts.data_modules.canon_v3.service import CanonV3Service
from scripts.data_modules.canon_v3.entity_registry import (
    build_approved_entity_registry,
)
from scripts.data_modules.workflow_authority import WorkflowAuthority


def proposal_authority(
    service: CanonV3Service,
    chapter: int,
) -> dict[str, Any]:
    if service.repository.current_head(validate=True) is None:
        service.initialize_new_project()
    snapshot = WorkflowAuthority(service.project_root).snapshot()
    head = str(snapshot["head_hash"])
    registry_digest = build_approved_entity_registry(
        service.repository,
        head,
        target_chapter=int(chapter),
    ).registry_digest
    return {
        "schema_version": "canon-v3/proposal-batch/v2",
        "parent_head": head,
        "workflow_digest": snapshot["workflow_digest"],
        "author_axiom_digest": snapshot["author_axiom_digest"],
        "entity_registry_digest": registry_digest,
        "expected_stage_digest": snapshot.get("stage_digest"),
    }


def decision_request(
    snapshot: Mapping[str, Any],
    decisions: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    cases = {
        str(case["case_key"]): case
        for case in snapshot.get("cases") or ()
    }
    bound: list[dict[str, Any]] = []
    for raw in decisions:
        item = dict(raw)
        case = cases[str(item["case_key"])]
        bound.append(
            {
                **item,
                "target_digest": case["target_digest"],
                "material_digest": case["review_material"][
                    "material_digest"
                ],
                "expected_decision_head_hash": case.get(
                    "decision_head_hash"
                ),
            }
        )
    return {
        "schema_version": "canon-v3/decision-request/v2",
        "expected_stage_digest": snapshot["stage_digest"],
        "transaction_hash": snapshot["transaction_hash"],
        "decisions": bound,
    }


def record_decisions(
    service: CanonV3Service,
    decisions: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current = service.workflow_snapshot() if snapshot is None else snapshot
    raw = (
        decisions.get("decisions") or ()
        if isinstance(decisions, Mapping)
        else decisions
    )
    return service.record_decisions(decision_request(current, raw))


def finalize(
    service: CanonV3Service,
    *,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current = service.workflow_snapshot() if snapshot is None else snapshot
    return service.finalize(
        {
            "schema_version": "canon-v3/finalize-request/v2",
            "expected_stage_digest": current["stage_digest"],
            "transaction_hash": current["transaction_hash"],
            "finalize_token": current.get("finalize_token") or "0" * 64,
        }
    )


def authorize_reprepare(
    proposal: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        **dict(proposal),
        "expected_stage_digest": snapshot.get("stage_digest"),
    }
