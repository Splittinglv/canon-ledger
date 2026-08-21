from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_document_links import validate_document_links  # noqa: E402


def test_current_repository_document_links_and_eval_files_resolve() -> None:
    assert validate_document_links(ROOT) == []


def test_validator_reports_missing_markdown_and_eval_targets(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text(
        "[missing](other.md) [external](https://example.com/x) [section](#local)\n",
        encoding="utf-8",
    )
    eval_dir = tmp_path / "skills" / "sample" / "evals"
    eval_dir.mkdir(parents=True)
    (eval_dir / "evals.json").write_text(
        json.dumps({"evals": [{"files": ["fixtures/missing.md"]}]}),
        encoding="utf-8",
    )

    issues = validate_document_links(tmp_path)
    assert {item["code"] for item in issues} == {
        "markdown.target.missing",
        "eval_file.missing",
    }


def test_validator_accepts_root_relative_and_angle_bracket_paths(tmp_path: Path) -> None:
    (tmp_path / "docs" / "with space").mkdir(parents=True)
    (tmp_path / "docs" / "with space" / "target.md").write_text("# Target\n")
    (tmp_path / "README.md").write_text(
        "[root](/docs/with%20space/target.md)\n"
        "[angle](<docs/with space/target.md>)\n",
        encoding="utf-8",
    )

    assert validate_document_links(tmp_path) == []


def test_validator_ignores_generated_and_test_scratch_directories(tmp_path: Path) -> None:
    scratch = tmp_path / ".tmp" / "pytest" / "book" / "evals"
    scratch.mkdir(parents=True)
    (scratch / "broken.md").write_text("[missing](not-created.md)\n", encoding="utf-8")
    (scratch / "evals.json").write_text(
        json.dumps({"evals": [{"files": ["also/missing.md"]}]}),
        encoding="utf-8",
    )

    assert validate_document_links(tmp_path) == []
