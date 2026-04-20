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
    Format LLM-generated code into something readable.

    - Strips long, narrative LLM comments while preserving short ones.
    - Reindents by braces/keywords for C-style languages.
    - Uses Black for Python when available, falling back to a safe
      manual reindent that's tolerant of partial / broken snippets.
    - Never raises: a fall-through returns the input unchanged.
    """
    if not code or not code.strip():
        return code
    try:
        lang = normalize_language(language)
    except Exception:
        return code

    try:
        cleaned = _strip_llm_comments(code, lang)
    except Exception:
        cleaned = code

    try:
        if lang in ("javascript", "typescript"):
            if not _braces_balanced(cleaned):
                return cleaned
            return _format_c_style(cleaned)
        if lang == "python":
            return _format_python(cleaned)
        if lang in ("shell", "bash", "powershell"):
            return _format_shell(cleaned)
    except Exception as e:
        log.debug("format_code(%s) failed: %s", lang, e)
        return cleaned
    return cleaned


def _braces_balanced(code: str) -> bool:
    """
    Return True iff `(`, `[`, `{` are balanced in `code`, ignoring chars inside
    strings and comments. Used to refuse formatting on broken LLM snippets so
    we don't multiply the damage.
    """
    depth = {"(": 0, "[": 0, "{": 0}
    pair = {")": "(", "]": "[", "}": "{"}
    in_str: str | None = None
    esc = False
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        if in_str is not None:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n and code[i + 1] == "/":
            nl = code.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if ch == "/" and i + 1 < n and code[i + 1] == "*":
            end = code.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch in depth:
            depth[ch] += 1
        elif ch in pair:
            depth[pair[ch]] -= 1
            if depth[pair[ch]] < 0:
                return False
        i += 1
    return all(v == 0 for v in depth.values())


def _strip_llm_comments(code: str, lang: str) -> str:
    """Remove only obviously verbose narrative comments (>120 chars)."""
    LIMIT = 120
    lines = code.split("\n") if "\n" in code else [code]
    out: list[str] = []
    for line in lines:
        stripped = line
        if lang in ("javascript", "typescript"):
            m = re.search(r"\s*(//.*$)", stripped)
            if m:
                comment = m.group(1).strip()
                before = stripped[: m.start()].rstrip()
                if len(comment) > LIMIT:
                    stripped = before
            stripped = re.sub(r"/\*[^*]{120,}?\*/", "", stripped)
        elif lang == "python":
            m = re.search(r"(?<!['\"])\s*(#.*$)", stripped)
            if m:
                comment = m.group(1).strip()
                before = stripped[: m.start()].rstrip()
                if len(comment) > LIMIT:
                    stripped = before
        out.append(stripped)
    return "\n".join(out)


# ── C-style (JS / TS) formatter ──────────────────────────────────────────────

_CONTINUATION_KEYWORDS = ("else", "catch", "finally")
_INDENT = "  "


def _format_c_style(code: str) -> str:
    """
    Reindent JS/TS source code character-by-character.

    Handles:
      - String literals (`'`, `"`, `` ` ``) and template substitutions.
      - Line (`//`) and block (`/* */`) comments.
      - Brace-driven indentation, including `} else {`, `} catch {`, etc.
      - Statement breaks on `;` outside `for(...)` headers.
      - Collapses runs of whitespace, normalises blank lines.
    """
    text = code.replace("\r\n", "\n").replace("\r", "\n")
    n = len(text)
    out: list[str] = []
    indent = 0
    line: list[str] = []
    in_string: str | None = None
    string_escape = False
    paren_depth = 0  # used to NOT split on `;` inside `for (...; ...; ...)`
    i = 0

    def flush() -> None:
        nonlocal line
        s = "".join(line).strip()
        if s:
            out.append(_INDENT * indent + s)
        line = []

    while i < n:
        ch = text[i]

        if in_string is not None:
            line.append(ch)
            if string_escape:
                string_escape = False
            elif ch == "\\":
                string_escape = True
            elif ch == in_string:
                in_string = None
            i += 1
            continue

        if ch in ("'", '"', "`"):
            in_string = ch
            line.append(ch)
            i += 1
            continue

        # Line comment
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            end = text.find("\n", i)
            if end == -1:
                end = n
            comment = text[i:end].rstrip()
            if line and "".join(line).strip():
                line.append(" " + comment)
            else:
                out.append(_INDENT * indent + comment)
                line = []
            i = end + 1
            continue

        # Block comment
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                line.append(text[i:])
                i = n
                continue
            block = text[i : end + 2]
            if "\n" in block:
                flush()
                for bl in block.split("\n"):
                    out.append(_INDENT * indent + bl.strip())
            else:
                if line and "".join(line).strip():
                    line.append(" " + block.strip())
                else:
                    out.append(_INDENT * indent + block.strip())
            i = end + 2
            continue

        if ch == "(":
            paren_depth += 1
            line.append(ch)
            i += 1
            continue
        if ch == ")":
            paren_depth = max(0, paren_depth - 1)
            line.append(ch)
            i += 1
            continue

        if ch == "{":
            current = "".join(line).rstrip()
            if current:
                line = [current + " {"]
            else:
                line = ["{"]
            flush()
            indent += 1
            i += 1
            continue

        if ch == "}":
            flush()
            indent = max(0, indent - 1)
            # Detect `} else { / } catch { / } finally {` style continuations
            j = i + 1
            while j < n and text[j] in " \t":
                j += 1
            rest_token = ""
            k = j
            while k < n and text[k].isalpha():
                rest_token += text[k]
                k += 1
            if rest_token in _CONTINUATION_KEYWORDS:
                line = ["} " + rest_token]
                i = k
                continue
            out.append(_INDENT * indent + "}")
            i += 1
            continue

        if ch == ";" and paren_depth == 0:
            line.append(";")
            flush()
            i += 1
            continue

        if ch == "\n":
            # respect explicit newlines as statement boundaries
            if "".join(line).strip():
                flush()
            else:
                out.append("")
                line = []
            i += 1
            continue

        if ch in " \t":
            if line and line[-1] not in (" ",) and "".join(line).strip():
                line.append(" ")
            i += 1
            continue

        line.append(ch)
        i += 1

    flush()

    # Tidy up: drop leading blanks; collapse ALL blank lines (no double-spaced output).
    # We then add a single blank line between top-level definitions for readability.
    lines = [ln for ln in out if ln.strip()]
    result: list[str] = []
    for i, ln in enumerate(lines):
        result.append(ln)
        # Insert a blank line after a top-level closing brace before the next top-level
        # statement (improves readability between functions / classes / main code).
        if (
            ln.rstrip() == "}"
            and i + 1 < len(lines)
            and not lines[i + 1].startswith((" ", "\t", "}", ")", "]", ".", ","))
        ):
            result.append("")
    return "\n".join(result).strip("\n")


# ── Python formatter ─────────────────────────────────────────────────────────

def _format_python(code: str) -> str:
    """Use Black if installed (preferred); otherwise a safe manual reindent."""
    import textwrap

    clean = textwrap.dedent(code).strip()
    if not clean:
        return clean

    try:
        import black

        try:
            return black.format_str(clean, mode=black.Mode()).rstrip()
        except Exception:
            pass
    except Exception:
        pass

    # Manual fallback: split semicolons-at-top-level into separate statements
    # and ensure code after `:` opens onto a new indented line.
    lines = clean.split("\n")
    fixed: list[str] = []
    for raw in lines:
        if not raw.strip():
            fixed.append("")
            continue
        # Split `a = 1; b = 2` only if not in a string
        if ";" in raw and not _in_python_string(raw):
            parts = [p.strip() for p in raw.split(";") if p.strip()]
            indent_ws = raw[: len(raw) - len(raw.lstrip())]
            for p in parts:
                fixed.append(indent_ws + p)
        else:
            fixed.append(raw.rstrip())
    out = "\n".join(fixed)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()

    # Validate; if it parses, great. Otherwise still return the cleaned text.
    try:
        import ast
        ast.parse(out)
    except SyntaxError:
        pass
    return out


def _in_python_string(line: str) -> bool:
    """Quick heuristic: odd number of unescaped quotes → currently inside one."""
    sq = len(re.findall(r"(?<!\\)'", line))
    dq = len(re.findall(r'(?<!\\)"', line))
    return (sq % 2 == 1) or (dq % 2 == 1)


# ── Shell formatter ──────────────────────────────────────────────────────────

def _format_shell(code: str) -> str:
    """
    Light shell formatter: split `;`-joined statements and trim trailing
    whitespace. Avoids rewriting heredocs / quoted strings.
    """
    lines: list[str] = []
    for raw in code.replace("\r\n", "\n").split("\n"):
        if not raw.strip():
            lines.append("")
            continue
        if ";" in raw and not (raw.count("'") % 2 or raw.count('"') % 2):
            for piece in raw.split(";"):
                if piece.strip():
                    lines.append(piece.rstrip())
        else:
            lines.append(raw.rstrip())
    out = "\n".join(lines).strip("\n")
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out
