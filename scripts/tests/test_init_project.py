from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import init_project as init_module


def _tree_bytes(path: Path) -> tuple[tuple[str, str, bytes], ...]:
    """Capture names, entry kinds and bytes without following symlinks."""
    if path.is_symlink():
        return ((".", "symlink", os.readlink(path).encode("utf-8")),)
    if not path.exists():
        return ()
    if path.is_file():
        return ((".", "file", path.read_bytes()),)

    captured: list[tuple[str, str, bytes]] = []
    for entry in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        relative = entry.relative_to(path).as_posix()
        if entry.is_symlink():
            captured.append((relative, "symlink", os.readlink(entry).encode("utf-8")))
        elif entry.is_dir():
            captured.append((relative, "dir", b""))
        else:
            captured.append((relative, "file", entry.read_bytes()))
    return tuple(captured)


def _disable_external_init_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(init_module, "is_git_available", lambda: False)
    monkeypatch.setattr(init_module, "write_current_project_pointer", lambda *_args, **_kwargs: None)


@pytest.mark.parametrize("target_exists", [False, True])
def test_clean_init_supports_missing_or_strictly_empty_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_exists: bool,
) -> None:
    _disable_external_init_side_effects(monkeypatch)
    target = tmp_path / ("existing-empty" if target_exists else "missing")
    if target_exists:
        target.mkdir()

    init_module.init_project(
        str(target),
        "雾城旧约",
        "悬疑",
        protagonist_name="林舟",
        target_chapters=12,
    )

    state = json.loads((target / ".canon-ledger" / "state.json").read_text(encoding="utf-8"))
    assert state["project_info"]["title"] == "雾城旧约"
    assert state["protagonist_state"]["name"] == "林舟"
    assert (target / ".story-system" / "v3" / "CURRENT").is_file()
    assert not list((target / ".canon-ledger").rglob("*.lock"))
    assert not list((target / ".story-system").rglob("*.lock"))


def test_init_rejects_all_nonclean_target_classes_without_byte_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_external_init_side_effects(monkeypatch)

    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    (ordinary / "作者笔记.bin").write_bytes(b"\x00keep-me\xff")

    existing_v3 = tmp_path / "v3"
    (existing_v3 / ".story-system" / "v3").mkdir(parents=True)
    (existing_v3 / ".story-system" / "v3" / "CURRENT").write_text("head\n", encoding="utf-8")

    legacy = tmp_path / "legacy"
    (legacy / ".canon-ledger").mkdir(parents=True)
    (legacy / ".canon-ledger" / "state.json").write_text("{}\n", encoding="utf-8")

    malformed = tmp_path / "malformed"
    (malformed / ".story-system").mkdir(parents=True)
    (malformed / ".story-system" / "unexpected.dat").write_bytes(b"runtime-fragment")

    malformed_v3 = tmp_path / "malformed-v3"
    (malformed_v3 / ".story-system" / "v3").mkdir(parents=True)

    target_file = tmp_path / "not-a-directory"
    target_file.write_bytes(b"plain-file")

    cases = {
        ordinary: "init_target_not_empty",
        existing_v3: "init_target_already_initialized",
        legacy: "init_target_legacy",
        malformed: "init_target_malformed",
        malformed_v3: "init_target_malformed",
        target_file: "init_target_malformed",
    }
    for target, expected_code in cases.items():
        before = _tree_bytes(target)
        with pytest.raises(SystemExit, match=expected_code):
            init_module.init_project(str(target), "不得覆盖", "悬疑")
        assert _tree_bytes(target) == before


def test_repeat_init_preserves_existing_state_and_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_external_init_side_effects(monkeypatch)
    target = tmp_path / "book"
    init_module.init_project(
        str(target),
        "原书名",
        "悬疑",
        protagonist_name="林舟",
    )
    before = _tree_bytes(target)
    state_before = (target / ".canon-ledger" / "state.json").read_bytes()
    head_before = (target / ".story-system" / "v3" / "CURRENT").read_bytes()

    with pytest.raises(SystemExit, match="Canon v3"):
        init_module.init_project(
            str(target),
            "覆盖后的书名",
            "玄幻",
            protagonist_name="另一个主角",
        )

    assert _tree_bytes(target) == before
    assert (target / ".canon-ledger" / "state.json").read_bytes() == state_before
    assert (target / ".story-system" / "v3" / "CURRENT").read_bytes() == head_before


def test_detached_build_failure_removes_partial_tree_and_created_parents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_external_init_side_effects(monkeypatch)
    target = tmp_path / "new-parent" / "nested" / "book"

    def fail_after_partial_write(staging: str, *_args: object, **_kwargs: object) -> None:
        staging_path = Path(staging)
        (staging_path / "partial").mkdir()
        (staging_path / "partial" / "state.bin").write_bytes(b"partial")
        raise RuntimeError("injected detached build failure")

    monkeypatch.setattr(init_module, "_build_project_tree", fail_after_partial_write)

    with pytest.raises(RuntimeError, match="injected detached build failure"):
        init_module.init_project(str(target), "失败项目", "悬疑")

    assert not target.exists()
    assert not (tmp_path / "new-parent").exists()
    assert not list(tmp_path.glob(".canon-ledger-init-*"))

    existing_empty = tmp_path / "existing-empty"
    existing_empty.mkdir()
    inode_before = existing_empty.stat().st_ino
    with pytest.raises(RuntimeError, match="injected detached build failure"):
        init_module.init_project(str(existing_empty), "失败项目", "悬疑")
    assert existing_empty.is_dir()
    assert existing_empty.stat().st_ino == inode_before
    assert _tree_bytes(existing_empty) == ()


def test_init_never_merges_when_clean_target_changes_during_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_external_init_side_effects(monkeypatch)
    target = tmp_path / "raced-target"
    target.mkdir()

    def race_with_external_file(staging: str, *_args: object, **_kwargs: object) -> None:
        staging_path = Path(staging)
        (staging_path / "generated.txt").write_text("generated\n", encoding="utf-8")
        (target / "author-created.txt").write_text("keep\n", encoding="utf-8")

    monkeypatch.setattr(init_module, "_build_project_tree", race_with_external_file)

    with pytest.raises(SystemExit, match="发生变化"):
        init_module.init_project(str(target), "并发项目", "悬疑")

    assert (target / "author-created.txt").read_text(encoding="utf-8") == "keep\n"
    assert not (target / "generated.txt").exists()
    assert not list(tmp_path.glob(".canon-ledger-init-*"))


def test_detached_init_rejects_temporary_absolute_path_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_external_init_side_effects(monkeypatch)
    target = tmp_path / "portable-target"

    def write_nonportable_artifact(staging: str, *_args: object, **_kwargs: object) -> None:
        staging_path = Path(staging)
        (staging_path / "state.json").write_text(
            json.dumps({"project_root": str(staging_path.resolve())}),
            encoding="utf-8",
        )

    monkeypatch.setattr(init_module, "_build_project_tree", write_nonportable_artifact)

    with pytest.raises(SystemExit, match="init_staging_path_leak"):
        init_module.init_project(str(target), "不可移植项目", "悬疑")
    assert not target.exists()
    assert not list(tmp_path.glob(".canon-ledger-init-*"))


def test_init_rejects_target_symlink_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_external_init_side_effects(monkeypatch)
    real_target = tmp_path / "real-target"
    real_target.mkdir()
    target_link = tmp_path / "target-link"
    try:
        target_link.symlink_to(real_target, target_is_directory=True)
    except OSError:
        pytest.skip("当前平台无法创建目录符号链接")

    before = _tree_bytes(real_target)
    with pytest.raises(SystemExit, match="符号链接"):
        init_module.init_project(str(target_link), "符号链接项目", "悬疑")
    assert _tree_bytes(real_target) == before


def test_init_rejects_nested_project_and_reserved_directory_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_external_init_side_effects(monkeypatch)
    existing = tmp_path / "existing-book"
    (existing / ".canon-ledger").mkdir(parents=True)
    (existing / ".canon-ledger" / "state.json").write_text("{}\n", encoding="utf-8")
    before = _tree_bytes(existing)

    with pytest.raises(SystemExit, match="init_target_inside_project"):
        init_module.init_project(
            str(existing / "nested-book"),
            "嵌套项目",
            "悬疑",
        )
    assert _tree_bytes(existing) == before

    for reserved in (".story-system", ".canon-ledger", ".cursor", ".git"):
        target = tmp_path / reserved / "nested-book"
        with pytest.raises(SystemExit, match="init_target_forbidden"):
            init_module.init_project(str(target), "保留目录项目", "悬疑")
        assert not target.exists()


def test_init_rejects_target_inside_plugin_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_external_init_side_effects(monkeypatch)
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    monkeypatch.setattr(init_module, "_PLUGIN_ROOT", plugin_root.resolve())
    target = plugin_root / "nested-book"

    with pytest.raises(SystemExit, match="init_target_forbidden"):
        init_module.init_project(str(target), "插件内项目", "悬疑")

    assert not target.exists()


def test_init_pins_real_parent_behind_system_style_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_external_init_side_effects(monkeypatch)
    parent_link = tmp_path / "parent-link"
    try:
        parent_link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("当前平台无法创建目录符号链接")

    target = parent_link / "new-book"
    init_module.init_project(str(target), "固定真实父路径", "悬疑")

    assert (tmp_path / "new-book" / ".story-system" / "v3" / "CURRENT").is_file()
    assert not target.is_symlink()


def test_init_git_commit_excludes_recursive_runtime_locks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not init_module.is_git_available():
        pytest.skip("Git 不可用")
    monkeypatch.setattr(init_module, "write_current_project_pointer", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Init Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "init@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Init Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "init@example.com")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    target = tmp_path / "git-book"

    init_module.init_project(str(target), "Git 项目", "悬疑")

    gitignore = (target / ".gitignore").read_text(encoding="utf-8")
    assert ".canon-ledger/**/*.lock" in gitignore
    assert ".story-system/**/*.lock" in gitignore
    assert not list((target / ".canon-ledger").rglob("*.lock"))
    assert not list((target / ".story-system").rglob("*.lock"))
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked
    assert not any(path.endswith(".lock") for path in tracked)
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", ".story-system/v3/nested/write.lock"],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
    )
    assert ignored.returncode == 0
