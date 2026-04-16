from __future__ import annotations

import re
from typing import Any, Generator

from local_mind.code_exec import extract_code_blocks, run_code
from local_mind.decision import decision_engine
from local_mind.knowledge import knowledge
from local_mind.models import model_manager


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
    "You are a code assistant. You MUST follow these rules exactly:\n"
    "1. Output ONLY a single fenced code block with the correct language tag.\n"
    "2. The code MUST be properly formatted with newlines and indentation.\n"
    "3. Do NOT put the entire function on one line.\n"
    "4. Each statement gets its own line. Each { and } gets its own line.\n"
    "5. Do NOT write long comments. At most a short 5-word comment per line.\n"
    "6. After the code block, write ONE sentence describing what it does.\n"
    "7. Do NOT repeat yourself. Do NOT ramble.\n"
    "8. The code must be complete and runnable as-is.\n\n"
    "Example of correct formatting:\n"
    "```javascript\n"
    "function add(a, b) {\n"
    "    return a + b;\n"
    "}\n"
    "console.log(add(2, 3));\n"
    "```\n"
    "This function adds two numbers and prints the result."
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


def _assemble_messages(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    use_rag: bool = True,
    decision: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    action = (decision or {}).get("action", {}).get("action", "rag_chat")
    conf = (decision or {}).get("confidence", {}).get("confidence", 1.0)

    if action == "summarize":
        ctx_text, _ = _build_rag_context(user_message)
        system = SUMMARIZE_PROMPT
        if ctx_text:
            system += "\n\n" + ctx_text
    elif action in ("write_code", "run_code"):
        system = CODE_PROMPT
        if use_rag:
            ctx_text, _ = _build_rag_context(user_message)
            if ctx_text:
                system += (
                    "\n\nUse the following reference material to write better code. "
                    "If it contains code examples, follow their patterns.\n\n"
                    + ctx_text
                )
    elif action == "direct_chat" or not use_rag:
        system = SYSTEM_PROMPT
    else:
        ctx_text, _ = _build_rag_context(user_message)
        system = SYSTEM_PROMPT
        if ctx_text:
            system += "\n\n" + ctx_text
        if conf < 0.45 and ctx_text:
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

    effective_temp = 0.2 if action in ("write_code", "run_code") else temperature
    msgs = _assemble_messages(user_message, history, use_rag, decision)
    resp = model_manager.complete_chat(
        msgs,
        max_tokens=max_tokens,
        temperature=effective_temp,
        stream=False,
        repeat_penalty=repeat_penalty,
    )
    text = resp["choices"][0]["message"]["content"]
    text = _sanitize_output(text)
    return {"role": "assistant", "content": text, "decision": decision}


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

    effective_temp = 0.2 if action in ("write_code", "run_code") else temperature
    msgs = _assemble_messages(user_message, history, use_rag, decision)
    stream = model_manager.complete_chat(
        msgs,
        max_tokens=max_tokens,
        temperature=effective_temp,
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
                yield {"type": "token", "token": msg}
                break
            yield {"type": "token", "token": token}
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
