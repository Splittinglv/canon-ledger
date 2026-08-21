#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure history folding over the manifest reachable from Canon v3 CURRENT.

History is a read model.  It never scans object directories and therefore can
never accidentally promote an orphan transaction, commit, or old chapter
suffix into active canon.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Generic, Iterator, Protocol, TypeVar

from .repository import ProjectionBinding


StateT = TypeVar("StateT")


class CanonHistoryRepository(Protocol):
    """Small repository surface required by the generic history fold."""

    def current_head(self, *, validate: bool = True) -> str | None: ...

    def read_manifest(
        self,
        object_hash: str,
        *,
        validate_references: bool = True,
    ) -> dict[str, Any]: ...

    def read_commit(self, object_hash: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CanonEffectRecord:
    """One canonical effect plus immutable revision provenance."""

    chapter: int
    revision: int
    commit_hash: str
    effect_index: int
    effect: dict[str, Any]


@dataclass(frozen=True)
class CurrentHistorySnapshot(Generic[StateT]):
    """A history result bound to one exact manifest generation."""

    head_hash: str | None
    generation: int
    records: tuple[CanonEffectRecord, ...]
    state: StateT

    @property
    def projection_binding(self) -> ProjectionBinding:
        return ProjectionBinding(
            generation=self.generation,
            head_hash=self.head_hash,
        )

    @property
    def effects(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(record.effect) for record in self.records]


def iter_current_effect_records(
    repository: CanonHistoryRepository,
) -> Iterator[CanonEffectRecord]:
    """Yield effects reachable from the CURRENT manifest, in chapter order."""
    head = repository.current_head(validate=False)
    if head is None:
        return
    manifest = repository.read_manifest(head, validate_references=True)
    entries = manifest.get("chapters")
    if not isinstance(entries, list):  # Defensive; repository validation rejects it.
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        commit_hash = str(entry.get("commit_hash") or "")
        commit = repository.read_commit(commit_hash)
        chapter = int(commit.get("chapter") or 0)
        revision = int(commit.get("revision") or 0)
        effects = commit.get("canon_effects")
        if not isinstance(effects, list):
            continue
        for effect_index, effect in enumerate(effects):
            if not isinstance(effect, dict):
                continue
            yield CanonEffectRecord(
                chapter=chapter,
                revision=revision,
                commit_hash=commit_hash,
                effect_index=effect_index,
                effect=copy.deepcopy(effect),
            )


def current_canon_effects(
    repository: CanonHistoryRepository,
) -> list[dict[str, Any]]:
    """Return only active canon effects; object-directory orphans are ignored."""
    return [copy.deepcopy(record.effect) for record in iter_current_effect_records(repository)]


def fold_current_history(
    repository: CanonHistoryRepository,
    *,
    initial: StateT,
    reducer: Callable[[StateT, dict[str, Any]], StateT],
) -> CurrentHistorySnapshot[StateT]:
    """Fold current effects with a caller-owned pure reducer.

    The HEAD is captured once and every object is immutable, so a concurrent
    publication yields either a complete old snapshot or a complete new one.
    Reducers receive deep copies and cannot mutate stored commit payloads.
    """
    head = repository.current_head(validate=False)
    if head is None:
        return CurrentHistorySnapshot(
            head_hash=None,
            generation=0,
            records=(),
            state=copy.deepcopy(initial),
        )
    manifest = repository.read_manifest(head, validate_references=True)
    entries = manifest.get("chapters")
    records: list[CanonEffectRecord] = []
    state = copy.deepcopy(initial)
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            commit_hash = str(entry.get("commit_hash") or "")
            commit = repository.read_commit(commit_hash)
            chapter = int(commit.get("chapter") or 0)
            revision = int(commit.get("revision") or 0)
            effects = commit.get("canon_effects")
            if not isinstance(effects, list):
                continue
            for effect_index, effect in enumerate(effects):
                if not isinstance(effect, dict):
                    continue
                safe_effect = copy.deepcopy(effect)
                record = CanonEffectRecord(
                    chapter=chapter,
                    revision=revision,
                    commit_hash=commit_hash,
                    effect_index=effect_index,
                    effect=safe_effect,
                )
                records.append(record)
                state = reducer(state, copy.deepcopy(safe_effect))
    return CurrentHistorySnapshot(
        head_hash=head,
        generation=int(manifest.get("generation") or 0),
        records=tuple(records),
        state=state,
    )


__all__ = [
    "CanonEffectRecord",
    "CanonHistoryRepository",
    "CurrentHistorySnapshot",
    "current_canon_effects",
    "fold_current_history",
    "iter_current_effect_records",
]
