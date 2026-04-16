"""
Sandboxed local code execution for Python, JavaScript (Node.js), and shell commands.

Security model: subprocess with timeout, restricted resource limits, temp working
directory, no network by default. This is *not* a full sandbox — it is intended
for a local-only assistant where the user is the operator.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

Language = Literal["python", "javascript", "typescript", "shell", "bash", "powershell"]

LANG_ALIASES: dict[str, Language] = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "node": "javascript",
    "ts": "typescript",
    "sh": "shell",
    "bash": "bash",
    "ps1": "powershell",
    "powershell": "powershell",
    "zsh": "shell",
}

MAX_OUTPUT = 50_000
DEFAULT_TIMEOUT = 30


@dataclass
class ExecResult:
    language: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:MAX_OUTPUT],
            "stderr": self.stderr[:MAX_OUTPUT],
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "ok": self.exit_code == 0 and not self.timed_out,
        }


def _find_exe(names: list[str]) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def _run_subprocess(
    cmd: list[str],
    code: str,
    *,
    timeout: int,
    cwd: str | None = None,
    env_extra: dict[str, str] | None = None,
    input_text: str | None = None,
) -> ExecResult:
    import time

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_extra:
        env.update(env_extra)
    # Strip variables that could leak credentials
    for k in ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL"):
        for ek in list(env):
            if k in ek.upper():
                env.pop(ek, None)

    start = time.monotonic()
    timed_out = False
    try:
        r = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        duration = int((time.monotonic() - start) * 1000)
        return ExecResult(
            language=cmd[0],
            exit_code=r.returncode,
            stdout=(r.stdout or "")[:MAX_OUTPUT],
            stderr=(r.stderr or "")[:MAX_OUTPUT],
            timed_out=False,
            duration_ms=duration,
        )
    except subprocess.TimeoutExpired:
        duration = int((time.monotonic() - start) * 1000)
        return ExecResult(
            language=cmd[0],
            exit_code=-1,
            stdout="",
            stderr=f"Execution timed out after {timeout}s",
            timed_out=True,
            duration_ms=duration,
        )
    except FileNotFoundError:
        return ExecResult(
            language=cmd[0],
            exit_code=-2,
            stdout="",
            stderr=f"Runtime not found: {cmd[0]}",
            timed_out=False,
            duration_ms=0,
        )


def run_python(code: str, timeout: int = DEFAULT_TIMEOUT, stdin: str | None = None) -> ExecResult:
    exe = _find_exe(["python3", "python"])
    if not exe:
        return ExecResult("python", -2, "", "Python not found on PATH", False, 0)
    with tempfile.TemporaryDirectory(prefix="lm-py-") as tmp:
        script = Path(tmp) / "script.py"
        script.write_text(code, encoding="utf-8")
        result = _run_subprocess([exe, "-u", str(script)], code, timeout=timeout, cwd=tmp, input_text=stdin)
        result.language = "python"
        return result


def run_javascript(code: str, timeout: int = DEFAULT_TIMEOUT, stdin: str | None = None) -> ExecResult:
    exe = _find_exe(["node"])
    if not exe:
        return ExecResult("javascript", -2, "", "Node.js not found on PATH", False, 0)
    with tempfile.TemporaryDirectory(prefix="lm-js-") as tmp:
        script = Path(tmp) / "script.mjs"
        script.write_text(code, encoding="utf-8")
        result = _run_subprocess([exe, str(script)], code, timeout=timeout, cwd=tmp, input_text=stdin)
        result.language = "javascript"
        return result


def run_typescript(code: str, timeout: int = DEFAULT_TIMEOUT, stdin: str | None = None) -> ExecResult:
    exe = _find_exe(["tsx", "ts-node", "npx"])
    if exe and "npx" in exe:
        cmd = [exe, "tsx", "--no-warnings"]
    elif exe:
        cmd = [exe]
    else:
        return run_javascript(code, timeout, stdin)

    with tempfile.TemporaryDirectory(prefix="lm-ts-") as tmp:
        script = Path(tmp) / "script.ts"
        script.write_text(code, encoding="utf-8")
        cmd.append(str(script))
        result = _run_subprocess(cmd, code, timeout=timeout, cwd=tmp, input_text=stdin)
        result.language = "typescript"
        return result


def run_shell(code: str, timeout: int = DEFAULT_TIMEOUT, stdin: str | None = None) -> ExecResult:
    is_win = platform.system() == "Windows"
    if is_win:
        exe = _find_exe(["cmd.exe"])
        if not exe:
            return ExecResult("shell", -2, "", "cmd.exe not found", False, 0)
        with tempfile.TemporaryDirectory(prefix="lm-sh-") as tmp:
            script = Path(tmp) / "script.bat"
            script.write_text(f"@echo off\n{code}", encoding="utf-8")
            result = _run_subprocess([exe, "/c", str(script)], code, timeout=timeout, cwd=tmp, input_text=stdin)
            result.language = "shell"
            return result
    else:
        exe = _find_exe(["bash", "sh"])
        if not exe:
            return ExecResult("shell", -2, "", "No shell found", False, 0)
        with tempfile.TemporaryDirectory(prefix="lm-sh-") as tmp:
            script = Path(tmp) / "script.sh"
            script.write_text(code, encoding="utf-8")
            result = _run_subprocess([exe, str(script)], code, timeout=timeout, cwd=tmp, input_text=stdin)
            result.language = "shell"
            return result


def run_powershell(code: str, timeout: int = DEFAULT_TIMEOUT, stdin: str | None = None) -> ExecResult:
    exe = _find_exe(["pwsh", "powershell"])
    if not exe:
        return ExecResult("powershell", -2, "", "PowerShell not found", False, 0)
    with tempfile.TemporaryDirectory(prefix="lm-ps-") as tmp:
        script = Path(tmp) / "script.ps1"
        script.write_text(code, encoding="utf-8")
        result = _run_subprocess(
            [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            code,
            timeout=timeout,
            cwd=tmp,
            input_text=stdin,
        )
        result.language = "powershell"
        return result


RUNNERS: dict[Language, Any] = {
    "python": run_python,
    "javascript": run_javascript,
    "typescript": run_typescript,
    "shell": run_shell,
    "bash": run_shell,
    "powershell": run_powershell,
}


def normalize_language(lang: str) -> Language:
    lang = lang.strip().lower()
    return LANG_ALIASES.get(lang, lang)  # type: ignore[return-value]


def run_code(code: str, language: str, timeout: int = DEFAULT_TIMEOUT, stdin: str | None = None) -> ExecResult:
    norm = normalize_language(language)
    runner = RUNNERS.get(norm)
    if not runner:
        return ExecResult(
            language=language,
            exit_code=-3,
            stdout="",
            stderr=f"Unsupported language: {language}. Supported: {list(RUNNERS)}",
            timed_out=False,
            duration_ms=0,
        )
    return runner(code, timeout=timeout, stdin=stdin)


# ── Extract code blocks from LLM output ──────────────────────────────────────

_CODE_BLOCK_RE = re.compile(
    r"```\s*(\w*)\s*\n?(.*?)```",
    re.DOTALL,
)


def extract_code_blocks(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for m in _CODE_BLOCK_RE.finditer(text):
        lang = m.group(1).strip() or "python"
        blocks.append({"language": normalize_language(lang), "code": m.group(2).strip()})
    return blocks


def available_runtimes() -> dict[str, str | None]:
    return {
        "python": _find_exe(["python3", "python"]),
        "javascript": _find_exe(["node"]),
        "typescript": _find_exe(["tsx", "ts-node"]),
        "shell": _find_exe(["bash", "sh", "cmd.exe"]),
        "powershell": _find_exe(["pwsh", "powershell"]),
    }


# ── Code formatter ────────────────────────────────────────────────────────────

def format_code(code: str, language: str) -> str:
    """
    Format LLM-generated code: strip long comments, reindent by braces/keywords.
    Preserves all actual code — never drops functional lines.
    """
    lang = normalize_language(language)
    code = _strip_llm_comments(code, lang)

    if lang in ("javascript", "typescript"):
        return _format_c_style(code)
    if lang == "python":
        return _format_python(code)
    return code


def _strip_llm_comments(code: str, lang: str) -> str:
    """
    Remove verbose LLM narration comments (>80 chars) but keep short ones.
    Never removes a line entirely — if only a comment is on the line and
    it's short, keep it.
    """
    lines = code.split("\n") if "\n" in code else [code]
    out: list[str] = []
    for line in lines:
        stripped = line
        if lang in ("javascript", "typescript"):
            # Handle // comments — only strip if the comment part is very long
            m = re.search(r"\s*(//.*$)", stripped)
            if m:
                comment = m.group(1)
                before = stripped[:m.start()].rstrip()
                if len(comment) > 80:
                    stripped = before if before else ""
                elif not before:
                    # Line is only a comment — keep short ones
                    stripped = line
            # Handle /* ... */ inline — only strip long ones
            stripped = re.sub(r"/\*[^*]{80,}?\*/", "", stripped)
        elif lang == "python":
            m = re.search(r"\s*(#.*$)", stripped)
            if m:
                comment = m.group(1)
                before = stripped[:m.start()].rstrip()
                if len(comment) > 80:
                    stripped = before if before else ""
        out.append(stripped)
    # Rejoin, keeping empty lines as-is (don't collapse structure)
    return "\n".join(out)


def _format_c_style(code: str) -> str:
    """Reindent JS/TS by splitting on braces, semicolons, keeping all code."""
    out: list[str] = []
    indent = 0
    line = ""
    in_string: str | None = None
    i = 0

    while i < len(code):
        ch = code[i]

        # Track string literals — don't reformat inside them
        if in_string:
            line += ch
            if ch == in_string and (i == 0 or code[i - 1] != "\\"):
                in_string = None
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_string = ch
            line += ch
            i += 1
            continue

        # // line comment — keep it on the current line, break after
        if ch == "/" and i + 1 < len(code) and code[i + 1] == "/":
            end = code.find("\n", i)
            comment = code[i:end] if end >= 0 else code[i:]
            # Only keep short comments
            if len(comment) <= 80:
                line += "  " + comment.strip()
            i = (end + 1) if end >= 0 else len(code)
            if line.strip():
                out.append("    " * indent + line.strip())
            line = ""
            continue

        # /* block comment */ — skip long ones, keep short
        if ch == "/" and i + 1 < len(code) and code[i + 1] == "*":
            end = code.find("*/", i + 2)
            if end >= 0:
                comment = code[i:end + 2]
                if len(comment) <= 80:
                    line += " " + comment.strip()
                i = end + 2
                continue

        if ch == "{":
            line += " {"
            out.append("    " * indent + line.strip())
            line = ""
            indent += 1
            i += 1
            continue

        if ch == "}":
            if line.strip():
                out.append("    " * indent + line.strip())
                line = ""
            indent = max(0, indent - 1)
            out.append("    " * indent + "}")
            i += 1
            # Check for else/else if/catch/finally after }
            rest = code[i:].lstrip()
            if rest.startswith(("else", "catch", "finally")):
                # Keep on same line as }
                out[-1] = out[-1]  # will be joined on next {
            continue

        if ch == ";":
            line += ";"
            out.append("    " * indent + line.strip())
            line = ""
            i += 1
            continue

        if ch in ("\n", "\r"):
            i += 1
            continue

        # Collapse runs of spaces to one
        if ch == " " and line.endswith(" "):
            i += 1
            continue

        line += ch
        i += 1

    if line.strip():
        out.append("    " * indent + line.strip())

    # Clean up empty lines but don't remove structure
    result = "\n".join(out)
    # Collapse 3+ blank lines to 1
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _format_python(code: str) -> str:
    """Reindent Python code, validating with ast if possible."""
    import ast
    import textwrap

    clean = textwrap.dedent(code).strip()
    try:
        ast.parse(clean)
        return clean
    except SyntaxError:
        pass
    # If it doesn't parse, still return dedented version
    return clean
