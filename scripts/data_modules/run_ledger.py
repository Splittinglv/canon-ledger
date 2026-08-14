#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - direct script entry
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

try:
    from chapter_paths import find_chapter_file
except ImportError:  # pragma: no cover
    from scripts.chapter_paths import find_chapter_file

if __package__ in {None, ""}:  # pragma: no cover - direct script entry
    from data_modules.artifact_validator import OK_PROJECTION_STATUSES, REQUIRED_PROJECTION_WRITERS
    from data_modules.chapter_content_binding import verify_chapter_binding, verify_commit_content_binding
    from data_modules.project_phase import COMMIT_ARTIFACT_FILES, contract_files_for_chapter
    from data_modules.projection_log import commit_hash, latest_projection_run, projection_status_from_run
else:
    from .artifact_validator import OK_PROJECTION_STATUSES, REQUIRED_PROJECTION_WRITERS
    from .chapter_content_binding import verify_chapter_binding, verify_commit_content_binding
    from .project_phase import COMMIT_ARTIFACT_FILES, contract_files_for_chapter
    from .projection_log import commit_hash, latest_projection_run, projection_status_from_run


SCHEMA_VERSION = "webnovel-run-ledger/v1"
LEDGER_REL = Path(".webnovel") / "run_ledger.json"
WRITE_STEPS = ("draft", "review", "data", "commit", "projection", "backup")

LOCAL_BACKUP_RECEIPT_SCHEMA = "webnovel-backup-receipt/v2"
LOCAL_SNAPSHOT_MANIFEST_SCHEMA = "webnovel-local-snapshot/v1"
LOCAL_SNAPSHOT_MANIFEST = "snapshot.manifest.json"
LOCAL_BACKUP_KEY_REL = Path(".webnovel") / "backups" / ".integrity-key"
LOCAL_SNAPSHOT_ROOTS = ("正文", "大纲", "设定集", ".story-system", ".webnovel")
_LOCAL_SNAPSHOT_RE = re.compile(r"^snapshot_ch(?P<chapter>\d{4})_[A-Za-z0-9._-]+$")


def ledger_path(project_root: str | Path) -> Path:
    return Path(project_root) / LEDGER_REL


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def backup_integrity_key_path(project_root: str | Path) -> Path:
    return Path(project_root) / LOCAL_BACKUP_KEY_REL


def ensure_backup_integrity_key(project_root: str | Path) -> bytes:
    """返回项目本地备份签名密钥；仅首次备份时创建。"""
    path = backup_integrity_key_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        descriptor = -1
    if descriptor >= 0:
        try:
            os.write(descriptor, secrets.token_bytes(32))
        finally:
            os.close(descriptor)
    try:
        if path.is_symlink() or not path.is_file():
            return b""
        key = path.read_bytes()
        if len(key) < 32:
            return b""
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return key
    except OSError:
        return b""


def sign_backup_payload(project_root: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    key = ensure_backup_integrity_key(project_root)
    if not key:
        raise OSError("无法创建或读取本地备份完整性密钥")
    signed = dict(payload)
    signed["signature_algorithm"] = "hmac-sha256"
    signed.pop("signature", None)
    signed["signature"] = hmac.new(
        key,
        _canonical_json_bytes(signed),
        hashlib.sha256,
    ).hexdigest()
    return signed


def _backup_signature_trusted(project_root: Path, payload: dict[str, Any]) -> bool:
    if payload.get("signature_algorithm") != "hmac-sha256":
        return False
    signature = str(payload.get("signature") or "")
    if len(signature) != 64:
        return False
    key_path = backup_integrity_key_path(project_root)
    try:
        if key_path.is_symlink() or not key_path.is_file():
            return False
        key = key_path.read_bytes()
    except OSError:
        return False
    if len(key) < 32:
        return False
    expected = hmac.new(key, _canonical_json_bytes(payload), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def snapshot_file_entries(snapshot_root: str | Path) -> list[dict[str, Any]] | None:
    """计算快照的完整文件清单；符号链接或特殊文件一律拒绝。"""
    root = Path(snapshot_root)
    entries: list[dict[str, Any]] = []
    try:
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(root).as_posix()
            if relative == LOCAL_SNAPSHOT_MANIFEST:
                continue
            if path.is_symlink():
                return None
            if path.is_dir():
                continue
            if not path.is_file():
                return None
            raw = path.read_bytes()
            entries.append(
                {
                    "path": relative,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
    except OSError:
        return None
    return entries


def snapshot_directory_entries(snapshot_root: str | Path) -> list[str] | None:
    root = Path(snapshot_root)
    directories: list[str] = []
    try:
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_symlink():
                return None
            if path.is_dir():
                directories.append(path.relative_to(root).as_posix())
            elif not path.is_file():
                return None
    except OSError:
        return None
    return directories


def build_local_snapshot_manifest(
    project_root: str | Path,
    snapshot_root: str | Path,
    *,
    chapter: int,
    snapshot_name: str,
    created_at: str,
) -> dict[str, Any]:
    snapshot = Path(snapshot_root)
    entries = snapshot_file_entries(snapshot)
    directories = snapshot_directory_entries(snapshot)
    if entries is None or directories is None:
        raise OSError("本地快照含有不安全的符号链接或特殊文件")
    root_presence = {
        name: bool((snapshot / name).is_dir() and not (snapshot / name).is_symlink())
        for name in LOCAL_SNAPSHOT_ROOTS
    }
    return sign_backup_payload(
        project_root,
        {
            "schema_version": LOCAL_SNAPSHOT_MANIFEST_SCHEMA,
            "snapshot_kind": "complete-project-consistency-state",
            "chapter": int(chapter),
            "snapshot": snapshot_name,
            "created_at": created_at,
            "root_presence": root_presence,
            "excluded_paths": [".webnovel/backups"],
            "directories": directories,
            "files": entries,
        },
    )


def _local_snapshot_manifest_trusted(
    project_root: Path,
    snapshot_root: Path,
    manifest: dict[str, Any],
    *,
    chapter: int,
    snapshot_name: str,
) -> bool:
    if manifest.get("schema_version") != LOCAL_SNAPSHOT_MANIFEST_SCHEMA:
        return False
    if manifest.get("snapshot_kind") != "complete-project-consistency-state":
        return False
    try:
        if int(manifest.get("chapter") or 0) != int(chapter):
            return False
    except (TypeError, ValueError):
        return False
    if manifest.get("snapshot") != snapshot_name:
        return False
    if manifest.get("excluded_paths") != [".webnovel/backups"]:
        return False
    if not _backup_signature_trusted(project_root, manifest):
        return False
    root_presence = manifest.get("root_presence")
    if not isinstance(root_presence, dict) or set(root_presence) != set(LOCAL_SNAPSHOT_ROOTS):
        return False
    for name in LOCAL_SNAPSHOT_ROOTS:
        path = snapshot_root / name
        present = bool(root_presence.get(name))
        if present != bool(path.is_dir() and not path.is_symlink()):
            return False
    allowed_top_level = set(LOCAL_SNAPSHOT_ROOTS) | {LOCAL_SNAPSHOT_MANIFEST}
    try:
        if any(path.name not in allowed_top_level for path in snapshot_root.iterdir()):
            return False
    except OSError:
        return False
    current_entries = snapshot_file_entries(snapshot_root)
    current_directories = snapshot_directory_entries(snapshot_root)
    expected_entries = manifest.get("files")
    expected_directories = manifest.get("directories")
    return bool(
        current_entries is not None
        and current_directories is not None
        and isinstance(expected_entries, list)
        and isinstance(expected_directories, list)
        and current_entries == expected_entries
        and current_directories == expected_directories
    )


def local_snapshot_manifest_trusted(
    project_root: str | Path,
    snapshot_root: str | Path,
) -> bool:
    root = Path(project_root)
    snapshot = Path(snapshot_root)
    backups_root = root / ".webnovel" / "backups"
    try:
        if snapshot.is_symlink() or not snapshot.is_dir():
            return False
        if snapshot.resolve().parent != backups_root.resolve():
            return False
        manifest = json.loads(
            (snapshot / LOCAL_SNAPSHOT_MANIFEST).read_text(encoding="utf-8")
        )
        chapter = int(manifest.get("chapter") or 0)
    except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return False
    return bool(
        chapter > 0
        and isinstance(manifest, dict)
        and _local_snapshot_manifest_trusted(
            root,
            snapshot,
            manifest,
            chapter=chapter,
            snapshot_name=snapshot.name,
        )
    )


def local_snapshot_receipt_trusted(
    project_root: str | Path,
    receipt: dict[str, Any],
    *,
    chapter: int,
    require_current_binding: bool,
) -> bool:
    """校验本地 receipt、内部 manifest、完整清单及可选正文绑定。"""
    root = Path(project_root)
    if receipt.get("schema_version") != LOCAL_BACKUP_RECEIPT_SCHEMA:
        return False
    if receipt.get("mode") != "local" or not _backup_signature_trusted(root, receipt):
        return False
    try:
        if int(receipt.get("chapter") or 0) != int(chapter) or int(chapter) <= 0:
            return False
    except (TypeError, ValueError):
        return False

    snapshot_name = str(receipt.get("snapshot") or "")
    matched = _LOCAL_SNAPSHOT_RE.fullmatch(snapshot_name)
    if not matched or int(matched.group("chapter")) != int(chapter):
        return False
    if receipt.get("manifest_path") != LOCAL_SNAPSHOT_MANIFEST:
        return False
    snapshot_root = root / ".webnovel" / "backups" / snapshot_name
    backups_root = root / ".webnovel" / "backups"
    try:
        if snapshot_root.is_symlink() or not snapshot_root.is_dir():
            return False
        if snapshot_root.resolve().parent != backups_root.resolve():
            return False
        manifest_raw = (snapshot_root / LOCAL_SNAPSHOT_MANIFEST).read_bytes()
        if hashlib.sha256(manifest_raw).hexdigest() != str(receipt.get("manifest_sha256") or ""):
            return False
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict) or not _local_snapshot_manifest_trusted(
        root,
        snapshot_root,
        manifest,
        chapter=chapter,
        snapshot_name=snapshot_name,
    ):
        return False

    binding = receipt.get("chapter_binding")
    commit_rel = str(receipt.get("chapter_commit_path") or "")
    expected_commit_hash = str(receipt.get("chapter_commit_hash") or "")
    has_binding_proof = bool(isinstance(binding, dict) and commit_rel and expected_commit_hash)
    if require_current_binding and not has_binding_proof:
        return False
    if has_binding_proof:
        binding_path = str(binding.get("path") or "")
        if (
            not binding_path
            or Path(binding_path).is_absolute()
            or ".." in Path(binding_path).parts
            or Path(commit_rel).is_absolute()
            or ".." in Path(commit_rel).parts
        ):
            return False
        snapshot_commit = _read_json(snapshot_root / commit_rel)
        snapshot_ok, _snapshot_code = verify_commit_content_binding(
            snapshot_root,
            chapter,
            snapshot_commit,
        )
        if (
            not snapshot_ok
            or str(((snapshot_commit.get("meta") or {}).get("status") or "")) != "accepted"
            or commit_hash(snapshot_commit) != expected_commit_hash
            or snapshot_commit.get("chapter_binding") != binding
        ):
            return False
        if not (snapshot_root / ".webnovel" / "state.json").is_file():
            return False
        if require_current_binding:
            current_commit = _read_json(_commit_path(root, chapter))
            current_ok, _current_code = verify_commit_content_binding(root, chapter, current_commit)
            if (
                not current_ok
                or commit_hash(current_commit) != expected_commit_hash
                or current_commit.get("chapter_binding") != binding
            ):
                return False
    return True


def load_ledger(project_root: str | Path) -> dict[str, Any]:
    payload = _read_json(ledger_path(project_root))
    if payload.get("schema_version") != SCHEMA_VERSION:
        return {"schema_version": SCHEMA_VERSION, "write": {}}
    payload.setdefault("write", {})
    if not isinstance(payload["write"], dict):
        payload["write"] = {}
    return payload


def save_ledger(project_root: str | Path, ledger: dict[str, Any]) -> Path:
    path = ledger_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def file_signature(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        return {"path": str(target), "exists": False}
    stat = target.stat()
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "path": str(target),
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest,
    }


def _chapter_key(chapter: int) -> str:
    return f"chapter_{int(chapter):03d}"


def _write_run(ledger: dict[str, Any], chapter: int, mode: str) -> dict[str, Any]:
    write = ledger.setdefault("write", {})
    key = _chapter_key(chapter)
    run = write.setdefault(key, {})
    run.setdefault("chapter", int(chapter))
    run.setdefault("mode", mode or "default")
    run.setdefault("steps", {})
    run["updated_at"] = _now_iso()
    return run


def record_write_step(
    project_root: str | Path,
    *,
    chapter: int,
    step: str,
    status: str,
    mode: str = "default",
    inputs: dict[str, str | Path] | None = None,
    outputs: dict[str, str | Path] | None = None,
    problems: list[str] | None = None,
    auto_handled: list[str] | None = None,
    duration_ms: int = 0,
) -> dict[str, Any]:
    if step not in WRITE_STEPS:
        raise ValueError(f"unknown write step: {step}")
    root = Path(project_root)
    ledger = load_ledger(root)
    run = _write_run(ledger, chapter, mode)
    input_signatures = {
        str(name): file_signature(path)
        for name, path in (inputs or {}).items()
    }
    output_signatures = {
        str(name): file_signature(path)
        for name, path in (outputs or {}).items()
    }
    entry = {
        "step": step,
        "status": status,
        "recorded_at": _now_iso(),
        "duration_ms": int(duration_ms or 0),
        "inputs": input_signatures,
        "outputs": output_signatures,
        "problems": list(problems or []),
        "auto_handled": list(auto_handled or []),
    }
    run["steps"][step] = entry
    save_ledger(root, ledger)
    return entry


def _same_signature(expected: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if not isinstance(expected, dict):
        return False
    return bool(expected.get("exists")) and expected.get("sha256") == current.get("sha256")


def _step_completed(run: dict[str, Any], step: str) -> dict[str, Any] | None:
    steps = run.get("steps") if isinstance(run.get("steps"), dict) else {}
    entry = steps.get(step)
    if not isinstance(entry, dict):
        return None
    return entry if entry.get("status") == "completed" else None


def _trusted_output(entry: dict[str, Any] | None, name: str) -> bool:
    if not entry:
        return False
    outputs = entry.get("outputs") if isinstance(entry.get("outputs"), dict) else {}
    expected = outputs.get(name)
    if not isinstance(expected, dict):
        return False
    return _same_signature(expected, file_signature(expected.get("path") or ""))


def _trusted_input(entry: dict[str, Any] | None, name: str, path: Path | None) -> bool:
    if not entry or path is None:
        return False
    inputs = entry.get("inputs") if isinstance(entry.get("inputs"), dict) else {}
    expected = inputs.get(name)
    if not isinstance(expected, dict):
        return False
    return _same_signature(expected, file_signature(path))


def _commit_path(project_root: Path, chapter: int) -> Path:
    return project_root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"


def _payload_binding_trusted(project_root: Path, chapter: int, payload: dict[str, Any]) -> bool:
    binding = payload.get("chapter_binding") if isinstance(payload, dict) else None
    if not isinstance(binding, dict):
        return False
    ok, _code = verify_chapter_binding(project_root, chapter, binding)
    return ok


def _artifact_binding_trusted(project_root: Path, chapter: int, path: Path) -> bool:
    return _payload_binding_trusted(project_root, chapter, _read_json(path))


def _commit_state(project_root: Path, chapter: int) -> tuple[str, bool]:
    payload = _read_json(_commit_path(project_root, chapter))
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    binding_ok, _binding_code = verify_commit_content_binding(
        project_root,
        chapter,
        payload,
    )
    return str(meta.get("status") or ""), binding_ok


def _projection_done(project_root: Path, chapter: int) -> bool:
    payload = _read_json(_commit_path(project_root, chapter))
    run = latest_projection_run(project_root, chapter=chapter)
    run_matches_commit = bool(
        run
        and str(run.get("commit_hash") or "")
        and str(run.get("commit_hash") or "") == commit_hash(payload)
    )
    statuses = projection_status_from_run(run) if run_matches_commit else {}
    if not statuses:
        raw = payload.get("projection_status") if isinstance(payload.get("projection_status"), dict) else {}
        statuses = {str(key): str(value) for key, value in raw.items()}
    if not statuses:
        return False
    return all(str(statuses.get(writer) or "") in OK_PROJECTION_STATUSES for writer in REQUIRED_PROJECTION_WRITERS)


def _binding_bytes_match(binding: dict[str, Any], raw: bytes) -> bool:
    try:
        expected_size = int(binding.get("bytes") or 0)
        expected_hash = str(binding.get("sha256") or "")
    except (AttributeError, TypeError, ValueError):
        return False
    return bool(
        expected_size > 0
        and len(raw) == expected_size
        and hashlib.sha256(raw).hexdigest() == expected_hash
    )


def _commit_bytes_match(expected_hash: str, raw: bytes) -> bool:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and commit_hash(payload) == expected_hash


def backup_receipt_trusted(project_root: Path, chapter: int) -> bool:
    backup_dir = project_root / ".webnovel" / "backups"
    receipt = _read_json(backup_dir / f"ch{chapter:04d}.receipt.json")
    if receipt.get("schema_version") == LOCAL_BACKUP_RECEIPT_SCHEMA:
        return local_snapshot_receipt_trusted(
            project_root,
            receipt,
            chapter=chapter,
            require_current_binding=True,
        )
    if receipt.get("schema_version") != "webnovel-backup-receipt/v1":
        return False
    # v1 的本地 receipt 没有完整目录清单和内部签名，任意外部 JSON 加两个
    # 文件就能伪造“已备份”。旧格式只继续支持由 Git tag 固化的备份点。
    if str(receipt.get("mode") or "") == "local":
        return False
    try:
        if int(receipt.get("chapter") or 0) != int(chapter):
            return False
    except (TypeError, ValueError):
        return False

    commit_payload = _read_json(_commit_path(project_root, chapter))
    commit_ok, _commit_code = verify_commit_content_binding(
        project_root,
        chapter,
        commit_payload,
    )
    if not commit_ok:
        return False
    if str(receipt.get("chapter_commit_hash") or "") != commit_hash(commit_payload):
        return False
    if receipt.get("chapter_binding") != commit_payload.get("chapter_binding"):
        return False

    binding = receipt.get("chapter_binding")
    if not isinstance(binding, dict):
        return False
    binding_path = str(binding.get("path") or "")
    commit_rel = str(receipt.get("chapter_commit_path") or "")
    expected_commit_hash = str(receipt.get("chapter_commit_hash") or "")
    if (
        not binding_path
        or Path(binding_path).is_absolute()
        or ".." in Path(binding_path).parts
        or not commit_rel
        or Path(commit_rel).is_absolute()
        or ".." in Path(commit_rel).parts
    ):
        return False

    mode = str(receipt.get("mode") or "")
    if mode == "local":
        snapshot = str(receipt.get("snapshot") or "")
        if not snapshot or Path(snapshot).name != snapshot:
            return False
        snapshot_root = backup_dir / snapshot
        try:
            chapter_raw = (snapshot_root / binding_path).read_bytes()
            commit_raw = (snapshot_root / commit_rel).read_bytes()
        except OSError:
            return False
        return _binding_bytes_match(binding, chapter_raw) and _commit_bytes_match(
            expected_commit_hash,
            commit_raw,
        )
    if mode == "git":
        tag_name = str(receipt.get("tag") or "")
        if tag_name != f"ch{chapter:04d}":
            return False
        try:
            check = subprocess.run(
                ["git", "rev-parse", "--verify", tag_name],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if check.returncode != 0:
                return False
            receipt_rel = (
                Path(".webnovel") / "backups" / f"ch{chapter:04d}.receipt.json"
            ).as_posix()
            stored = subprocess.run(
                ["git", "show", f"{tag_name}:{receipt_rel}"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if stored.returncode != 0:
                return False
            stored_receipt = json.loads(stored.stdout)
            stored_chapter = subprocess.run(
                ["git", "show", f"{tag_name}:{binding_path}"],
                cwd=project_root,
                capture_output=True,
                timeout=10,
                check=False,
            )
            stored_commit = subprocess.run(
                ["git", "show", f"{tag_name}:{commit_rel}"],
                cwd=project_root,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return False
        return bool(
            stored_receipt == receipt
            and stored_chapter.returncode == 0
            and stored_commit.returncode == 0
            and _binding_bytes_match(binding, stored_chapter.stdout)
            and _commit_bytes_match(expected_commit_hash, stored_commit.stdout)
        )
    return False


def _latest_contract_mtime(project_root: Path, chapter: int) -> int:
    mtimes: list[int] = []
    for path in contract_files_for_chapter(project_root, chapter).values():
        if path.is_file():
            mtimes.append(path.stat().st_mtime_ns)
    return max(mtimes or [0])


def build_write_resume_plan(
    project_root: str | Path,
    *,
    chapter: int,
    mode: str = "default",
) -> dict[str, Any]:
    root = Path(project_root)
    ledger = load_ledger(root)
    run = ((ledger.get("write") or {}).get(_chapter_key(chapter)) or {})
    if not isinstance(run, dict):
        run = {}

    chapter_file = find_chapter_file(root, chapter)
    draft_entry = _step_completed(run, "draft")
    review_entry = _step_completed(run, "review")
    data_entry = _step_completed(run, "data")
    backup_entry = _step_completed(run, "backup")
    commit_status, commit_binding_trusted = _commit_state(root, chapter)
    accepted_done = commit_status == "accepted" and commit_binding_trusted
    rejected_done = commit_status == "rejected"

    steps: list[dict[str, str]] = []
    confirmations: list[dict[str, str]] = []

    draft_trusted = bool(accepted_done or (chapter_file and _trusted_output(draft_entry, "chapter_file")))
    if draft_entry and chapter_file and not draft_trusted:
        confirmations.append(
            {
                "code": "chapter_file_changed",
                "message": "正文文件与上次记录不一致，需要确认沿用手改正文还是重新起草。",
            }
        )
    if draft_trusted and chapter_file and _latest_contract_mtime(root, chapter) > chapter_file.stat().st_mtime_ns:
        draft_trusted = False
        confirmations.append(
            {
                "code": "outline_newer_than_draft",
                "message": "章纲或合同晚于正文，需要确认沿用旧正文还是重新起草。",
            }
        )
    steps.append({"step": "draft", "action": "skip" if draft_trusted else "run", "reason": "正文可信" if draft_trusted else "正文缺失或已过期"})

    review_path = root / COMMIT_ARTIFACT_FILES[0]
    review_trusted = bool(
        draft_trusted
        and review_path.is_file()
        and _artifact_binding_trusted(root, chapter, review_path)
        and (accepted_done or _trusted_input(review_entry, "chapter_file", chapter_file))
    )
    steps.append({"step": "review", "action": "skip" if review_trusted else "run", "reason": "审查结果匹配当前正文" if review_trusted else "正文变更后需要重审"})

    data_paths = [root / rel for rel in COMMIT_ARTIFACT_FILES[1:]]
    data_trusted = bool(
        review_trusted
        and all(path.is_file() for path in data_paths)
        and all(_artifact_binding_trusted(root, chapter, path) for path in data_paths)
        and (accepted_done or _trusted_input(data_entry, "chapter_file", chapter_file))
    )
    steps.append({"step": "data", "action": "skip" if data_trusted else "run", "reason": "故事事实提取可信" if data_trusted else "data artifacts 缺失或过期"})

    if accepted_done:
        confirmations.append(
            {
                "code": "chapter_already_accepted",
                "message": "本章已 accepted；重跑前需要确认是重写正文，还是只查看状态/补跑后续步骤。",
            }
        )
    elif commit_status == "accepted":
        confirmations.append(
            {
                "code": "chapter_commit_stale",
                "message": "本章 accepted commit 与当前正文不匹配或缺少绑定；必须重跑审查、data 和 commit。",
            }
        )
    if rejected_done:
        confirmations.append(
            {
                "code": "chapter_commit_rejected",
                "message": "本章事实提交未通过，需要先处理审查/大纲/消歧阻断项，再重新提交。",
            }
        )
    commit_reason = (
        f"commit status={commit_status}"
        if accepted_done
        else "commit rejected，需要修复后重新提交"
        if rejected_done
        else "尚未生成 commit"
    )
    steps.append({"step": "commit", "action": "skip" if accepted_done else "run", "reason": commit_reason})

    projection_done = bool(accepted_done and _projection_done(root, chapter))
    projection_action = "skip" if projection_done else ("retry" if accepted_done else "run")
    projection_reason = (
        "资料更新已完成"
        if projection_done
        else "commit accepted 后再更新资料"
        if not accepted_done
        else "需要补跑资料更新"
    )
    steps.append({"step": "projection", "action": projection_action, "reason": projection_reason})

    backup_done = bool(
        accepted_done
        and backup_receipt_trusted(root, chapter)
        and _trusted_input(backup_entry, "chapter_file", chapter_file)
    )
    backup_action = "skip" if backup_done else ("retry" if accepted_done else "run")
    steps.append({"step": "backup", "action": backup_action, "reason": "备份已确认" if backup_done else "备份未确认"})

    resume_from = "done"
    for item in steps:
        if item["action"] != "skip":
            resume_from = item["step"]
            break

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "write",
        "chapter": int(chapter),
        "mode": mode or "default",
        "resume_from": resume_from,
        "steps": steps,
        "needs_user_confirmation": confirmations,
    }


def format_resume_plan(plan: dict[str, Any], output_format: str = "json") -> str:
    if output_format == "json":
        return json.dumps(plan, ensure_ascii=False, indent=2)
    lines = [
        f"resume_from: {plan.get('resume_from')}",
        f"chapter: {plan.get('chapter')}",
    ]
    for item in plan.get("steps") or []:
        lines.append(f"- {item.get('step')}: {item.get('action')} ({item.get('reason')})")
    confirmations = plan.get("needs_user_confirmation") or []
    if confirmations:
        lines.append("needs_user_confirmation:")
        lines.extend(f"- {item.get('code')}: {item.get('message')}" for item in confirmations)
    return "\n".join(lines)


def _parse_path_map(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"不是合法 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("必须是 JSON object")
    return {str(key): str(value) for key, value in payload.items()}


def _parse_string_list(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"不是合法 JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("必须是 JSON list")
    return [str(item) for item in payload]


def main() -> None:
    parser = argparse.ArgumentParser(description="Record and inspect webnovel write run ledger")
    parser.add_argument("--project-root", required=True, help="书项目根目录")
    sub = parser.add_subparsers(dest="action", required=True)
    record = sub.add_parser("record-write-step", help="记录写章步骤状态")
    record.add_argument("--chapter", type=int, required=True)
    record.add_argument("--step", choices=WRITE_STEPS, required=True)
    record.add_argument("--status", required=True)
    record.add_argument("--mode", default="default")
    record.add_argument("--inputs-json", default="{}")
    record.add_argument("--outputs-json", default="{}")
    record.add_argument("--problems-json", default="[]")
    record.add_argument("--auto-handled-json", default="[]")
    record.add_argument("--duration-ms", type=int, default=0)
    record.add_argument("--format", choices=["json", "text"], default="json")
    resume = sub.add_parser("write-resume", help="输出写章断点续跑建议")
    resume.add_argument("--chapter", type=int, required=True)
    resume.add_argument("--mode", default="default")
    resume.add_argument("--format", choices=["json", "text"], default="json")
    args = parser.parse_args()

    if args.action == "record-write-step":
        try:
            entry = record_write_step(
                args.project_root,
                chapter=args.chapter,
                step=args.step,
                status=args.status,
                mode=args.mode,
                inputs=_parse_path_map(args.inputs_json),
                outputs=_parse_path_map(args.outputs_json),
                problems=_parse_string_list(args.problems_json),
                auto_handled=_parse_string_list(args.auto_handled_json),
                duration_ms=args.duration_ms,
            )
        except ValueError as exc:
            raise SystemExit(str(exc))
        if args.format == "json":
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        else:
            print(f"{entry['step']}: {entry['status']}")
        return

    if args.action == "write-resume":
        plan = build_write_resume_plan(args.project_root, chapter=args.chapter, mode=args.mode)
        print(format_resume_plan(plan, args.format))


if __name__ == "__main__":
    main()
