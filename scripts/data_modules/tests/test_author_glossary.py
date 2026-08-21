#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from data_modules.author_glossary import (
    author_label,
    default_glossary_path,
    explain,
    load_terms,
    lookup,
)


def test_author_glossary_loads_single_source():
    terms = load_terms()

    assert default_glossary_path().is_file()
    assert terms["HEAD"].author == "当前正史版本"
    assert terms["STAGING"].author == "待确认事实事务"
    assert terms["workflow_snapshot"].author == "权威流程快照"
    assert terms["projection"].author == "当前正史事实视图"
    assert "兼容" in terms["mainline_ready"].author
    assert terms["write-gate"].explanation


def test_author_glossary_covers_v3_workflow_and_recovery_states():
    terms = load_terms()

    required = {
        "migration_required",
        "legacy_repair",
        "awaiting_human",
        "rewrite_required",
        "recompile_required",
        "ready_to_finalize",
        "ready",
        "projection_rebuild_required",
        "projection_fresh",
        "exact-version-conflict",
        "primary_action",
        "author-axiom",
    }
    assert required <= terms.keys()
    assert "唯一" in terms["primary_action"].author
    assert "刷新" in terms["exact-version-conflict"].explanation
    assert "HEAD" in terms["projection_fresh"].explanation


def test_author_glossary_lookup_is_case_insensitive():
    terms = load_terms()

    found = lookup("chapter_commit", terms=terms)
    assert found is not None
    assert found.author == "旧版本章事实存档"
    assert author_label("CHAPTER_COMMIT", terms=terms) == "旧版本章事实存档"
    assert "退役" in terms["chapter-commit"].explanation


def test_author_glossary_unknown_term_falls_back_to_original():
    terms = load_terms()

    assert author_label("unknown_runtime_word", terms=terms) == "unknown_runtime_word"
    assert "unknown_runtime_word" in explain("unknown_runtime_word", terms=terms)
