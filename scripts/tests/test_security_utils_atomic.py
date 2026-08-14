from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

import security_utils
from security_utils import AtomicWriteError, atomic_write_json, read_json_safe, resolve_inside_project


def test_atomic_write_retries_transient_permission_error(tmp_path, monkeypatch):
    """瞬时占用（WinError 5）在退避重试窗口内自愈——issue #125 主场景。"""
    target = tmp_path / "memory_scratchpad.json"
    target.write_text('{"old": true}', encoding="utf-8")

    real_replace = security_utils.os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "拒绝访问。")
        return real_replace(src, dst)

    sleeps: list[float] = []
    monkeypatch.setattr(security_utils.os, "replace", flaky_replace)
    monkeypatch.setattr(security_utils.time, "sleep", sleeps.append)

    atomic_write_json(target, {"new": 1}, use_lock=False, backup=False)

    assert read_json_safe(target) == {"new": 1}
    assert calls["n"] == 3
    assert sleeps == [0.02, 0.04]  # 指数退避
    assert not list(tmp_path.glob("*.tmp"))  # 成功后无临时文件残留


def test_atomic_write_raises_when_target_stays_locked(tmp_path, monkeypatch):
    """持续占用：重试穷尽后如实抛 AtomicWriteError（生产模式），原文件不被破坏。"""
    monkeypatch.delenv("CANON_LEDGER_TEST_RELAX_ATOMIC_REPLACE", raising=False)
    target = tmp_path / "state.json"
    target.write_text("{}", encoding="utf-8")

    def always_denied(src, dst):
        raise PermissionError(5, "拒绝访问。")

    monkeypatch.setattr(security_utils.os, "replace", always_denied)
    monkeypatch.setattr(security_utils.time, "sleep", lambda _s: None)

    with pytest.raises(AtomicWriteError):
        atomic_write_json(target, {"x": 1}, use_lock=False, backup=False)

    assert read_json_safe(target) == {}
    assert not list(tmp_path.glob("*.tmp"))  # 失败路径清理了临时文件


def test_atomic_write_relaxed_fallback_still_writes(tmp_path, monkeypatch):
    """测试沙箱降级分支（CANON_LEDGER_TEST_RELAX_ATOMIC_REPLACE=1）行为保持：穷尽后覆写成功。"""
    monkeypatch.setenv("CANON_LEDGER_TEST_RELAX_ATOMIC_REPLACE", "1")
    target = tmp_path / "state.json"
    target.write_text("{}", encoding="utf-8")

    def always_denied(src, dst):
        raise PermissionError(5, "拒绝访问。")

    monkeypatch.setattr(security_utils.os, "replace", always_denied)
    monkeypatch.setattr(security_utils.time, "sleep", lambda _s: None)

    atomic_write_json(target, {"x": 1}, use_lock=False, backup=False)

    assert read_json_safe(target) == {"x": 1}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 独有的 replace 共享冲突")
def test_atomic_write_survives_real_windows_file_hold(tmp_path):
    """真实复现 issue #125：另一线程 open 持有目标文件（无 FILE_SHARE_DELETE），
    句柄释放前 os.replace 报 WinError 5，退避重试窗口内自愈。"""
    target = tmp_path / "memory_scratchpad.json"
    target.write_text('{"old": true}', encoding="utf-8")

    opened = threading.Event()

    def hold():
        with open(target, "r", encoding="utf-8"):
            opened.set()
            time.sleep(0.15)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert opened.wait(timeout=2)
        atomic_write_json(target, {"new": 1}, use_lock=False, backup=False)
    finally:
        t.join()

    assert read_json_safe(target) == {"new": 1}


def _symlink_or_skip(target: Path, link: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError:
        pytest.skip("无法创建符号链接")


def test_resolve_inside_project_rejects_leaf_symlink_outside(tmp_path):
    inside = tmp_path / "book"
    inside.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = inside / "alias.txt"
    _symlink_or_skip(outside, link)
    with pytest.raises(ValueError, match="符号链接"):
        resolve_inside_project(inside, link, reject_leaf_symlink=True)


def test_resolve_inside_project_rejects_parent_directory_symlink_outside(tmp_path):
    project = tmp_path / "book"
    project.mkdir()
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "file.txt").write_text("secret", encoding="utf-8")
    nested = project / "设定集"
    _symlink_or_skip(outside, nested, target_is_directory=True)
    with pytest.raises(ValueError, match="越出项目|必须位于项目内"):
        resolve_inside_project(project, nested / "file.txt")


def test_resolve_inside_project_rejects_leaf_symlink_target_outside_without_flag(tmp_path):
    inside = tmp_path / "book"
    inside.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = inside / "alias.txt"
    _symlink_or_skip(outside, link)
    with pytest.raises(ValueError, match="越出项目|必须位于项目内"):
        resolve_inside_project(inside, link, reject_leaf_symlink=False)


def test_resolve_inside_project_accepts_real_file_inside(tmp_path):
    project = tmp_path / "book"
    target = project / "设定集" / "文风提示词.md"
    target.parent.mkdir(parents=True)
    target.write_text("ok", encoding="utf-8")
    resolved = resolve_inside_project(project, target, reject_leaf_symlink=True)
    assert resolved == target.resolve()
    resolved.relative_to(project.resolve())
