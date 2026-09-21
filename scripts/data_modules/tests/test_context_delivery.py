from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from data_modules.config import DataModulesConfig
from data_modules.context_delivery import ContextPageError, ContextPager, compact_context, paginate_context
from data_modules.memory_contract import ContextPack
from data_modules.memory_contract_adapter import MemoryContractAdapter, _estimate_tokens
from data_modules.tests.test_canon_v3_retrieval import _project, _publish_loop, _tree_bytes


@pytest.fixture
def book(tmp_path):
    root = _project(tmp_path / "book")
    for chapter in range(1, 7):
        _publish_loop(root, chapter, f"第{chapter}枚铜符为何刻着同一张地图")
    return root


def _leaves(value, path=""):
    if isinstance(value, (dict, list)) and value:
        items = value.items() if isinstance(value, dict) else enumerate(value)
        result = {}
        for key, child in items:
            token = str(key).replace("~", "~0").replace("/", "~1")
            result.update(_leaves(child, path + "/" + token))
        return result
    return {path: value}


def test_default_budget_does_not_block_seventh_chapter_or_drop_facts(book):
    adapter = MemoryContractAdapter(DataModulesConfig(project_root=book))
    before = _tree_bytes(book)
    default = adapter.load_context(7)
    tiny = adapter.load_context(7, budget_tokens=1)
    roomy = adapter.load_context(7, budget_tokens=20_000)
    assert default.budget["hard_over_budget"] is True
    assert default.budget["kind"] == "soft_target"
    assert default.budget["delivery_hint"] == "paginate"
    for pack in (default, tiny, roomy):
        assert pack.completeness["status"] == "complete"
        assert pack.completeness["missing_sources"] == []
        assert pack.sections["canonical_facts"] == roomy.sections["canonical_facts"]
        assert pack.sections["hard_constraints"] == roomy.sections["hard_constraints"]
    assert adapter.load_context(99, budget_tokens=1).completeness["status"] == "blocked"
    assert _tree_bytes(book) == before


def test_paging_consumes_every_value_exactly_once_and_is_read_only(book):
    adapter = MemoryContractAdapter(DataModulesConfig(project_root=book))
    before = _tree_bytes(book)
    cursor = None
    seen = {}
    digests = set()
    pages = 0
    while True:
        pack = adapter.load_context(7, include_retrieval=False)
        page = paginate_context(pack, cursor=cursor)
        assert page == paginate_context(pack, cursor=cursor)
        assert page["source_completeness"]["status"] == "complete"
        assert page["delivery"]["estimated_tokens"] == _estimate_tokens(page)
        assert page["delivery"]["estimated_tokens"] <= 4000
        digests.add(page["context_digest"])
        pages += 1
        for entry in page["entries"]:
            leaves = _leaves(entry["value"], entry["path"])
            assert not set(seen) & set(leaves)
            seen.update(leaves)
        cursor = page["delivery"]["next_cursor"]
        assert page["delivery"]["has_more"] == bool(cursor)
        if not cursor:
            break
        assert pages < 100
    assert pages > 1
    assert len(digests) == 1
    assert seen == _leaves(pack.sections, "/sections")
    assert _tree_bytes(book) == before


@pytest.mark.parametrize("changed", ["chapter", "head", "fact", "budget"])
def test_cursor_rejects_mixed_contexts(changed):
    pack = ContextPack(chapter=7, sections={
        "runtime_status": {"workflow_snapshot": {"head_hash": "a" * 64}},
        "canonical_facts": [{"value": "事实" * 200} for _ in range(6)],
    }, completeness={"status": "complete"})
    cursor = paginate_context(pack, budget_tokens=512)["delivery"]["next_cursor"]
    assert cursor
    changed_pack = copy.deepcopy(pack)
    budget = 512
    if changed == "chapter":
        changed_pack.chapter = 8
    elif changed == "head":
        changed_pack.sections["runtime_status"]["workflow_snapshot"]["head_hash"] = "b" * 64
    elif changed == "fact":
        changed_pack.sections["canonical_facts"][0]["value"] = "另一个事实"
    else:
        budget = 1024
    with pytest.raises(ContextPageError, match="cursor_stale"):
        paginate_context(changed_pack, budget_tokens=budget, cursor=cursor)


def test_large_atomic_value_is_reported_over_budget_and_never_truncated():
    value = "不能删掉的完整规则" * 1000
    pack = ContextPack(chapter=1, sections={"rules": [value]}, completeness={"status": "complete"})
    page = paginate_context(pack, budget_tokens=512)
    assert page["delivery"]["over_budget"] is True
    assert page["delivery"]["has_more"] is False
    assert page["entries"] == [{"path": "/sections/rules/0", "value": value}]
    assert page["source_completeness"]["status"] == "complete"


def test_page_estimates_remain_conservative_at_budget_boundaries():
    for length in range(1000, 1016):
        pack = ContextPack(chapter=1, sections={"value": "x" * length})
        estimate = paginate_context(pack, budget_tokens=10_000)["delivery"]["estimated_tokens"]
        for budget in range(estimate - 2, estimate + 3):
            page = paginate_context(pack, budget_tokens=budget)
            assert page["delivery"]["estimated_tokens"] >= _estimate_tokens(page)
            if not page["delivery"]["over_budget"]:
                assert _estimate_tokens(page) <= budget


def test_cli_paging_uses_public_command_and_stale_cursor_fails_closed(book):
    root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable, str(root / "canon_ledger.py"), "--project-root", str(book),
        "memory-contract", "load-context", "--chapter", "7", "--paged",
    ]
    first = subprocess.run(command, capture_output=True, text=True, check=True)
    page = json.loads(first.stdout)
    cursor = page["delivery"]["next_cursor"]
    assert cursor
    assert page["binding"]["head_hash"]
    assert page["source_completeness"]["source_status"]["rag"]["reason"] == "paged_context"
    # Style lives outside the fact context; it must not invalidate this cursor.
    style = book / "设定集" / "文风提示词.md"
    style.parent.mkdir(exist_ok=True)
    style.write_text("## 作者提示词\n写得简洁。", encoding="utf-8")
    second = subprocess.run([*command, "--cursor", cursor], capture_output=True, text=True, check=True)
    assert json.loads(second.stdout)["context_digest"] == page["context_digest"]
    _publish_loop(book, 7, "钟楼的第三扇门通向何方")
    stale = subprocess.run([*command, "--cursor", cursor], capture_output=True, text=True)
    assert stale.returncode != 0
    error = json.loads(stale.stderr)
    assert "cursor_stale" in error["error"]
    assert error["usable_for_writing"] is False


def test_compact_context_keeps_fact_semantics_and_resolves_all_references(book):
    adapter = MemoryContractAdapter(DataModulesConfig(project_root=book))
    pack = adapter.load_context(7, include_retrieval=False, all_sections=True)
    compact = compact_context(pack)
    catalog = compact.sections["fact_catalog"]
    for row in pack.sections["canonical_facts"]:
        saved = catalog[row["fact_digest"]]
        for field in ("subject", "field", "value", "payload", "status", "source_chapter"):
            assert saved.get(field) == row.get(field)
    refs = [value for path, value in _leaves(compact.sections).items() if path.endswith("/fact_ref")]
    assert refs and set(refs).issubset(catalog)
    assert _estimate_tokens(compact.to_dict()) < _estimate_tokens(pack.to_dict())
    bulk = ContextPager(compact).all_pages()
    assert len({page["context_digest"] for page in bulk["pages"]}) == 1
    assert bulk["pages"][-1]["delivery"]["has_more"] is False


def test_bulk_export_loads_runtime_once_and_preserves_outline(book, monkeypatch, capsys):
    import memory_cli

    outline = book / "大纲" / "第0007章.md"
    outline.parent.mkdir(exist_ok=True)
    outline.write_text("本章目标：去钟楼寻找铜符。", encoding="utf-8")
    adapter = MemoryContractAdapter(DataModulesConfig(project_root=book))
    original_load = adapter.load_context
    calls = []

    def counted_load(*args, **kwargs):
        calls.append((args, kwargs))
        return original_load(*args, **kwargs)

    monkeypatch.setattr(adapter, "load_context", counted_load)
    monkeypatch.setattr(memory_cli, "_adapter", lambda _root: adapter)
    monkeypatch.setattr(sys, "argv", [
        "memory_cli", "--project-root", str(book), "load-context", "--chapter", "7",
        "--all-pages", "--out", ".canon-ledger/tmp/context_pages.json",
    ])
    before = _tree_bytes(book)
    memory_cli.main()
    receipt = json.loads(capsys.readouterr().out)
    assert len(calls) == 1
    assert "pages" not in receipt
    bundle = json.loads((book / receipt["path"]).read_text(encoding="utf-8"))
    assert bundle["context_digest"] == receipt["context_digest"]
    assert "去钟楼寻找铜符" in json.dumps(bundle, ensure_ascii=False)
    after = _tree_bytes(book)
    after.pop(".canon-ledger/tmp/context_pages.json")
    assert after == before


def test_bulk_export_cannot_write_manuscript_or_canon(book, monkeypatch):
    import memory_cli

    called = []
    monkeypatch.setattr(memory_cli, "_adapter", lambda _root: called.append(True))
    for target in ("正文/第0001章.md", ".story-system/v3/CURRENT", "../outside.json"):
        monkeypatch.setattr(sys, "argv", [
            "memory_cli", "--project-root", str(book), "load-context", "--chapter", "7",
            "--all-pages", "--out", target,
        ])
        with pytest.raises(ValueError):
            memory_cli.main()
    assert called == []
