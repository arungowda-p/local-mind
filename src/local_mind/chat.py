from __future__ import annotations

import re
from typing import Any, Generator

from local_mind.code_exec import extract_code_blocks, format_code, run_code
from local_mind.config import settings
from local_mind.decision import decision_engine
from local_mind.knowledge import knowledge
from local_mind.models import model_manager
from local_mind.web_crawl import crawl_and_learn

WEB_CRAWL_CONF_THRESHOLD = 0.45


_DEGRADE_STOP = (
    "\n\n[Generation stopped — output quality degraded. "
    "This model is too small for this task. Try a larger model "
    "(Phi-3-mini or Qwen2.5-1.5B), or simplify the prompt.]"
)
_REPEAT_STOP = (
    "\n\n[Generation stopped — repetition detected. "
    "Try a more specific prompt, or teach me the content via Learn URL.]"
)


class _OutputGuard:
    """
    Monitors streaming output for two failure modes:
      1. Repetition loops (same phrase appears 2+ times)
      2. Gibberish / degradation (low word-quality ratio)
    """

    def __init__(
        self,
        window: int = 200,
        min_pattern: int = 15,
        max_repeats: int = 2,
        gibberish_window: int = 120,
        gibberish_threshold: float = 0.35,
    ):
        self._buf: list[str] = []
        self._window = window
        self._min_pattern = min_pattern
        self._max_repeats = max_repeats
        self._gib_window = gibberish_window
        self._gib_threshold = gibberish_threshold

    def feed(self, token: str) -> str | None:
        """
        Append a token. Returns None if OK, or a stop-reason string
        if generation should halt.
        """
        self._buf.append(token)
        text = "".join(self._buf)

        if self._check_repetition(text):
            return "repetition"
        if self._check_gibberish(text):
            return "gibberish"
        return None

    def _check_repetition(self, text: str) -> bool:
        if len(text) < self._min_pattern * (self._max_repeats + 1):
            return False
        tail = text[-self._window:]
        for plen in range(self._min_pattern, len(tail) // 3 + 1):
            pattern = tail[-plen:]
            count = 0
            pos = len(tail) - plen
            while pos >= 0:
                chunk = tail[max(0, pos - plen):pos]
                if chunk.lower().strip() == pattern.lower().strip():
                    count += 1
                    if count >= self._max_repeats:
                        return True
                    pos -= plen
                else:
                    break
        return False

    def _check_gibberish(self, text: str) -> bool:
        if len(text) < self._gib_window:
            return False
        tail = text[-self._gib_window:]
        words = tail.split()
        if len(words) < 10:
            return False
        # Heuristics for gibberish:
        # 1. Very long "words" (model merging tokens without spaces)
        long_junk = sum(1 for w in words if len(w) > 25)
        # 2. Words with no vowels (not real English/code)
        vowels = set("aeiouAEIOU")
        no_vowel = sum(1 for w in words if len(w) > 3 and not any(c in vowels for c in w))
        # 3. Excessive punctuation-only tokens
        punct_only = sum(1 for w in words if all(not c.isalnum() for c in w) and len(w) > 2)

        bad_ratio = (long_junk + no_vowel + punct_only) / len(words)
        return bad_ratio >= self._gib_threshold

SYSTEM_PROMPT = (
    "You are LocalMind, a helpful, concise AI assistant. "
    "When context from a knowledge base is provided, use it to give accurate, "
    "grounded answers. If the context does not cover the question, say so honestly."
)

LOW_CONFIDENCE_ADDENDUM = (
    "\n\nNote: the knowledge base has limited relevant context for this query. "
    "Be transparent with the user if your answer relies on general knowledge "
    "rather than retrieved documents."
)

SUMMARIZE_PROMPT = (
    "You are LocalMind, a helpful assistant. "
    "Summarize the following context clearly and concisely, keeping the key points."
)

CODE_PROMPT = (
    "You are a code assistant. You write code that solves EXACTLY what the user asked, "
    "nothing else.\n\n"
    "Execution environment (your code runs here when the user clicks Run):\n"
    "- JavaScript / TypeScript → Node.js subprocess. NO browser globals: do NOT use "
    "`prompt`, `alert`, `confirm`, `document`, `window`, `localStorage`, `fetch` to "
    "the DOM. For input, either hardcode example values OR read from `process.stdin`.\n"
    "- Python → python3 subprocess. `input()` works only if the user pipes stdin; "
    "prefer hardcoded example values so the code runs out of the box.\n"
    "- Shell / PowerShell → runs as a script in a temp dir; no interactive prompts.\n\n"
    "Hard rules:\n"
    "1. Output exactly ONE fenced code block with the correct language tag, then ONE "
    "short sentence (under 20 words) describing what it does.\n"
    "2. SOLVE THE USER'S REQUEST. Do not substitute a different problem (e.g. if asked "
    "for palindrome, write a palindrome — not a sum function).\n"
    "3. Make the code immediately runnable: define your function AND call it once with "
    "a hardcoded example, then print the result. Example pattern:\n"
    "   ```javascript\n"
    "   function isPalindrome(s) { /* ... */ }\n"
    "   console.log(isPalindrome('racecar'));\n"
    "   ```\n"
    "4. Each statement on its own line, properly indented. Every `{` opens a block; "
    "every `}` closes one.\n"
    "5. The code must be complete, syntactically valid, and runnable with no edits.\n"
    "6. Comments may be at most 8 words. Do not apologise or describe rules in comments.\n"
    "7. Never invent variables you did not declare. Never call a function with the "
    "wrong number of arguments. Do not interpolate the same variable twice in a "
    "string when you mean two different things.\n"
    "8. Do not repeat the user's question. Do not include phrases like "
    "'here is an example of incorrect formatting'.\n"
)


def _build_rag_context(query: str) -> tuple[str, list[dict[str, Any]]]:
    results = knowledge.query(query)
    if not results:
        return "", []
    lines: list[str] = ["### Retrieved context"]
    for i, r in enumerate(results, 1):
        src = r["meta"].get("url") or r["meta"].get("source", "")
        lines.append(f"[{i}] ({src})\n{r['text']}")
    return "\n\n".join(lines), results


def _augment_with_web(
    query: str,
    decision: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    If KB confidence is too low, crawl the web for fresh sources and
    ingest them. Returns a summary of crawled sources, or None if skipped.
    """
    if not decision:
        return None
    action = decision.get("action", {}).get("action", "")
    if action in ("write_code", "run_code", "learn_url", "clarify"):
        return None
    intent = decision.get("intent", {}).get("intent", "")
    if intent == "chitchat":
        return None
    confidence = decision.get("confidence", {}) or {}
    conf_score = float(confidence.get("confidence", 0.0))
    if confidence.get("has_context") and conf_score >= WEB_CRAWL_CONF_THRESHOLD:
        return None
    summary = crawl_and_learn(query)
    if summary.get("status") not in ("learned", "fetched"):
        return summary
    if summary.get("status") == "learned":
        # Refresh confidence so downstream prompt + UI reflect the new context
        try:
            updated_conf = decision_engine.score_confidence(query)
            decision["confidence"] = updated_conf
        except Exception:
            pass
    return summary


def _assemble_messages(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    use_rag: bool = True,
    decision: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    action = (decision or {}).get("action", {}).get("action", "rag_chat")
    conf = (decision or {}).get("confidence", {}).get("confidence", 1.0)

    ctx_text = ""
    if use_rag:
        ctx_text, _ = _build_rag_context(user_message)

    if action == "summarize":
        system = SUMMARIZE_PROMPT
        if ctx_text:
            system += "\n\n" + ctx_text
    elif action in ("write_code", "run_code"):
        system = CODE_PROMPT
        if ctx_text:
            system += (
                "\n\nUse the following reference material to write better code. "
                "If it contains code examples, follow their patterns.\n\n"
                + ctx_text
            )
    else:
        system = SYSTEM_PROMPT
        if ctx_text:
            system += "\n\n" + ctx_text
            if conf < 0.45:
                system += LOW_CONFIDENCE_ADDENDUM

    msgs: list[dict[str, str]] = [{"role": "system", "content": system}]
    if history:
        msgs.extend(history)
    msgs.append({"role": "user", "content": user_message})
    return msgs


def _sanitize_output(text: str) -> str:
    """
    Post-hoc cleanup: detects repetition loops and gibberish in completed output.
    Truncates to the last good section.
    """
    # --- Repetition check ---
    min_pattern, max_repeats = 15, 2
    if len(text) >= min_pattern * (max_repeats + 1):
        for plen in range(min_pattern, len(text) // 3 + 1):
            for start in range(0, len(text) - plen * (max_repeats + 1) + 1):
                pattern = text[start:start + plen]
                count = 0
                pos = start + plen
                while pos + plen <= len(text):
                    candidate = text[pos:pos + plen]
                    if candidate.lower().strip() == pattern.lower().strip():
                        count += 1
                        pos += plen
                    else:
                        break
                if count >= max_repeats:
                    return text[:start + plen].rstrip() + _REPEAT_STOP

    # --- Gibberish check (sliding window) ---
    words = text.split()
    if len(words) > 30:
        vowels = set("aeiouAEIOU")
        window = 20
        for i in range(len(words) - window):
            chunk = words[i:i + window]
            long_junk = sum(1 for w in chunk if len(w) > 25)
            no_vowel = sum(1 for w in chunk if len(w) > 3 and not any(c in vowels for c in w))
            bad = (long_junk + no_vowel) / window
            if bad >= 0.35:
                good_part = " ".join(words[:i]).rstrip()
                return good_part + _DEGRADE_STOP

    return text


_FENCE_RE = re.compile(r"```\s*(\w*)\s*\n?([\s\S]*?)```")
_OPEN_FENCE_RE = re.compile(r"```\s*(\w*)\s*\n([\s\S]*)$")

_TRUNCATION_NOTE = (
    "\n\n_The model stopped before finishing — likely too small for this task. "
    "Try a larger model (Phi-3-mini or Qwen2.5-1.5B) or rephrase the request._"
)


def _repair_truncated_code(text: str) -> str:
    """
    Detect a code block that was opened (``` <lang>) but never closed (no
    matching ```), or that obviously stops mid-statement. Append a closing
    fence and a short truncation note so the UI can still render the partial
    code AND signal the user that something went wrong.
    """
    if not text:
        return text
    # Are there an odd number of fences? Then the last block is unclosed.
    fence_count = text.count("```")
    if fence_count % 2 == 1:
        # Find the last opened fence and its body
        m = _OPEN_FENCE_RE.search(text)
        body = (m.group(2) if m else "").rstrip()
        # If the body is empty or ends with an opening brace / colon / comma
        # it's truncated mid-statement → close cleanly + warn.
        looks_truncated = (
            not body
            or body.endswith(("{", "(", "[", ",", ":"))
            or body.count("{") > body.count("}")
        )
        text = text.rstrip() + "\n```"
        if looks_truncated:
            text += _TRUNCATION_NOTE
        return text

    # Even number of fences: still check if the LAST block looks unfinished.
    matches = list(_FENCE_RE.finditer(text))
    if matches:
        body = (matches[-1].group(2) or "").rstrip()
        if body and body.count("{") > body.count("}"):
            return text + _TRUNCATION_NOTE
    return text


def _format_code_blocks(text: str) -> str:
    """Run every fenced code block in `text` through the local formatter."""

    def _replace(match: "re.Match[str]") -> str:
        lang = (match.group(1) or "").strip() or "text"
        body = match.group(2) or ""
        try:
            formatted = format_code(body, lang)
        except Exception:
            return match.group(0)
        if not formatted.strip():
            return match.group(0)
        return f"```{lang}\n{formatted.strip()}\n```"

    return _FENCE_RE.sub(_replace, text)


def _extract_url(text: str) -> str | None:
    m = re.search(r"https?://\S+", text)
    return m.group(0).rstrip(".,;:!?)\"'") if m else None


def smart_chat(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    use_rag: bool = True,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    repeat_penalty: float | None = None,
) -> dict[str, Any]:
    """
    Decision-aware chat: runs the 3-stage pipeline, then routes to the
    right handler. Returns the assistant reply + decision metadata.
    """
    decision = decision_engine.decide(user_message)
    action = decision["action"]["action"]

    # Auto-learn URL if the engine says so
    if action == "learn_url":
        url = _extract_url(user_message)
        if url:
            result = knowledge.learn_url(url)
            learn_msg = (
                f"I've learned from that URL ({result.get('chunks', 0)} chunks stored). "
                "You can now ask me questions about it."
                if result.get("status") == "learned"
                else f"I tried to learn from that URL but: {result.get('reason', 'unknown issue')}."
            )
            return {
                "role": "assistant",
                "content": learn_msg,
                "decision": decision,
            }

    # Run code — execute code blocks from the user message directly
    if action == "run_code":
        blocks = extract_code_blocks(user_message)
        if blocks:
            results = []
            for block in blocks:
                r = run_code(block["code"], block["language"])
                results.append(r.to_dict())
            parts = []
            for i, r in enumerate(results):
                header = f"**Block {i + 1}** (`{r['language']}`) — " + (
                    "OK" if r["ok"] else f"exit {r['exit_code']}"
                )
                parts.append(header)
                if r["stdout"]:
                    parts.append(f"```\n{r['stdout'].rstrip()}\n```")
                if r["stderr"]:
                    parts.append(f"stderr:\n```\n{r['stderr'].rstrip()}\n```")
            return {
                "role": "assistant",
                "content": "\n\n".join(parts),
                "decision": decision,
                "code_results": results,
            }

    # Clarify — ask user for more info without hitting the LLM
    if action == "clarify" and decision["intent"]["confidence"] > 0.6:
        return {
            "role": "assistant",
            "content": "Could you give me a bit more detail? I want to make sure I understand what you're looking for.",
            "decision": decision,
        }

    web_summary = _augment_with_web(user_message, decision) if use_rag else None

    is_code = action in ("write_code", "run_code")
    effective_temp = 0.2 if is_code else temperature
    effective_max = (
        max_tokens
        if max_tokens is not None
        else (settings.llm_code_max_tokens if is_code else None)
    )
    effective_repeat = (
        repeat_penalty
        if repeat_penalty is not None
        else (settings.llm_code_repeat_penalty if is_code else None)
    )
    msgs = _assemble_messages(user_message, history, use_rag, decision)
    resp = model_manager.complete_chat(
        msgs,
        max_tokens=effective_max,
        temperature=effective_temp,
        stream=False,
        repeat_penalty=effective_repeat,
        frequency_penalty=0.0 if is_code else None,
        presence_penalty=0.0 if is_code else None,
    )
    text = resp["choices"][0]["message"]["content"]
    text = _sanitize_output(text)
    text = _repair_truncated_code(text)
    text = _format_code_blocks(text)
    return {
        "role": "assistant",
        "content": text,
        "decision": decision,
        "web_sources": web_summary,
    }


def smart_chat_stream(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    use_rag: bool = True,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    repeat_penalty: float | None = None,
) -> Generator[dict[str, Any], None, None]:
    """
    Streaming version: yields decision metadata first, then token dicts.
    """
    decision = decision_engine.decide(user_message)
    action = decision["action"]["action"]

    yield {"type": "decision", "decision": decision}

    # Auto-learn
    if action == "learn_url":
        url = _extract_url(user_message)
        if url:
            result = knowledge.learn_url(url)
            msg = (
                f"I've learned from that URL ({result.get('chunks', 0)} chunks stored). "
                "You can now ask me questions about it."
                if result.get("status") == "learned"
                else f"I tried to learn from that URL but: {result.get('reason', 'unknown issue')}."
            )
            yield {"type": "token", "token": msg}
            yield {"type": "done"}
            return

    # Run code blocks from user message
    if action == "run_code":
        blocks = extract_code_blocks(user_message)
        if blocks:
            results = []
            for i, block in enumerate(blocks):
                r = run_code(block["code"], block["language"])
                rd = r.to_dict()
                results.append(rd)
                header = f"**Block {i + 1}** (`{rd['language']}`) — " + (
                    "OK" if rd["ok"] else f"exit {rd['exit_code']}"
                )
                yield {"type": "token", "token": header + "\n\n"}
                if rd["stdout"]:
                    yield {"type": "token", "token": f"```\n{rd['stdout'].rstrip()}\n```\n\n"}
                if rd["stderr"]:
                    yield {"type": "token", "token": f"stderr:\n```\n{rd['stderr'].rstrip()}\n```\n\n"}
            yield {"type": "code_results", "results": results}
            yield {"type": "done"}
            return

    if action == "clarify" and decision["intent"]["confidence"] > 0.6:
        yield {
            "type": "token",
            "token": "Could you give me a bit more detail? I want to make sure I understand what you're looking for.",
        }
        yield {"type": "done"}
        return

    if use_rag:
        web_summary = _augment_with_web(user_message, decision)
        if web_summary and web_summary.get("status") == "learned":
            yield {"type": "decision", "decision": decision}
            yield {"type": "web_sources", "web_sources": web_summary}
        elif web_summary:
            yield {"type": "web_sources", "web_sources": web_summary}

    is_code = action in ("write_code", "run_code")
    effective_temp = 0.2 if is_code else temperature
    effective_max = (
        max_tokens
        if max_tokens is not None
        else (settings.llm_code_max_tokens if is_code else None)
    )
    effective_repeat = (
        repeat_penalty
        if repeat_penalty is not None
        else (settings.llm_code_repeat_penalty if is_code else None)
    )
    msgs = _assemble_messages(user_message, history, use_rag, decision)
    stream = model_manager.complete_chat(
        msgs,
        max_tokens=effective_max,
        temperature=effective_temp,
        stream=True,
        repeat_penalty=effective_repeat,
        frequency_penalty=0.0 if is_code else None,
        presence_penalty=0.0 if is_code else None,
    )
    guard = _OutputGuard()
    raw_chunks: list[str] = []
    for chunk in stream:
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        token = delta.get("content")
        if token:
            reason = guard.feed(token)
            if reason:
                msg = _REPEAT_STOP if reason == "repetition" else _DEGRADE_STOP
                yield {"type": "token", "token": msg}
                break
            raw_chunks.append(token)
            yield {"type": "token", "token": token}

    full_text = "".join(raw_chunks)
    repaired = _repair_truncated_code(full_text)
    formatted = _format_code_blocks(repaired)
    if formatted != full_text:
        yield {"type": "rewrite", "content": formatted}
    yield {"type": "done"}


# ── Legacy non-decision wrappers (still available) ────────────────────────────

def chat(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    use_rag: bool = True,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    repeat_penalty: float | None = None,
) -> dict[str, Any]:
    msgs = _assemble_messages(user_message, history, use_rag)
    resp = model_manager.complete_chat(
        msgs,
        max_tokens=max_tokens,
        temperature=temperature,
        stream=False,
        repeat_penalty=repeat_penalty,
    )
    text = resp["choices"][0]["message"]["content"]
    text = _sanitize_output(text)
    return {"role": "assistant", "content": text}


def chat_stream(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    use_rag: bool = True,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    repeat_penalty: float | None = None,
) -> Generator[str, None, None]:
    msgs = _assemble_messages(user_message, history, use_rag)
    stream = model_manager.complete_chat(
        msgs,
        max_tokens=max_tokens,
        temperature=temperature,
        stream=True,
        repeat_penalty=repeat_penalty,
    )
    guard = _OutputGuard()
    for chunk in stream:
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        token = delta.get("content")
        if token:
            reason = guard.feed(token)
            if reason:
                msg = _REPEAT_STOP if reason == "repetition" else _DEGRADE_STOP
                yield msg
                break
            yield token
