#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import json
import os
import posixpath
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

PROTECTED_SUFFIXES = (
    ".canon-ledger/state.json",
    ".canon-ledger/index.db",
    ".canon-ledger/vectors.db",
    ".canon-ledger/memory_scratchpad.json",
    ".canon-ledger/projection_log.jsonl",
)
PROTECTED_BASENAMES = (
    "state.json",
    "index.db",
    "vectors.db",
    "memory_scratchpad.json",
    "projection_log.jsonl",
)
SHELL_CONTROL_RE = re.compile(r"(?:&&|\|\||[;&|<>`\r\n]|\$\()")
COMMIT_BASENAME_RE = re.compile(r"(?:^|[/\\])chapter_?\d+\.commit\.json(?:$|[\s'\"])", re.I)
DANGEROUS_COMMAND_RE = re.compile(
    r"(?im)(?:^|[;&|]\s*)(?:rm|cp|mv|tee|touch|truncate|dd|install|unlink|"
    r"perl|sed|bash|sh|zsh|fish|powershell|pwsh|cmd)\b"
)
RUNTIME_ENTRYPOINT_NAMES = {"canon_ledger.py"}
TRUSTED_RUNTIME_ENV_TOKENS = {
    "${SCRIPTS_DIR}/canon_ledger.py": ("SCRIPTS_DIR", "", "canon_ledger.py"),
    "$SCRIPTS_DIR/canon_ledger.py": ("SCRIPTS_DIR", "", "canon_ledger.py"),
    "${CANON_LEDGER_PLUGIN_ROOT}/scripts/canon_ledger.py": (
        "CANON_LEDGER_PLUGIN_ROOT", "scripts", "canon_ledger.py"
    ),
    "$CANON_LEDGER_PLUGIN_ROOT/scripts/canon_ledger.py": (
        "CANON_LEDGER_PLUGIN_ROOT", "scripts", "canon_ledger.py"
    ),
    "${CURSOR_PLUGIN_ROOT}/scripts/canon_ledger.py": (
        "CURSOR_PLUGIN_ROOT", "scripts", "canon_ledger.py"
    ),
    "$CURSOR_PLUGIN_ROOT/scripts/canon_ledger.py": (
        "CURSOR_PLUGIN_ROOT", "scripts", "canon_ledger.py"
    ),
}
TRUSTED_PYTHON_ENV_TOKENS = {
    "${CANON_LEDGER_PYTHON}", "$CANON_LEDGER_PYTHON",
}
PYTHON_INTERPRETERS = {"python", "python3", "python.exe", "python3.exe", "py", "py.exe", "pypy", "pypy3"}
DENIED_INLINE_INTERPRETERS = {
    "awk": set(),
    "bash": {"-c"},
    "dash": {"-c"},
    "ksh": {"-c"},
    "sh": {"-c"},
    "zsh": {"-c"},
    "fish": {"-c"},
    "gawk": set(),
    "perl": {"-e"},
    "ruby": {"-e"},
    "bun": {"-e", "--eval"},
    "deno": {"eval"},
    "node": {"-e", "--eval"},
    "nodejs": {"-e", "--eval"},
    "nawk": set(),
    "php": {"-r"},
    "lua": {"-e"},
    "luajit": {"-e"},
    "r": {"-e"},
    "rscript": {"-e"},
    "powershell": {"-command", "-c"},
    "pwsh": {"-command", "-c"},
}
INTERPRETER_WRAPPERS = {"busybox", "command", "env", "exec", "nice", "nohup", "sudo", "time", "xargs"}
TRUSTED_SCRIPTS_ENV_TOKENS = {
    "${SCRIPTS_DIR}": ("SCRIPTS_DIR", ""),
    "$SCRIPTS_DIR": ("SCRIPTS_DIR", ""),
    "${CANON_LEDGER_PLUGIN_ROOT}/scripts": ("CANON_LEDGER_PLUGIN_ROOT", "scripts"),
    "$CANON_LEDGER_PLUGIN_ROOT/scripts": ("CANON_LEDGER_PLUGIN_ROOT", "scripts"),
    "${CURSOR_PLUGIN_ROOT}/scripts": ("CURSOR_PLUGIN_ROOT", "scripts"),
    "$CURSOR_PLUGIN_ROOT/scripts": ("CURSOR_PLUGIN_ROOT", "scripts"),
}
TRUSTED_PLUGIN_SCRIPT_NAMES = {
    "bootstrap_env.py",
    "canon_ledger.py",
    "export_cursor_env.py",
    "reference_search.py",
}
BOOTSTRAP_SCRIPT_TOKENS = {
    "$_PLUGIN_ROOT_HINT/scripts/bootstrap_env.py",
    "${_PLUGIN_ROOT_HINT}/scripts/bootstrap_env.py",
}
# Skill 代码块的首行环境守卫（POSIX 空操作 + ${VAR:?} 参数校验）。
# 只有与随包文本完全一致的守卫行会被剥离；改写过的变体不享受豁免。
_ENV_GUARD_G1 = (
    ': "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md '
    '开头的环境引导代码块，再重试本块}"'
)
_ENV_GUARD_G2 = (
    _ENV_GUARD_G1
    + ' "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill '
    '解析项目根的代码块，再重试本块}"'
)
ENV_GUARD_LINES = {_ENV_GUARD_G1, _ENV_GUARD_G2}


def _strip_env_guard_lines(command: str) -> str:
    lines = command.strip().splitlines()
    while lines and lines[0].strip() in ENV_GUARD_LINES:
        lines = lines[1:]
    return "\n".join(lines).strip()


@dataclass(frozen=True)
class _ShellWord:
    value: str
    start: int
    end: int
    dynamic: bool = False


class _UnsafeInlineCode(ValueError):
    """Raised when inline interpreter code exceeds the read-only capability set."""


class _ShellCommandScanner:
    """Small fail-closed lexer for locating commands inside ``$(...)`` blocks.

    It is intentionally not a full shell parser.  It only preserves the word and
    quote boundaries needed to find interpreter invocations.  Unsupported or
    unbalanced syntax is reported as unsafe when an inline interpreter is present.
    """

    def __init__(self, source: str):
        self.source = source
        self.commands: list[list[_ShellWord]] = []

    def scan(self) -> list[list[_ShellWord]]:
        end = self._scan_sequence(0, closing_parenthesis=False)
        if end != len(self.source):
            raise ValueError("Shell 命令结构无法完整解析")
        return self.commands

    def _scan_sequence(self, index: int, *, closing_parenthesis: bool) -> int:
        words: list[_ShellWord] = []
        buffer: list[str] = []
        word_start: int | None = None
        word_dynamic = False
        quote = ""

        def start_word(at: int) -> None:
            nonlocal word_start
            if word_start is None:
                word_start = at

        def flush_word(at: int) -> None:
            nonlocal buffer, word_start, word_dynamic
            if word_start is not None:
                words.append(_ShellWord("".join(buffer), word_start, at, word_dynamic))
            buffer = []
            word_start = None
            word_dynamic = False

        def flush_command(at: int) -> None:
            flush_word(at)
            if words:
                self.commands.append(list(words))
                words.clear()

        source = self.source
        while index < len(source):
            char = source[index]
            if quote == "'":
                if char == "'":
                    quote = ""
                else:
                    buffer.append(char)
                index += 1
                continue
            if quote == '"':
                if char == '"':
                    quote = ""
                    index += 1
                    continue
                if char == "\\":
                    if index + 1 >= len(source):
                        raise ValueError("双引号中的转义不完整")
                    following = source[index + 1]
                    if following in {'"', "$", "`", "\\"}:
                        buffer.append(following)
                    elif following not in {"\n", "\r"}:
                        buffer.extend((char, following))
                    index += 2
                    continue
                if char == "`":
                    raise ValueError("不支持反引号命令替换")
                if char == "$" and index + 1 < len(source) and source[index + 1] == "(":
                    if index + 2 < len(source) and source[index + 2] == "(":
                        raise ValueError("不支持算术命令替换")
                    start_word(index)
                    buffer.append("命令替换")
                    word_dynamic = True
                    index = self._scan_sequence(index + 2, closing_parenthesis=True)
                    continue
                buffer.append(char)
                index += 1
                continue

            if char in {" ", "\t"}:
                flush_word(index)
                index += 1
                continue
            if char in {"\n", "\r", ";", "&", "|"}:
                flush_command(index)
                if index + 1 < len(source) and source[index + 1] == char and char in {"&", "|"}:
                    index += 2
                else:
                    index += 1
                continue
            if char in {"<", ">"}:
                flush_word(index)
                end = index + 1
                if end < len(source) and source[end] == char:
                    end += 1
                words.append(_ShellWord(source[index:end], index, end))
                index = end
                continue
            if char == ")" and closing_parenthesis:
                flush_command(index)
                return index + 1
            if char == "(" or (char == ")" and not closing_parenthesis):
                flush_command(index)
                index += 1
                continue
            if char == "#" and word_start is None:
                newline = source.find("\n", index)
                if newline < 0:
                    flush_command(len(source))
                    return len(source)
                flush_command(index)
                index = newline + 1
                continue
            if char == "'" or char == '"':
                start_word(index)
                quote = char
                index += 1
                continue
            if char == "\\":
                if index + 1 >= len(source):
                    raise ValueError("Shell 转义不完整")
                if source[index + 1] in {"\n", "\r"}:
                    index += 2
                    if source[index - 1] == "\r" and index < len(source) and source[index] == "\n":
                        index += 1
                    continue
                start_word(index)
                buffer.append(source[index + 1])
                index += 2
                continue
            if char == "`":
                raise ValueError("不支持反引号命令替换")
            if char == "$" and index + 1 < len(source) and source[index + 1] == "(":
                if index + 2 < len(source) and source[index + 2] == "(":
                    raise ValueError("不支持算术命令替换")
                start_word(index)
                buffer.append("命令替换")
                word_dynamic = True
                index = self._scan_sequence(index + 2, closing_parenthesis=True)
                continue
            start_word(index)
            buffer.append(char)
            index += 1

        if quote:
            raise ValueError("Shell 引号未闭合")
        if closing_parenthesis:
            raise ValueError("命令替换括号未闭合")
        flush_command(len(source))
        return len(source)


def _qualified_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else None
    return None


class _ReadOnlyPythonPolicy(ast.NodeVisitor):
    """Closed capability policy for the small Python snippets used by Skills."""

    _IMPORTS = {"json", "os", "pathlib", "re", "sys"}
    _FROM_IMPORTS = {
        "pathlib": {"Path"},
        "chapter_outline_loader": {"load_chapter_execution_directive"},
    }
    _CALLS = {
        "SystemExit",
        "all", "any", "bool", "dict", "int", "isinstance", "len", "list",
        "open", "print", "set", "str", "tuple",
        "json.load", "json.loads", "pathlib.Path", "re.sub",
        "sys.exit", "sys.stdout.write", "sys.path.insert",
        "chapter_outline_loader.load_chapter_execution_directive",
    }
    _METHOD_CALLS = {
        "endswith", "expanduser", "get", "is_absolute", "is_dir", "is_file",
        "join", "lower", "read_text", "resolve", "split", "splitlines", "startswith",
        "strip", "upper",
    }
    _MODULE_ATTRIBUTES = {
        "json.JSONDecodeError", "os.environ", "os.environ.get",
        "sys.argv", "sys.exit", "sys.path", "sys.path.insert", "sys.stdin",
        "sys.stdout", "sys.stdout.write", "re.sub",
    }
    _DATA_ATTRIBUTES = _METHOD_CALLS | {"parent"}

    def __init__(self, *, command_arguments: list[str]):
        self.aliases: dict[str, str] = {}
        self.command_arguments = command_arguments
        self.outline_path_ready = False

    def reject(self, reason: str) -> None:
        raise _UnsafeInlineCode(reason)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            self.reject("禁止访问双下划线能力")
        if isinstance(node.ctx, (ast.Store, ast.Del)) and (
            node.id in self.aliases or node.id in self._CALLS
        ):
            self.reject("禁止覆盖受信能力名称")

    def visit_Import(self, node: ast.Import) -> None:
        for imported in node.names:
            if imported.name not in self._IMPORTS:
                self.reject("导入不在只读白名单")
            self.aliases[imported.asname or imported.name] = imported.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        allowed = self._FROM_IMPORTS.get(module)
        if node.level or allowed is None:
            self.reject("相对导入或未知模块被拒绝")
        if module == "chapter_outline_loader" and not self.outline_path_ready:
            self.reject("章纲读取器必须绑定受信脚本目录")
        for imported in node.names:
            if imported.name == "*" or imported.name not in allowed:
                self.reject("导入成员不在只读白名单")
            self.aliases[imported.asname or imported.name] = f"{module}.{imported.name}"

    def _visit_assignment_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            if target.id.startswith("__") or target.id in self.aliases or target.id in self._CALLS:
                self.reject("禁止覆盖受信能力名称")
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._visit_assignment_target(item)
            return
        self.reject("只允许给局部变量赋值")

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._visit_assignment_target(target)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._visit_assignment_target(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._visit_assignment_target(node.target)
        self.visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._visit_assignment_target(node.target)
        self.visit(node.value)

    def visit_Delete(self, node: ast.Delete) -> None:
        self.reject("禁止删除对象")

    def visit_Global(self, node: ast.Global) -> None:
        self.reject("禁止修改全局作用域")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.reject("禁止修改外层作用域")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.reject("禁止定义可隐藏副作用的函数")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.reject("禁止定义可隐藏副作用的类型")

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.reject("禁止定义可隐藏副作用的匿名函数")

    def visit_Await(self, node: ast.Await) -> None:
        self.reject("禁止异步执行")

    def visit_Yield(self, node: ast.Yield) -> None:
        self.reject("禁止生成器执行")

    visit_YieldFrom = visit_Yield

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if not isinstance(node.ctx, ast.Load):
            self.reject("禁止修改对象属性")
        if node.attr.startswith("_"):
            self.reject("禁止访问私有或双下划线属性")
        qualified = _qualified_name(node, self.aliases)
        root = (qualified or "").split(".", 1)[0]
        if root in self._IMPORTS or root == "chapter_outline_loader":
            if qualified not in self._MODULE_ATTRIBUTES and qualified not in self._CALLS:
                self.reject("模块属性不在只读白名单")
        elif node.attr not in self._DATA_ATTRIBUTES:
            self.reject("对象属性不在只读白名单")
        self.visit(node.value)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if not isinstance(node.ctx, ast.Load):
            self.reject("禁止修改容器或环境内容")
        self.generic_visit(node)

    @staticmethod
    def _is_sys_argv_one(node: ast.AST, aliases: dict[str, str]) -> bool:
        if not isinstance(node, ast.Subscript):
            return False
        if _qualified_name(node.value, aliases) != "sys.argv":
            return False
        slice_node = node.slice
        return isinstance(slice_node, ast.Constant) and slice_node.value == 1

    def _validate_sys_path_insert(self, node: ast.Call) -> None:
        if (
            len(node.args) != 2
            or node.keywords
            or not isinstance(node.args[0], ast.Constant)
            or node.args[0].value != 0
            or not self._is_sys_argv_one(node.args[1], self.aliases)
            or not self.command_arguments
            or not _is_trusted_scripts_argument(self.command_arguments[0])
        ):
            self.reject("sys.path 只能插入受信插件脚本目录")
        self.outline_path_ready = True

    def _validate_open(self, node: ast.Call) -> None:
        if not node.args:
            self.reject("open 缺少读取目标")
        mode_nodes: list[ast.AST] = []
        if len(node.args) >= 2:
            mode_nodes.append(node.args[1])
        for keyword in node.keywords:
            if keyword.arg is None:
                self.reject("禁止通过字典展开传递文件模式")
            if keyword.arg == "opener":
                self.reject("禁止自定义文件打开器")
            if keyword.arg == "mode":
                mode_nodes.append(keyword.value)
        if len(mode_nodes) > 1:
            self.reject("文件模式定义冲突")
        if mode_nodes:
            mode = mode_nodes[0]
            if not isinstance(mode, ast.Constant) or mode.value not in {"r", "rt", "tr", "rb", "br"}:
                self.reject("open 只允许静态只读模式")

    def visit_Call(self, node: ast.Call) -> None:
        if any(isinstance(argument, ast.Starred) for argument in node.args) or any(
            keyword.arg is None for keyword in node.keywords
        ):
            self.reject("禁止动态展开调用参数")
        qualified = _qualified_name(node.func, self.aliases)
        attribute_name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if qualified == "open":
            self._validate_open(node)
        elif qualified == "sys.path.insert":
            self._validate_sys_path_insert(node)
        elif qualified == "chapter_outline_loader.load_chapter_execution_directive":
            if not self.outline_path_ready:
                self.reject("章纲读取器未绑定受信脚本目录")
        elif qualified in self._CALLS:
            pass
        elif attribute_name in self._METHOD_CALLS:
            pass
        else:
            self.reject("调用不在只读白名单")
        self.generic_visit(node)


def _is_trusted_scripts_argument(token: str) -> bool:
    trusted_scripts = (Path(__file__).resolve().parents[1] / "scripts").resolve(strict=False)
    normalized = token.replace("\\", "/")
    env_spec = TRUSTED_SCRIPTS_ENV_TOKENS.get(normalized)
    if env_spec is not None:
        env_name, suffix = env_spec
        raw_root = os.environ.get(env_name)
        if not raw_root:
            return False
        candidate = Path(raw_root).expanduser()
        if suffix:
            candidate /= suffix
    else:
        candidate = Path(token).expanduser()
        if not candidate.is_absolute():
            return False
    try:
        return candidate.resolve(strict=False) == trusted_scripts
    except (OSError, RuntimeError, ValueError):
        return False


def _trusted_plugin_script(token: str) -> Path | None:
    plugin_root = Path(__file__).resolve().parents[1]
    scripts_dir = (plugin_root / "scripts").resolve(strict=False)
    normalized = token.replace("\\", "/")
    candidate: Path | None = None

    scripts_prefixes = ("${SCRIPTS_DIR}/", "$SCRIPTS_DIR/")
    plugin_prefixes = {
        "${CANON_LEDGER_PLUGIN_ROOT}/scripts/": "CANON_LEDGER_PLUGIN_ROOT",
        "$CANON_LEDGER_PLUGIN_ROOT/scripts/": "CANON_LEDGER_PLUGIN_ROOT",
        "${CURSOR_PLUGIN_ROOT}/scripts/": "CURSOR_PLUGIN_ROOT",
        "$CURSOR_PLUGIN_ROOT/scripts/": "CURSOR_PLUGIN_ROOT",
    }
    for prefix in scripts_prefixes:
        if normalized.startswith(prefix):
            raw_scripts = os.environ.get("SCRIPTS_DIR")
            if not raw_scripts:
                return None
            base = Path(raw_scripts).expanduser().resolve(strict=False)
            if base != scripts_dir:
                return None
            candidate = base / normalized[len(prefix):]
            break
    if candidate is None:
        for prefix, env_name in plugin_prefixes.items():
            if not normalized.startswith(prefix):
                continue
            raw_root = os.environ.get(env_name)
            if not raw_root:
                return None
            base = Path(raw_root).expanduser().resolve(strict=False)
            if base != plugin_root.resolve(strict=False):
                return None
            candidate = base / "scripts" / normalized[len(prefix):]
            break
    if candidate is None:
        raw_candidate = Path(token).expanduser()
        if not raw_candidate.is_absolute():
            return None
        candidate = raw_candidate
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if resolved.parent != scripts_dir or resolved.name not in TRUSTED_PLUGIN_SCRIPT_NAMES:
        return None
    return resolved


def _has_validated_bootstrap_block(command: str) -> bool:
    """Match an exact shipped bootstrap block that runs ``bootstrap_env.py``.

    The hint-prefixed bootstrap may only run as the verbatim block shipped in
    a SKILL.md code fence; any edited variant loses the exemption and falls
    back to the strict path checks.
    """
    candidate = command.strip()
    if "/scripts/bootstrap_env.py" not in candidate or "_PLUGIN_ROOT_HINT" not in candidate:
        return False
    fence_pattern = re.compile(
        r"^```(?:bash|sh)\s*$\n(.*?)^```\s*$",
        re.MULTILINE | re.DOTALL,
    )
    skills_root = Path(__file__).resolve().parents[1] / "skills"
    try:
        skill_paths = sorted(skills_root.glob("*/SKILL.md"))
        for skill_path in skill_paths:
            text = skill_path.read_text(encoding="utf-8")
            for match in fence_pattern.finditer(text):
                shipped = match.group(1).strip()
                if (
                    shipped == candidate
                    and "/scripts/bootstrap_env.py" in shipped
                    and "_PLUGIN_ROOT_HINT" in shipped
                ):
                    return True
    except OSError:
        return False
    return False


def _python_script_execution_is_trusted(
    arguments: list[_ShellWord],
    *,
    executable: str,
    complete_command: str,
) -> bool:
    values = [word.value for word in arguments]
    if executable in {"py", "py.exe"} and values[:1] in (
        ["-3"], ["-3.10"], ["-3.11"], ["-3.12"], ["-3.13"], ["-3.14"],
    ):
        values = values[1:]

    index = 0
    while index < len(values):
        token = values[index]
        if token == "-u":
            index += 1
            continue
        if token == "-X" and index + 1 < len(values) and values[index + 1] == "utf8":
            index += 2
            continue
        break
    remaining = values[index:]
    if remaining in (["-V"], ["--version"], ["-h"], ["--help"]):
        return True
    if not remaining:
        return False
    if remaining[0] == "-m":
        return len(remaining) >= 2 and _trusted_python_module_execution(
            remaining[1],
            remaining[2:],
        )
    script = remaining[0]
    if script in BOOTSTRAP_SCRIPT_TOKENS:
        return (
            remaining[1:] == []
            and _has_validated_bootstrap_block(complete_command)
        )
    return _trusted_plugin_script(script) is not None


def _trusted_python_module_execution(module: str, arguments: list[str]) -> bool:
    plugin_root = Path(__file__).resolve().parents[1]
    if module == "pip":
        if len(arguments) != 3 or arguments[:2] != ["install", "-r"]:
            return False
        requirements = arguments[2].replace("\\", "/")
        if requirements == "${DASHBOARD_DIR}/requirements.txt" or requirements == "$DASHBOARD_DIR/requirements.txt":
            raw_dashboard = os.environ.get("DASHBOARD_DIR")
            if not raw_dashboard:
                return False
            candidate = Path(raw_dashboard).expanduser() / "requirements.txt"
        else:
            candidate = Path(arguments[2]).expanduser()
            if not candidate.is_absolute():
                return False
        try:
            return candidate.resolve(strict=False) == (plugin_root / "dashboard" / "requirements.txt").resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return False
    if module == "dashboard.server":
        if not arguments or arguments[0] != "--project-root" or len(arguments) < 2:
            return False
        index = 2
        while index < len(arguments):
            token = arguments[index]
            if token == "--no-browser":
                index += 1
                continue
            if token == "--port" and index + 1 < len(arguments) and arguments[index + 1].isdigit():
                index += 2
                continue
            return False
        python_path = os.environ.get("PYTHONPATH", "").split(os.pathsep, 1)[0]
        try:
            return bool(python_path) and Path(python_path).expanduser().resolve(strict=False) == plugin_root.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return False
    return False


def _python_inline_is_read_only(code: str, *, command_arguments: list[str]) -> bool:
    if not code.strip():
        return False
    try:
        code = _expand_inline_code_environment(code)
    except ValueError:
        return False
    try:
        tree = ast.parse(code, mode="exec")
        _ReadOnlyPythonPolicy(command_arguments=command_arguments).visit(tree)
    except (SyntaxError, ValueError, _UnsafeInlineCode):
        return False
    return True


def _expand_inline_code_environment(code: str) -> str:
    """Apply the one Skill interpolation that occurs inside Python source.

    Shell-expanded data can otherwise terminate a Python string and inject a
    new statement after the static AST has been approved.  PROJECT_ROOT is the
    sole legacy interpolation used by the shipped Skills; validate the source
    *after* applying its current value.  Other code-variable expansions fail
    closed and callers should pass data through ``sys.argv`` instead.
    """
    variable_pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2) or ""
        if name != "PROJECT_ROOT":
            raise ValueError("内联代码包含未受信的 Shell 变量展开")
        return os.environ.get(name, "")

    return variable_pattern.sub(replace, code)


def _is_python_interpreter(executable: str) -> bool:
    return executable in PYTHON_INTERPRETERS or bool(
        re.fullmatch(r"(?:python|pypy)3(?:\.\d+)*(?:\.exe)?", executable)
    )


def _command_executable(words: list[_ShellWord]) -> tuple[int, str] | None:
    for index, word in enumerate(words):
        value = word.value
        if not value or value in {"{", "}", "!"}:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", value, re.DOTALL):
            continue
        normalized = value.replace("\\", "/")
        return index, normalized.rsplit("/", 1)[-1].lower()
    return None


def _normalize_shell_continuations(command: str) -> str:
    """Apply POSIX backslash-newline removal before classifying one command."""
    return re.sub(r"\\(?:\r\n|\n|\r)[ \t]*", " ", command)


def _screen_inline_interpreters(command: str) -> tuple[bool, bool, str]:
    """Return ``(found, safe, command_without_safe_code)``.

    Any inline interpreter that cannot be proven to use the read-only Python
    subset makes the complete shell request unsafe.  Removing validated code
    spans prevents a protected *read* inside that code from being mistaken for
    a direct runtime write, while the surrounding shell remains guarded.
    """
    interpreter_hint = re.search(
        r"(?i)(?:python(?:3(?:\.\d+)*)?(?:\.exe)?|pypy3?|py(?:\.exe)?|"
        r"\$\{?CANON_LEDGER_PYTHON\}?|perl|ruby|node(?:js)?|bun|deno|bash|dash|ksh|"
        r"zsh|fish|php|lua|luajit|rscript|powershell|pwsh)",
        command,
    )
    option_hint = re.search(r"(?i)(?:^|\s)(?:-[cer](?:\s|[^\s])|--eval(?:=|\s)|-command(?:\s|[^\s]))", command)
    inline_hint = bool(interpreter_hint and option_hint)
    try:
        commands = _ShellCommandScanner(command).scan()
    except ValueError:
        return (True, False, command) if inline_hint else (False, True, command)

    found = False
    safe_spans: list[tuple[int, int]] = []
    for words in commands:
        executable_spec = _command_executable(words)
        if executable_spec is None:
            continue
        executable_index, executable = executable_spec
        interpreter_token = words[executable_index].value.replace("\\", "/")
        arguments = words[executable_index + 1 :]

        if executable in INTERPRETER_WRAPPERS:
            wrapped_values = [word.value.replace("\\", "/").rsplit("/", 1)[-1].lower() for word in arguments]
            if any(_is_python_interpreter(value) or value in DENIED_INLINE_INTERPRETERS for value in wrapped_values):
                return True, False, command
            continue

        if executable in DENIED_INLINE_INTERPRETERS:
            flags = DENIED_INLINE_INTERPRETERS[executable]
            if any(
                word.value.lower() == flag
                or (flag.startswith("-") and word.value.lower().startswith(flag) and len(word.value) > len(flag))
                for word in arguments
                for flag in flags
            ):
                return True, False, command
            if arguments and [word.value.lower() for word in arguments] not in (
                ["--help"], ["--version"], ["-h"], ["-v"],
            ):
                return True, False, command
            continue

        is_python = interpreter_token in TRUSTED_PYTHON_ENV_TOKENS or _is_python_interpreter(executable)
        if not is_python:
            continue
        code_specs: list[tuple[int, _ShellWord, str, int]] = []
        for index, word in enumerate(arguments):
            if word.value == "-c":
                if index + 1 >= len(arguments):
                    return True, False, command
                code_specs.append((index, arguments[index + 1], arguments[index + 1].value, index + 2))
            elif word.value.startswith("-c"):
                code_specs.append((index, word, word.value[2:], index + 1))
        if not code_specs:
            found = True
            if executable_index or any(word.value in {">", ">>"} for word in words):
                return True, False, command
            if not _python_script_execution_is_trusted(
                arguments,
                executable=executable,
                complete_command=command,
            ):
                return True, False, command
            continue
        found = True
        if executable_index:
            return True, False, command
        if any(word.value in {">", ">>"} for word in words):
            return True, False, command
        if len(code_specs) != 1:
            return True, False, command
        code_index, code_word, code, arguments_start = code_specs[0]
        prefix = [word.value for word in arguments[:code_index]]
        if executable in {"py", "py.exe"} and prefix[:1] in (["-3"], ["-3.10"], ["-3.11"], ["-3.12"], ["-3.13"], ["-3.14"]):
            prefix = prefix[1:]
        if prefix not in (
            [], ["-X", "utf8"], ["-u"], ["-u", "-X", "utf8"], ["-X", "utf8", "-u"],
        ):
            return True, False, command
        if code_word.dynamic:
            return True, False, command
        command_arguments = [word.value for word in arguments[arguments_start:]]
        if not _python_inline_is_read_only(code, command_arguments=command_arguments):
            return True, False, command
        safe_spans.append((code_word.start, code_word.end))

    if not found:
        return False, True, command
    screened = list(command)
    for start, end in safe_spans:
        screened[start:end] = " " * (end - start)
    return True, True, "".join(screened)


def _load_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("Hook 输入为空")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Hook 输入不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Hook 输入顶层必须是对象")
    return payload


def _normalized_path(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.replace("\\", "/")
    try:
        if ":" in raw[:3]:
            raw = PureWindowsPath(str(value)).as_posix()
        else:
            raw = PurePosixPath(raw).as_posix()
    except Exception:
        pass
    return posixpath.normpath(raw).lower()


def _deny(message: str) -> int:
    payload = {
        "permission": "deny",
        "user_message": message,
        "agent_message": message,
        "hookSpecificOutput": {"permissionDecision": "deny"},
        "systemMessage": message,
    }
    print(json.dumps(payload, ensure_ascii=False))
    print(json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}, "systemMessage": message}, ensure_ascii=False), file=sys.stderr)
    return 2


def _allow() -> int:
    print(json.dumps({"permission": "allow"}))
    return 0


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_input") or payload.get("toolInput") or payload.get("input") or {}
    return value if isinstance(value, dict) else {}


def _tool_name(payload: dict[str, Any]) -> str:
    return str(
        payload.get("tool_name")
        or payload.get("toolName")
        or payload.get("tool")
        or payload.get("matcher")
        or ""
    ).strip()


def _file_paths_from_payload(payload: dict[str, Any], tool_input: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for source in (tool_input, payload):
        for key in (
            "file_path",
            "path",
            "filename",
            "filePath",
            "target_path",
            "targetPath",
            "file_paths",
            "paths",
        ):
            value = source.get(key)
            if isinstance(value, (list, tuple)):
                paths.extend(str(item) for item in value if item)
            elif value:
                paths.append(str(value))
    return paths


def _command_from_payload(payload: dict[str, Any], tool_input: dict[str, Any]) -> str:
    for source in (tool_input, payload):
        value = source.get("command")
        if value:
            return str(value)
    return ""


def _base_directory(payload: dict[str, Any], tool_input: dict[str, Any]) -> Path:
    for source in (tool_input, payload):
        for key in ("cwd", "working_directory", "workingDirectory", "workspace_root"):
            value = source.get(key)
            if value:
                return Path(str(value)).expanduser().resolve(strict=False)
    raw = os.environ.get("CURSOR_PROJECT_DIR")
    return Path(raw).expanduser().resolve(strict=False) if raw else Path.cwd().resolve()


def _is_protected_path(path: str, *, base_directory: Path | None = None) -> bool:
    normalized = _normalized_path(path)
    if not normalized:
        return False
    components = {part for part in normalized.split("/") if part not in {"", ".", ".."}}
    if ".story-system" in components:
        return True
    if any(suffix in normalized for suffix in PROTECTED_SUFFIXES):
        return True
    try:
        candidate = Path(str(path)).expanduser()
        if not candidate.is_absolute():
            candidate = (base_directory or Path.cwd()) / candidate
        resolved = candidate.resolve(strict=False).as_posix().lower()
    except (OSError, RuntimeError, ValueError):
        return True
    resolved_components = {
        part for part in resolved.split("/") if part not in {"", ".", ".."}
    }
    return ".story-system" in resolved_components or any(
        suffix in resolved for suffix in PROTECTED_SUFFIXES
    )


def _find_command_is_read_only(arguments: list[str]) -> bool:
    if not arguments:
        return False
    index = 1  # 第一个参数是搜索根。
    predicates_without_value = {"!", "(", ")", "-a", "-o", "-and", "-or", "-print", "-print0"}
    predicates_with_value = {"-maxdepth", "-mindepth", "-type", "-name", "-iname", "-path", "-ipath"}
    while index < len(arguments):
        token = arguments[index]
        if token in predicates_without_value:
            index += 1
            continue
        if token in predicates_with_value and index + 1 < len(arguments):
            index += 2
            continue
        return False
    return True


def _command_is_read_only_protected(
    command: str,
    *,
    validated_interpreters: bool = False,
) -> bool:
    """Allow a closed set of side-effect-free probes, including Skill blocks."""
    if not command.strip():
        return False
    try:
        commands = _ShellCommandScanner(command).scan()
    except ValueError:
        return False
    if not commands:
        return False

    saw_executable = False
    for words in commands:
        executable_spec = _command_executable(words)
        if executable_spec is None:
            # Plain assignments surrounding a validated command substitution.
            if any(
                word.value
                and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word.value, re.DOTALL)
                for word in words
            ):
                return False
            continue
        executable_index, executable = executable_spec
        saw_executable = True
        if executable_index:
            return False
        values = [word.value for word in words[1:]]
        if any(word.value in {">", ">>", "<", "<<"} for word in words):
            return False
        if validated_interpreters and (
            executable in PYTHON_INTERPRETERS
            or _is_python_interpreter(executable)
            or words[0].value.replace("\\", "/") in TRUSTED_PYTHON_ENV_TOKENS
        ):
            continue
        if executable == ":":
            # POSIX no-op；重定向已在上方统一拒绝，${VAR:?} 展开无副作用，
            # 嵌套 $() 会作为独立命令另行校验。
            continue
        if executable in {"test", "["}:
            if executable == "[" and (not values or values[-1] != "]"):
                return False
            continue
        if executable == "find":
            if not _find_command_is_read_only(values):
                return False
            continue
        if executable == "cat":
            if not values or any(value.startswith("-") and value != "--" for value in values):
                return False
            continue
        if executable in {"basename", "dirname", "printf", "pwd", "seq"}:
            continue
        return False
    return saw_executable


def _command_targets_resolved_protected(command: str, *, base_directory: Path) -> bool:
    if SHELL_CONTROL_RE.search(command):
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return True
    if not tokens:
        return False
    executable = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if executable not in {
        "rm", "cp", "mv", "tee", "touch", "truncate", "dd", "install", "unlink",
    }:
        return False
    for token in tokens[1:]:
        if not token or token.startswith("-") or "=" in token:
            continue
        if _is_protected_path(token, base_directory=base_directory):
            return True
    return False


def _command_is_runtime_safe(command: str) -> bool:
    command = _normalize_shell_continuations(command).strip()
    if not command or SHELL_CONTROL_RE.search(command):
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    runtime_indexes = [
        index
        for index, token in enumerate(tokens)
        if token.replace("\\", "/").rsplit("/", 1)[-1].lower()
        in RUNTIME_ENTRYPOINT_NAMES
    ]
    if len(runtime_indexes) != 1:
        return False
    runtime_index = runtime_indexes[0]
    runtime_token = tokens[runtime_index].replace("\\", "/")
    runtime_name = runtime_token.rsplit("/", 1)[-1].lower()
    trusted_absolute = (
        Path(__file__).resolve().parents[1] / "scripts" / runtime_name
    ).as_posix()
    if runtime_token != trusted_absolute:
        env_spec = TRUSTED_RUNTIME_ENV_TOKENS.get(runtime_token)
        if env_spec is None:
            return False
        env_name, suffix, expected_name = env_spec
        if expected_name != runtime_name:
            return False
        raw_root = os.environ.get(env_name)
        if not raw_root:
            return False
        candidate = Path(raw_root).expanduser()
        if suffix:
            candidate /= suffix
        try:
            candidate_runtime = (candidate / expected_name).resolve(strict=False).as_posix()
        except OSError:
            return False
        if candidate_runtime != trusted_absolute:
            return False
    prefix = tokens[:runtime_index]
    if not prefix:
        return False
    interpreter_token = prefix[0].replace("\\", "/")
    interpreter = interpreter_token.rsplit("/", 1)[-1].lower()
    if (
        interpreter_token not in TRUSTED_PYTHON_ENV_TOKENS
        and interpreter not in {"python", "python3", "python.exe", "python3.exe", "py", "py.exe"}
    ):
        return False
    interpreter_args = prefix[1:]
    allowed_interpreter_args = (
        [],
        ["-X", "utf8"],
        ["-u"],
        ["-u", "-X", "utf8"],
        ["-X", "utf8", "-u"],
    )
    if interpreter in {"py", "py.exe"} and interpreter_args[:1] in (["-3"], ["-3.10"], ["-3.11"], ["-3.12"], ["-3.13"]):
        interpreter_args = interpreter_args[1:]
    if interpreter_args not in allowed_interpreter_args:
        return False
    arguments = tokens[runtime_index + 1 :]
    command_arguments: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--project-root":
            if index + 1 >= len(arguments):
                return False
            index += 2
            continue
        if token.startswith("--project-root="):
            index += 1
            continue
        command_arguments.append(token.lower())
        index += 1
    if not command_arguments:
        return False
    if command_arguments[0] == "chapter-commit":
        return True
    return command_arguments[:2] in (["projections", "retry"], ["projections", "replay"])


def _command_mentions_protected_runtime(command: str) -> bool:
    lowered = command.lower().replace("\\", "/")
    non_comment_lines = "\n".join(
        line for line in lowered.splitlines() if not line.lstrip().startswith("#")
    )
    if (
        SHELL_CONTROL_RE.search(non_comment_lines)
        or any(character in non_comment_lines for character in "*?[")
    ) and DANGEROUS_COMMAND_RE.search(non_comment_lines):
        return True
    if ".story" in lowered:
        return True
    if ".canon-ledger" in lowered and any(character in lowered for character in "*?["):
        return True
    if any(suffix in lowered for suffix in PROTECTED_SUFFIXES) or any(
        basename in lowered for basename in PROTECTED_BASENAMES
    ):
        return True
    if COMMIT_BASENAME_RE.search(lowered):
        return True
    return bool(re.search(r"\bcommits(?:/|\\|\s|$)", lowered))


def _looks_like_runtime_bypass(command: str, *, base_directory: Path) -> bool:
    # 规范守卫行是 skill 代码块的首行断链自检（: "${VAR:?...}"），对判定透明；
    # 仅剥离与随包文本逐字一致的行，改写过的变体不享受豁免、照常全文扫描。
    command = _strip_env_guard_lines(command)
    if _command_is_runtime_safe(command):
        return False
    if _command_is_read_only_protected(command):
        return False
    inline_found, inline_safe, screened_command = _screen_inline_interpreters(command)
    if inline_found and not inline_safe:
        return True
    lowered = screened_command.lower().replace("\\", "/")
    if "chapter_commit.py" in lowered:
        return True
    if any(entrypoint in lowered for entrypoint in RUNTIME_ENTRYPOINT_NAMES) and any(
        marker in lowered
        for marker in ("chapter-commit", "projections retry", "projections replay")
    ):
        return True
    if _command_is_read_only_protected(
        screened_command,
        validated_interpreters=inline_found,
    ):
        return False
    if _command_targets_resolved_protected(screened_command, base_directory=base_directory):
        return True
    return _command_mentions_protected_runtime(screened_command)


def main() -> int:
    try:
        payload = _load_input()
    except ValueError as exc:
        return _deny(f"叙典 CanonLedger 运行时保护拒绝了无效输入：{exc}。")
    tool_input = _tool_input(payload)
    tool = _tool_name(payload)
    command = _command_from_payload(payload, tool_input)
    base_directory = _base_directory(payload, tool_input)

    if tool.lower() in {"bash", "shell"} or command:
        if not command:
            return _deny("叙典 CanonLedger 运行时保护收到缺少命令内容的 Shell 请求。")
        if _looks_like_runtime_bypass(command, base_directory=base_directory):
            return _deny(
                "叙典 CanonLedger 已阻止直接写入或绕过 Story System 与读模型的命令。"
                "请改用 canon_ledger.py 的 write-gate、chapter-commit 或 projections retry/replay。"
            )
        return _allow()

    paths = _file_paths_from_payload(payload, tool_input)
    if any(
        _is_protected_path(path, base_directory=base_directory)
        for path in paths
    ):
        return _deny(
            "叙典 CanonLedger 已阻止直接编辑 Story System 或读模型文件。"
            "请通过统一运行时命令写入，以保持提交与投影一致。"
        )
    if not tool or not paths:
        return _deny("叙典 CanonLedger 运行时保护拒绝了字段不完整的工具请求。")
    return _allow()


if __name__ == "__main__":
    raise SystemExit(main())
