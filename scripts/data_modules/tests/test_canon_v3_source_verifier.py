from __future__ import annotations

import hashlib
import json

import pytest

from scripts.data_modules.canon_v3.schema import (
    AuthorAxiomSource,
    FactCandidate,
    PresenceObservedClaim,
    canonical_digest,
)
from scripts.data_modules.canon_v3.source_verifier import (
    SourceVerificationError,
    resolve_json_pointer,
    verify_candidate_sources,
)
from scripts.data_modules.chapter_content_binding import build_chapter_binding


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _project(tmp_path):
    root = tmp_path / "book"
    chapter = root / "正文" / "第0003章.md"
    chapter.parent.mkdir(parents=True)
    chapter.write_text("前文。\n苏月仍在青云殿内。\n后文。\n", encoding="utf-8")
    return root, chapter, build_chapter_binding(root, 3)


def _span_candidate(chapter, binding):
    raw = chapter.read_bytes()
    quote = "苏月仍在青云殿内。"
    quote_raw = quote.encode("utf-8")
    start = raw.index(quote_raw)
    source = {
        "source_type": "manuscript_span",
        "source_id": "span-presence",
        "document_sha256": binding["sha256"],
        "chapter": 3,
        "start": start,
        "end": start + len(quote_raw),
        "quote": quote,
        "quote_sha256": _digest(quote_raw),
    }
    return FactCandidate(
        candidate_id="presence-suyue",
        claim=PresenceObservedClaim(
            subject="苏月",
            location="青云殿",
            presence="在",
        ),
        sources=(source,),
        support_map={
            "subject": ("span-presence",),
            "location": ("span-presence",),
            "presence": ("span-presence",),
        },
    )


def test_exact_nonzero_utf8_byte_span_is_verified(tmp_path) -> None:
    root, chapter, binding = _project(tmp_path)
    candidate = _span_candidate(chapter, binding)

    verify_candidate_sources(root, binding, candidate)

    source = candidate.sources[0]
    assert source.start > len("前文。\n")
    assert source.end - source.start == len(source.quote.encode("utf-8"))


def test_model_hash_cannot_hide_changed_manuscript(tmp_path) -> None:
    root, chapter, binding = _project(tmp_path)
    candidate = _span_candidate(chapter, binding)
    chapter.write_text("前文。\n苏月已经离开青云殿。\n后文。\n", encoding="utf-8")

    with pytest.raises(SourceVerificationError, match="document_hash_mismatch"):
        verify_candidate_sources(root, binding, candidate)


def test_author_axiom_must_resolve_exact_leaf_value(tmp_path) -> None:
    root = tmp_path / "book"
    master = root / ".story-system" / "MASTER_SETTING.json"
    master.parent.mkdir(parents=True)
    document = {"setting_canon": {"rules": {"moon": "月门只能在夜间开启"}}}
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    master.write_bytes(raw)
    source = AuthorAxiomSource(
        source_id="axiom-moon",
        document_path=".story-system/MASTER_SETTING.json",
        document_sha256=_digest(raw),
        json_pointer="/setting_canon/rules/moon",
        value="月门只能在夜间开启",
        value_sha256=canonical_digest("月门只能在夜间开启"),
    )

    assert resolve_json_pointer(document, source.json_pointer) == source.value

    tampered = source.model_copy(update={"json_pointer": "/setting_canon/rules"})
    with pytest.raises(SourceVerificationError, match="must_be_leaf"):
        from scripts.data_modules.canon_v3.source_verifier import (
            _verify_author_axiom_source,
        )

        _verify_author_axiom_source(root, tampered)


def test_arbitrary_project_json_is_not_an_author_axiom(tmp_path) -> None:
    root = tmp_path / "book"
    payload = root / ".canon-ledger" / "tmp" / "model.json"
    payload.parent.mkdir(parents=True)
    raw = b'{"fact":"forged"}'
    payload.write_bytes(raw)
    source = AuthorAxiomSource(
        source_id="forged-axiom",
        document_path=".canon-ledger/tmp/model.json",
        document_sha256=_digest(raw),
        json_pointer="/fact",
        value="forged",
        value_sha256=canonical_digest("forged"),
    )
    root.joinpath("正文").mkdir()
    root.joinpath("正文", "第0003章.md").write_text("正文", encoding="utf-8")
    binding = build_chapter_binding(root, 3)
    candidate = FactCandidate(
        candidate_id="forged-presence",
        claim=PresenceObservedClaim(
            subject="forged", location="forged", presence="forged"
        ),
        sources=(source,),
        support_map={
            "subject": ("forged-axiom",),
            "location": ("forged-axiom",),
            "presence": ("forged-axiom",),
        },
    )

    with pytest.raises(SourceVerificationError, match="not_author_owned"):
        verify_candidate_sources(root, binding, candidate)
