from __future__ import annotations

from pathlib import Path

import scripts.conftest as selection


ROOT = Path(__file__).resolve().parents[2]


def test_current_authority_manifest_covers_every_canon_v3_test_module():
    discovered = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "scripts" / "data_modules" / "tests").glob(
            "test_canon_v3_*.py"
        )
    }

    assert discovered <= selection._REQUIRED_CURRENT_AUTHORITY_MODULES


def test_current_authority_modules_exist_and_are_not_wholly_retired():
    missing = {
        path
        for path in selection._REQUIRED_CURRENT_AUTHORITY_MODULES
        if not (ROOT / path).is_file()
    }

    assert not missing
    assert not (
        selection._REQUIRED_CURRENT_AUTHORITY_MODULES
        & selection._RETIRED_V2_MODULES
    )


def test_retired_manifest_is_explicit_and_cannot_match_canon_v3_modules():
    assert selection._RETIRED_V2_MODULES
    assert selection._RETIRED_V2_TESTS
    assert all("test_canon_v3_" not in path for path in selection._RETIRED_V2_MODULES)
    assert all("test_canon_v3_" not in node for node in selection._RETIRED_V2_TESTS)


def test_full_acceptance_enables_current_authority_collection_audit():
    runner = (ROOT / "scripts" / "run_acceptance.py").read_text(encoding="utf-8")

    assert 'environment["CANON_LEDGER_REQUIRE_CURRENT_AUTHORITY"] = "1"' in runner
