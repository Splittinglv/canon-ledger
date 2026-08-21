#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One project-wide authority for unpublished Canon v3 transactions."""

from __future__ import annotations

from pathlib import Path


CHAPTER_STAGING_RELATIVE_PATH = Path(".story-system/v3/STAGING.json")
AUTHOR_AXIOM_STAGING_RELATIVE_PATH = Path(
    ".story-system/v3/AUTHOR_AXIOM_STAGING.json"
)
# Keep the historic chapter lock name so existing installations coordinate
# with the new channel without a lock-file migration.
AUTHORITATIVE_STAGING_LOCK_RELATIVE_PATH = Path(
    ".story-system/v3/STAGING.json.lock"
)


class AuthoritativeStagingConflict(RuntimeError):
    pass


def authoritative_staging_kinds(project_root: str | Path) -> tuple[str, ...]:
    root = Path(project_root).expanduser().resolve()
    kinds: list[str] = []
    if (root / CHAPTER_STAGING_RELATIVE_PATH).is_file():
        kinds.append("chapter")
    if (root / AUTHOR_AXIOM_STAGING_RELATIVE_PATH).is_file():
        kinds.append("author_axiom")
    return tuple(kinds)


def assert_single_authoritative_staging(project_root: str | Path) -> None:
    kinds = authoritative_staging_kinds(project_root)
    if len(kinds) > 1:
        raise AuthoritativeStagingConflict(
            "canon_v3_multiple_authoritative_staging:" + ",".join(kinds)
        )


def assert_no_authoritative_staging(project_root: str | Path) -> None:
    kinds = authoritative_staging_kinds(project_root)
    if kinds:
        raise AuthoritativeStagingConflict(
            "canon_v3_authoritative_staging_active:" + ",".join(kinds)
        )


__all__ = [
    "AUTHORITATIVE_STAGING_LOCK_RELATIVE_PATH",
    "AUTHOR_AXIOM_STAGING_RELATIVE_PATH",
    "CHAPTER_STAGING_RELATIVE_PATH",
    "AuthoritativeStagingConflict",
    "assert_no_authoritative_staging",
    "assert_single_authoritative_staging",
    "authoritative_staging_kinds",
]
