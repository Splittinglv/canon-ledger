#!/usr/bin/env python3
"""
Git 集成备份管理系统 (Backup Manager with Git)

核心理念：写 200万字必然会"写废设定"，需要支持任意时间点回滚。

🔧 重大升级：使用 Git 进行原子性版本控制

为什么选择 Git：
1. ✅ 原子性回滚：state.json + 正文/*.md 同时回滚，数据 100% 一致
2. ✅ 增量存储：只存储 diff，节省 95% 空间
3. ✅ 成熟稳定：经过 20 年验证的版本控制系统
4. ✅ 分支管理：天然支持"平行世界"创作

功能：
1. 自动 Git 提交：每次 /canon-ledger-write 完成后自动 commit
2. 原子性回滚：git checkout 同时回滚所有文件
3. 版本历史：git log 查看完整历史
4. 差异对比：git diff 查看任意两个版本的差异
5. 分支创建：git branch 从任意时间点创建分支

使用方式：
  # 在第 45 章完成后自动备份（自动 git commit）
  python backup_manager.py --chapter 45

  # 回滚到第 30 章状态（git checkout）
  python backup_manager.py --rollback 30

  # 查看第 20 章和第 40 章的差异（git diff）
  python backup_manager.py --diff 20 40

  # 从第 50 章创建分支（git branch）
  python backup_manager.py --create-branch 50 --branch-name "alternative-ending"

  # 列出所有备份（git log）
  python backup_manager.py --list

Git 提交规范：
  - 提交信息格式: "Chapter {N}: {章节标题}"
  - Tag 格式: "ch{N}" (如 ch0045)
  - 每个章节对应一个 commit + 一个 tag

数据一致性保证：
  ✅ 回滚时，state.json 和所有 .md 文件同步回滚
  ✅ 不会出现"状态记录筑基期，但文件里写着金丹期"的数据撕裂
  ✅ 原子性操作，要么全部成功，要么全部失败
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio
from datetime import datetime
from typing import Optional, List, Tuple

# ============================================================================
# 安全修复：导入安全工具函数（P1 MEDIUM）
# ============================================================================
from security_utils import sanitize_commit_message, is_git_available, is_git_repo, git_graceful_operation
from project_locator import resolve_project_root
from data_modules.chapter_content_binding import verify_commit_content_binding
from data_modules.projection_log import commit_hash
from data_modules.run_ledger import (
    GIT_BACKUP_RECEIPT_SCHEMA,
    LOCAL_BACKUP_RECEIPT_SCHEMA,
    LOCAL_SNAPSHOT_MANIFEST,
    LOCAL_SNAPSHOT_ROOTS,
    build_local_snapshot_manifest,
    ensure_backup_integrity_key,
    local_snapshot_manifest_trusted,
    local_snapshot_receipt_trusted,
    sign_backup_payload,
)

# Windows 编码兼容性修复
if sys.platform == "win32":
    enable_windows_utf8_stdio()


class BackupError(RuntimeError):
    """Git backup operation failed."""


class GitBackupManager:
    """基于 Git 的备份管理器（支持优雅降级）"""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.git_dir = self.project_root / ".git"
        self.git_available = is_git_available()

        if not self.git_available:
            print("⚠️  Git 不可用，将使用本地备份模式")
            print("💡 如需启用 Git 版本控制，请安装 Git: https://git-scm.com/")
            return

        # 检查 Git 是否初始化
        if not self.git_dir.exists():
            print("⚠️  Git 未初始化，请先运行 /canon-ledger-init 或手动执行 git init")
            print("💡 现在自动初始化 Git...")
            self._init_git()

    def _init_git(self) -> bool:
        """初始化 Git 仓库"""
        try:
            # git init
            subprocess.run(
                ["git", "init"],
                cwd=self.project_root,
                check=True,
                capture_output=True
            )

            # 创建 .gitignore
            gitignore_file = self.project_root / ".gitignore"
            if not gitignore_file.exists():
                with open(gitignore_file, 'w', encoding='utf-8') as f:
                    f.write("""# Python
__pycache__/
*.py[cod]
*.so

# Temporary files
*.tmp
*.bak
.DS_Store

# IDE
.vscode/
.idea/

# Don't ignore .canon-ledger (we need to track state.json)
# But ignore cache files
.canon-ledger/context_cache.json
.canon-ledger/backups/.integrity-key

# Env files
.env
.env.*
!.env.example
""")

            # 初始提交
            subprocess.run(
                ["git", "add", "."],
                cwd=self.project_root,
                check=True,
                capture_output=True
            )

            subprocess.run(
                ["git", "commit", "-m", "Initial commit: Project initialized"],
                cwd=self.project_root,
                check=True,
                capture_output=True
            )

            print("✅ Git 仓库已初始化")
            return True

        except subprocess.CalledProcessError as e:
            print(f"❌ Git 初始化失败: {e}")
            return False

    def _run_git_command(self, args: List[str], check: bool = True) -> Tuple[bool, str, str]:
        """执行 Git 命令（支持优雅降级）"""
        if not self.git_available:
            return False, "", "Git 不可用"

        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60
            )
            ok = result.returncode == 0
            if check and not ok:
                message = (result.stderr or result.stdout).strip()
                raise BackupError(f"git {' '.join(args)} 失败: {message}")
            return ok, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            if check:
                raise BackupError(f"git {' '.join(args)} 失败: Git 命令超时")
            return False, "", "Git 命令超时"
        except OSError as e:
            if check:
                raise BackupError(f"git {' '.join(args)} 失败: {e}")
            return False, "", str(e)

    @staticmethod
    def _format_git_output(stdout: str, stderr: str) -> str:
        """合并 Git 输出，优先保留 stderr 中的故障信息。"""
        return "\n".join(part.strip() for part in (stderr, stdout) if part.strip())

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)

    @classmethod
    def _assert_copyable_tree(cls, source: Path, *, exclude_top: set[str] | None = None) -> None:
        excluded = exclude_top or set()
        for child in source.iterdir():
            if child.name in excluded:
                continue
            if child.is_symlink():
                raise OSError(f"拒绝备份符号链接: {child}")
            if child.is_dir():
                cls._assert_copyable_tree(child)
            elif not child.is_file():
                raise OSError(f"拒绝备份特殊文件: {child}")

    @classmethod
    def _copy_tree(cls, source: Path, target: Path, *, exclude_top: set[str] | None = None) -> None:
        excluded = exclude_top or set()
        cls._assert_copyable_tree(source, exclude_top=excluded)

        def ignore(directory: str, names: list[str]) -> list[str]:
            if Path(directory) == source:
                return sorted(set(names) & excluded)
            return []

        shutil.copytree(source, target, ignore=ignore)

    def _copy_consistency_roots(self, target_root: Path) -> list[str]:
        copied: list[str] = []
        for root_name in LOCAL_SNAPSHOT_ROOTS:
            source = self.project_root / root_name
            if source.is_symlink():
                raise OSError(f"一致性根目录不能是符号链接: {source}")
            if not source.exists():
                continue
            if not source.is_dir():
                raise OSError(f"一致性根目录不是安全目录: {source}")
            excluded = {"backups"} if root_name == ".canon-ledger" else set()
            self._copy_tree(source, target_root / root_name, exclude_top=excluded)
            copied.append(root_name)
        return copied

    def _strict_snapshot_has_no_later_chapter(self, chapter_num: int) -> bool:
        commit_pattern = re.compile(r"^chapter_(\d+)\.commit\.json$")
        for path in (self.project_root / ".story-system" / "commits").glob(
            "chapter_*.commit.json"
        ):
            matched = commit_pattern.fullmatch(path.name)
            if matched and int(matched.group(1)) > chapter_num:
                print(
                    f"❌ 备份失败：发现晚于第 {chapter_num} 章的 canonical commit "
                    f"({path.name})"
                )
                return False
        chapter_pattern = re.compile(r"第0*(\d+)章")
        for path in (self.project_root / "正文").rglob("*"):
            if not path.is_file():
                continue
            matched = chapter_pattern.search(path.name)
            if matched and int(matched.group(1)) > chapter_num:
                print(
                    f"❌ 备份失败：正文中已有晚于第 {chapter_num} 章的文件 "
                    f"({path.relative_to(self.project_root)})"
                )
                return False
        return True

    def _latest_canonical_chapter(self, fallback: int) -> int:
        commit_pattern = re.compile(r"^chapter_(\d+)\.commit\.json$")
        chapters = []
        for path in (self.project_root / ".story-system" / "commits").glob(
            "chapter_*.commit.json"
        ):
            matched = commit_pattern.fullmatch(path.name)
            if matched:
                chapters.append(int(matched.group(1)))
        return max(chapters or [int(fallback)])

    @staticmethod
    def _json_bytes(payload: dict) -> bytes:
        return (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

    def _create_snapshot_tree(
        self,
        *,
        chapter_num: int,
        snapshot_name: str,
    ) -> tuple[Path, dict]:
        backup_dir = self.project_root / ".canon-ledger" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        if not ensure_backup_integrity_key(self.project_root):
            raise OSError("无法创建本地备份完整性密钥")
        backup_path = backup_dir / snapshot_name
        if backup_path.exists() or backup_path.is_symlink():
            raise OSError(f"本地快照路径已存在: {backup_path}")
        backup_path.mkdir(parents=True)
        try:
            self._copy_consistency_roots(backup_path)
            manifest = build_local_snapshot_manifest(
                self.project_root,
                backup_path,
                chapter=chapter_num,
                snapshot_name=snapshot_name,
                created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
            (backup_path / LOCAL_SNAPSHOT_MANIFEST).write_bytes(
                self._json_bytes(manifest)
            )
            return backup_path, manifest
        except Exception:
            shutil.rmtree(backup_path, ignore_errors=True)
            raise

    def _prune_local_snapshots(self, *, keep: int = 10) -> None:
        backup_dir = self.project_root / ".canon-ledger" / "backups"
        snapshots = sorted(
            (path for path in backup_dir.glob("snapshot_ch*") if path.is_dir()),
            key=lambda path: path.name,
        )
        for old_snapshot in snapshots[:-keep]:
            snapshot_name = old_snapshot.name
            shutil.rmtree(old_snapshot)
            for receipt_path in backup_dir.glob("ch*.receipt.json"):
                try:
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if receipt.get("snapshot") == snapshot_name:
                    receipt_path.unlink(missing_ok=True)

    def _local_backup(self, chapter_num: int, accepted_receipt: dict | None = None) -> bool:
        """创建带签名清单的完整本地一致性快照。"""
        backup_dir = self.project_root / ".canon-ledger" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"snapshot_ch{chapter_num:04d}_{timestamp}"

        try:
            if accepted_receipt is not None and not self._strict_snapshot_has_no_later_chapter(
                chapter_num
            ):
                return False
            backup_path, _manifest = self._create_snapshot_tree(
                chapter_num=chapter_num,
                snapshot_name=backup_name,
            )

            manifest_raw = (backup_path / LOCAL_SNAPSHOT_MANIFEST).read_bytes()
            receipt = {
                key: value
                for key, value in (accepted_receipt or {}).items()
                if key != "schema_version"
            }
            receipt.update(
                {
                    "schema_version": LOCAL_BACKUP_RECEIPT_SCHEMA,
                    "chapter": int(chapter_num),
                    "mode": "local",
                    "snapshot": backup_name,
                    "manifest_path": LOCAL_SNAPSHOT_MANIFEST,
                    "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                }
            )
            receipt = sign_backup_payload(self.project_root, receipt)
            self._write_receipt(receipt)
            if not local_snapshot_receipt_trusted(
                self.project_root,
                receipt,
                chapter=chapter_num,
                require_current_binding=accepted_receipt is not None,
            ):
                self._discard_receipt(chapter_num)
                shutil.rmtree(backup_path, ignore_errors=True)
                print("❌ 本地备份失败：快照、manifest 或 receipt 自校验未通过")
                return False

            self._prune_local_snapshots()

            print(f"✅ 本地备份完成: {backup_path}")
            print("📦 已备份: 正文、大纲、设定集、完整 .story-system 与必要 .canon-ledger 状态")
            return True
        except (OSError, ValueError) as e:
            print(f"❌ 本地备份失败: {e}")
            return False

    def _receipt_path(self, chapter_num: int) -> Path:
        return (
            self.project_root
            / ".canon-ledger"
            / "backups"
            / f"ch{chapter_num:04d}.receipt.json"
        )

    def _write_receipt(self, payload: dict) -> None:
        path = self._receipt_path(int(payload["chapter"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _discard_receipt(self, chapter_num: int) -> None:
        try:
            self._receipt_path(chapter_num).unlink(missing_ok=True)
        except OSError:
            pass

    def _accepted_receipt(self, chapter_num: int) -> dict | None:
        commit_path = (
            self.project_root
            / ".story-system"
            / "commits"
            / f"chapter_{chapter_num:03d}.commit.json"
        )
        try:
            payload = json.loads(commit_path.read_text(encoding="utf-8"))
            status = str(((payload.get("meta") or {}).get("status") or ""))
        except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
            print(f"❌ 备份失败：无法验证 accepted chapter commit: {exc}")
            return None
        binding_ok, binding_code = verify_commit_content_binding(
            self.project_root,
            chapter_num,
            payload,
        )
        if status != "accepted" or not binding_ok:
            reason = binding_code if not binding_ok else f"commit_status_{status or 'missing'}"
            print(f"❌ 备份失败：当前正文没有可信 accepted binding（{reason}）")
            return None
        return {
            "schema_version": GIT_BACKUP_RECEIPT_SCHEMA,
            "chapter": chapter_num,
            "chapter_binding": payload["chapter_binding"],
            "chapter_commit_path": commit_path.relative_to(self.project_root).as_posix(),
            "chapter_commit_hash": commit_hash(payload),
        }

    def _verify_backup_receipt(self, chapter_num: int) -> bool:
        try:
            from data_modules.run_ledger import backup_receipt_trusted

            return backup_receipt_trusted(self.project_root, chapter_num)
        except Exception:
            return False

    def backup(
        self,
        chapter_num: int,
        chapter_title: str = "",
        *,
        require_accepted_binding: bool = False,
    ) -> bool:
        """
        备份当前状态（Git commit + tag，或本地备份）

        Args:
            chapter_num: 章节号
            chapter_title: 章节标题（可选）
        """
        print(f"📝 正在备份第 {chapter_num} 章...")

        tag_name = f"ch{chapter_num:04d}"
        receipt = self._accepted_receipt(chapter_num) if require_accepted_binding else None
        if require_accepted_binding and receipt is None:
            return False

        if self.git_available:
            if receipt is not None:
                receipt.update({"mode": "git", "tag": tag_name})
            tag_exists, existing_target, _ = self._run_git_command(
                ["rev-parse", "--verify", tag_name],
                check=False,
            )
            if tag_exists:
                if receipt is not None:
                    receipt_rel = self._receipt_path(chapter_num).relative_to(
                        self.project_root
                    ).as_posix()
                    shown, stored_receipt, _ = self._run_git_command(
                        ["show", f"{tag_name}:{receipt_rel}"],
                        check=False,
                    )
                    try:
                        receipt_matches = shown and json.loads(stored_receipt) == receipt
                    except (json.JSONDecodeError, TypeError):
                        receipt_matches = False
                    if receipt_matches:
                        if self._verify_backup_receipt(chapter_num):
                            print(f"ℹ️  备份点 {tag_name} 已绑定当前 accepted 正文")
                            return True
                        print(f"❌ 备份失败：{tag_name} 缺少可验证的正文或 commit 快照")
                        return False
                print(f"❌ 备份失败：不可覆盖或移动已有备份点 {tag_name}")
                return False

        # 如果 Git 不可用，使用本地备份
        if not self.git_available:
            return self._local_backup(chapter_num, accepted_receipt=receipt)

        if receipt is not None:
            self._write_receipt(receipt)

        # Step 1: git add .
        success, stdout, stderr = self._run_git_command(["add", "."], check=False)
        if not success:
            if receipt is not None:
                self._discard_receipt(chapter_num)
            print(f"❌ 备份失败：git add 失败: {self._format_git_output(stdout, stderr)}")
            return False
        if receipt is not None:
            # These two files are the recovery proof.  Force-stage them even
            # when a project-level ignore rule would otherwise omit them.
            required_paths = [
                str(receipt["chapter_binding"]["path"]),
                str(receipt["chapter_commit_path"]),
                self._receipt_path(chapter_num).relative_to(
                    self.project_root
                ).as_posix(),
            ]
            success, stdout, stderr = self._run_git_command(
                ["add", "-f", "--", *required_paths],
                check=False,
            )
            if not success:
                self._discard_receipt(chapter_num)
                print(
                    "❌ 备份失败：无法将正文、commit 和 receipt 写入恢复点: "
                    f"{self._format_git_output(stdout, stderr)}"
                )
                return False

        # Step 2: git commit
        commit_message = f"Chapter {chapter_num}"
        if chapter_title:
            # ============================================================================
            # 安全修复：清理提交消息，防止命令注入 (CWE-77) - P1 MEDIUM
            # 原代码: commit_message += f": {chapter_title}"
            # 漏洞: chapter_title可能包含 Git 标志（如 --author, --amend）导致命令注入
            # ============================================================================
            safe_chapter_title = sanitize_commit_message(chapter_title)
            commit_message += f": {safe_chapter_title}"

        success, stdout, stderr = self._run_git_command(
            ["commit", "-m", commit_message],
            check=False  # 允许"无变更"的情况
        )
        commit_output = self._format_git_output(stdout, stderr)

        if not success and "nothing to commit" in commit_output.lower():
            if receipt is not None:
                receipt_rel = self._receipt_path(chapter_num).relative_to(
                    self.project_root
                ).as_posix()
                shown, stored_receipt, _ = self._run_git_command(
                    ["show", f"HEAD:{receipt_rel}"],
                    check=False,
                )
                try:
                    receipt_matches = shown and json.loads(stored_receipt) == receipt
                except (json.JSONDecodeError, TypeError):
                    receipt_matches = False
                if receipt_matches:
                    tag_ok, tag_out, tag_err = self._run_git_command(
                        ["tag", tag_name],
                        check=False,
                    )
                    if tag_ok:
                        if receipt is None or self._verify_backup_receipt(chapter_num):
                            print(f"✅ Git tag 已从已验证的 receipt 恢复: {tag_name}")
                            return True
                        print(f"❌ 备份失败：{tag_name} 中的正文或 commit 与 receipt 不一致")
                        return False
                    print(
                        f"❌ 创建 tag 失败: "
                        f"{self._format_git_output(tag_out, tag_err)}"
                    )
                    return False
                self._discard_receipt(chapter_num)
            print("⚠️  本章无变更，无法生成新的不可变备份点")
            return False
        elif not success:
            if receipt is not None:
                self._discard_receipt(chapter_num)
            print(f"❌ 备份失败：git commit 失败")
            if commit_output:
                print(commit_output)
            print("💡 请先运行: git config user.name \"你的名字\" && git config user.email \"you@example.com\"")
            return False

        print(f"✅ Git 提交完成: {commit_message}")

        # Step 3: git tag
        success, stdout, stderr = self._run_git_command(["tag", tag_name], check=False)
        if not success:
            print(f"❌ 创建 tag 失败: {self._format_git_output(stdout, stderr)}")
            return False
        else:
            print(f"✅ Git tag 已创建: {tag_name}")

        if receipt is not None and not self._verify_backup_receipt(chapter_num):
            print(f"❌ 备份失败：{tag_name} 不包含与 receipt 匹配的正文和 commit")
            return False
        return True

    def _restore_snapshot_tree(self, snapshot_root: Path) -> bool:
        if not local_snapshot_manifest_trusted(self.project_root, snapshot_root):
            print(f"❌ 拒绝恢复：快照清单校验失败 ({snapshot_root.name})")
            return False
        try:
            manifest = json.loads(
                (snapshot_root / LOCAL_SNAPSHOT_MANIFEST).read_text(encoding="utf-8")
            )
            root_presence = manifest["root_presence"]
            for root_name in LOCAL_SNAPSHOT_ROOTS:
                source = snapshot_root / root_name
                target = self.project_root / root_name
                present = bool(root_presence.get(root_name))
                if root_name == ".canon-ledger":
                    if target.is_symlink() or (target.exists() and not target.is_dir()):
                        raise OSError(".canon-ledger 不是安全目录")
                    target.mkdir(parents=True, exist_ok=True)
                    for child in list(target.iterdir()):
                        if child.name == "backups":
                            continue
                        self._remove_path(child)
                    if present:
                        for child in source.iterdir():
                            destination = target / child.name
                            if child.is_dir():
                                self._copy_tree(child, destination)
                            else:
                                shutil.copy2(child, destination)
                    continue

                self._remove_path(target)
                if present:
                    self._copy_tree(source, target)
            return True
        except Exception as exc:
            print(f"❌ 安装本地快照失败: {exc}")
            return False

    def _local_rollback(self, chapter_num: int) -> bool:
        receipt_path = self._receipt_path(chapter_num)
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"❌ 本地备份点 ch{chapter_num:04d} 不存在或 receipt 无效: {exc}")
            return False
        if not local_snapshot_receipt_trusted(
            self.project_root,
            receipt,
            chapter=chapter_num,
            require_current_binding=False,
        ):
            print("❌ 拒绝恢复：本地 receipt、manifest 或文件清单校验失败")
            return False

        snapshot_root = (
            self.project_root / ".canon-ledger" / "backups" / str(receipt["snapshot"])
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        rescue_chapter = self._latest_canonical_chapter(chapter_num)
        rescue_name = (
            f"rescue_before_restore_ch{chapter_num:04d}_from_ch"
            f"{rescue_chapter:04d}_{timestamp}"
        )
        try:
            rescue_root, _rescue_manifest = self._create_snapshot_tree(
                chapter_num=rescue_chapter,
                snapshot_name=rescue_name,
            )
        except (OSError, ValueError) as exc:
            print(f"❌ 恢复前救援快照创建失败，未改动项目: {exc}")
            return False

        print(f"💾 恢复前救援快照: {rescue_root}")
        if not self._restore_snapshot_tree(snapshot_root):
            print("↩️  正在从救援快照恢复原状...")
            if not self._restore_snapshot_tree(rescue_root):
                print(f"⚠️  自动救援失败，请从此快照手动恢复: {rescue_root}")
            return False

        try:
            from data_modules.projection_rebuild import rebuild_all_projections

            report = rebuild_all_projections(
                self.project_root,
                reason=f"local_rollback_chapter_{chapter_num}",
            )
        except Exception as exc:
            report = {"ok": False, "error": "projection_rebuild_exception", "detail": str(exc)}
        if not bool(report.get("ok")):
            print(
                "❌ 本地快照已安装，但统一投影重建失败；正在从救援快照恢复原状: "
                f"{report.get('error') or 'unknown'}"
            )
            if not self._restore_snapshot_tree(rescue_root):
                print(f"⚠️  自动救援失败，请从此快照手动恢复: {rescue_root}")
            return False

        print(f"✅ 已从本地完整快照恢复到第 {chapter_num} 章")
        print("✅ canonical 数据、正文与 .canon-ledger 状态已同步，投影已统一重建")
        print(f"💡 恢复前状态仍保留在救援快照: {rescue_root}")
        return True

    def rollback(self, chapter_num: int) -> bool:
        """
        前滚式恢复到指定章节（在当前分支创建恢复提交）
        """

        tag_name = f"ch{chapter_num:04d}"

        print(f"🔄 正在回滚到第 {chapter_num} 章...")
        print("💾 将在当前分支创建一个恢复提交，历史不会丢失")

        if not self.git_available:
            print("💾 Git 不可用，将使用已签名的本地完整快照恢复")
            return self._local_rollback(chapter_num)

        success, _, error = self._run_git_command(["rev-parse", "--verify", tag_name], check=False)
        if not success:
            print(f"❌ 备份点 {tag_name} 不存在")
            return False

        success, branch, branch_error = self._run_git_command(["symbolic-ref", "--short", "HEAD"], check=False)
        if not success or not branch.strip():
            print(f"❌ 当前不在分支上，无法创建前滚恢复提交: {self._format_git_output(branch, branch_error)}")
            return False

        success, stdout, stderr = self._run_git_command(["checkout", tag_name, "--", "."], check=False)

        if not success:
            print(f"❌ 回滚失败: {self._format_git_output(stdout, stderr)}")
            print(f"💡 提示：确保 tag '{tag_name}' 存在（运行 --list 查看所有备份）")
            return False

        success, stdout, stderr = self._run_git_command(["add", "-A"], check=False)
        if not success:
            print(f"❌ 回滚失败: {self._format_git_output(stdout, stderr)}")
            return False

        success, stdout, stderr = self._run_git_command(
            ["commit", "-m", f"rollback: 恢复到 {tag_name} 备份点"],
            check=False,
        )
        commit_output = self._format_git_output(stdout, stderr)
        if not success and "nothing to commit" not in commit_output.lower():
            print(f"❌ 回滚提交失败: {commit_output}")
            return False

        print(f"✅ 已在 {branch.strip()} 分支恢复到第 {chapter_num} 章！")
        print(f"\n💡 提示:")
        print(f"  - 所有文件（state.json + 正文/*.md）已同步恢复")
        print(f"  - 历史提交保留，可用 git log 查看恢复记录")

        return True

    def diff(self, chapter_a: int, chapter_b: int):
        """对比两个版本的差异（Git diff）"""

        tag_a = f"ch{chapter_a:04d}"
        tag_b = f"ch{chapter_b:04d}"

        print(f"📊 对比第 {chapter_a} 章 与 第 {chapter_b} 章的差异...\n")

        success, output, error = self._run_git_command(["diff", tag_a, tag_b, "--stat"], check=False)

        if not success:
            print(f"❌ 对比失败: {self._format_git_output(output, error)}")
            return

        print("📈 文件变更统计：")
        print(output)

        # 显示 state.json 的详细差异
        print("\n📝 state.json 详细差异：")
        success, state_diff, _ = self._run_git_command(
            ["diff", tag_a, tag_b, "--", ".canon-ledger/state.json"],
            check=False,
        )

        if success and state_diff:
            print(state_diff[:2000])  # 限制输出长度
            if len(state_diff) > 2000:
                print("\n...(输出过长，已截断)")
        else:
            print("(无变更)")

    def list_backups(self):
        """列出所有备份（Git log + tags）"""

        print("\n📚 备份列表（Git tags）：\n")

        # 获取所有 tags
        success, tags_output, _ = self._run_git_command(["tag", "-l", "ch*"], check=False)

        if not success or not tags_output:
            print("⚠️  暂无备份")
            return

        tags = sorted(tags_output.strip().split('\n'))

        for tag in tags:
            # 提取章节号
            chapter_num = int(tag[2:])

            # 获取该 tag 的提交信息
            success, commit_info, _ = self._run_git_command(
                ["log", tag, "-1", "--format=%h %ci %s"],
                check=False,
            )

            if success:
                print(f"📖 {tag} | {commit_info.strip()}")

        print(f"\n总计：{len(tags)} 个备份")

        # 显示最近 5 次提交
        print("\n📜 最近提交历史：\n")
        success, log_output, _ = self._run_git_command(
            ["log", "--oneline", "-5"],
            check=False,
        )

        if success:
            print(log_output)

    def create_branch(self, chapter_num: int, branch_name: str) -> bool:
        """从指定章节创建分支（Git branch）"""

        tag_name = f"ch{chapter_num:04d}"

        print(f"🌿 从第 {chapter_num} 章创建分支: {branch_name}")

        # 检查 tag 是否存在
        success, _, _ = self._run_git_command(["rev-parse", tag_name], check=False)

        if not success:
            print(f"❌ Tag '{tag_name}' 不存在")
            return False

        # 创建分支
        success, output, error = self._run_git_command(["branch", branch_name, tag_name], check=False)

        if not success:
            print(f"❌ 创建分支失败: {self._format_git_output(output, error)}")
            return False

        print(f"✅ 分支已创建: {branch_name}")
        print(f"\n💡 切换到分支:")
        print(f"  git checkout {branch_name}")

        return True

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Git 集成备份管理系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 在第 45 章完成后自动备份
  python backup_manager.py --chapter 45

  # 回滚到第 30 章（原子性：state.json + 所有 .md 文件）
  python backup_manager.py --rollback 30

  # 查看第 20 章和第 40 章的差异
  python backup_manager.py --diff 20 40

  # 从第 50 章创建分支
  python backup_manager.py --create-branch 50 --branch-name "alternative-ending"

  # 列出所有备份
  python backup_manager.py --list
        """
    )

    parser.add_argument('--chapter', type=int, help='备份章节号')
    parser.add_argument('--chapter-title', help='章节标题（可选）')
    parser.add_argument(
        '--require-accepted-binding',
        action='store_true',
        help='写章流程使用：只备份与当前正文匹配的 accepted commit',
    )
    parser.add_argument('--rollback', type=int, metavar='CHAPTER', help='回滚到指定章节')
    parser.add_argument('--diff', nargs=2, type=int, metavar=('A', 'B'), help='对比两个版本')
    parser.add_argument('--create-branch', type=int, metavar='CHAPTER', help='从指定章节创建分支')
    parser.add_argument('--branch-name', help='分支名称')
    parser.add_argument('--list', action='store_true', help='列出所有备份')
    parser.add_argument('--project-root', default='.', help='项目根目录')

    args = parser.parse_args()

    # 解析项目根目录（允许传入“工作区根目录”，统一解析到真正的 book project_root）
    try:
        project_root = str(resolve_project_root(args.project_root))
    except FileNotFoundError as exc:
        print(f"❌ 无法定位项目根目录（需要包含 .canon-ledger/state.json）: {exc}", file=sys.stderr)
        sys.exit(1)

    # 创建管理器
    manager = GitBackupManager(project_root)

    # 执行操作
    if args.chapter:
        ok = manager.backup(
            args.chapter,
            args.chapter_title or "",
            require_accepted_binding=args.require_accepted_binding,
        )
        if not ok:
            sys.exit(1)

    elif args.rollback:
        if not manager.rollback(args.rollback):
            sys.exit(1)

    elif args.diff:
        manager.diff(args.diff[0], args.diff[1])

    elif args.create_branch:
        if not args.branch_name:
            print("❌ 创建分支需要 --branch-name 参数")
            sys.exit(1)
        manager.create_branch(args.create_branch, args.branch_name)

    elif args.list:
        manager.list_backups()

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
