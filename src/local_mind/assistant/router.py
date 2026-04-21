from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from local_mind.assistant.actions import ActionRegistry, ActionResult

log = logging.getLogger(__name__)


@dataclass
class Intent:
    name: str
    confidence: float
    slots: dict[str, str]
    handler: Callable[["Intent", ActionRegistry], ActionResult]
    original: str


def _strip_fillers(text: str) -> str:
    text = text.lower().strip().rstrip("?.!,")
    prefixes = (
        "jarvis ", "hey jarvis ", "ok jarvis ", "computer ",
        "hey computer ", "please ", "could you ", "can you ",
        "would you ", "i want to ", "i'd like to ", "i want you to ",
        "go ahead and ", "kindly ",
    )
    for _ in range(3):
        for p in prefixes:
            if text.startswith(p):
                text = text[len(p):]
                break
        else:
            break
    return text.strip()


class IntentRouter:
    """Rule-based intent matcher with LLM fallback.

    Patterns are tried in order; the first match wins. If no pattern matches,
    the query is routed to the LLM action.
    """

    def __init__(self, actions: ActionRegistry | None = None, llm_fallback: bool = True) -> None:
        self.actions = actions or ActionRegistry()
        self.llm_fallback = llm_fallback
        self._rules: list[tuple[re.Pattern[str], str, Callable[[re.Match[str], ActionRegistry], ActionResult]]] = []
        self._register_builtins()

    # ── registration ────────────────────────────────────────────────────────
    def add(self, name: str, pattern: str, handler: Callable[[re.Match[str], ActionRegistry], ActionResult]) -> None:
        self._rules.append((re.compile(pattern, re.IGNORECASE), name, handler))

    # ── matching ────────────────────────────────────────────────────────────
    def route(self, text: str) -> tuple[Intent, ActionResult]:
        raw = text
        norm = _strip_fillers(text)
        for pat, name, handler in self._rules:
            m = pat.search(norm)
            if m:
                intent = Intent(
                    name=name,
                    confidence=0.9,
                    slots={k: v for k, v in (m.groupdict() or {}).items() if v},
                    handler=lambda i, a, h=handler, mm=m: h(mm, a),
                    original=raw,
                )
                try:
                    result = handler(m, self.actions)
                except Exception as e:
                    log.exception("Handler %s failed", name)
                    result = ActionResult(False, f"Something went wrong handling that: {e}")
                return intent, result
        intent = Intent(
            name="llm_fallback" if self.llm_fallback else "unknown",
            confidence=0.3,
            slots={"query": raw},
            handler=lambda i, a: a.ask_llm(i.original),
            original=raw,
        )
        if self.llm_fallback:
            return intent, self.actions.ask_llm(raw)
        return intent, ActionResult(False, f"I don't know how to do that yet.")

    # ── built-in rules ──────────────────────────────────────────────────────
    def _register_builtins(self) -> None:
        R = self.add

        # Document creation — try FIRST so 'create a document called open google'
        # doesn't get hijacked by the open rule.
        R("write_document",
          r"^(?:write|draft|compose|generate|author)\s+(?:me\s+)?(?:a\s+|an\s+)?"
          r"(?P<kind>word\s+document|word\s+doc|word|text\s+file|markdown(?:\s+file)?|pdf|document|doc|note|report|essay|article)?"
          r"\s*(?:about|on|explaining|describing|regarding|covering)\s+(?P<topic>.+?)"
          r"(?:\s+(?:titled|named|called)\s+(?P<name>.+))?$",
          lambda m, a: a.write_document(
              m.group("topic"),
              fmt=_doc_fmt_from_word(m.group("kind")),
              name=m.group("name"),
          ))

        R("create_document",
          r"^(?:create|make|new|start)\s+(?:me\s+)?(?:a\s+|an\s+)?"
          r"(?:new\s+)?"
          r"(?P<kind>word\s+document|word\s+doc|word(?:\s+file)?|text\s+file|markdown(?:\s+file)?|pdf(?:\s+file)?|document|doc|note|file)"
          r"(?:\s+(?:titled|named|called)\s+(?P<name>.+))?$",
          lambda m, a: a.create_document(
              m.group("name") or "Untitled",
              fmt=_doc_fmt_from_word(m.group("kind")),
          ))

        # Site-scoped search — must come BEFORE open_target and search_web so
        # 'find cats on youtube' routes correctly.
        R("search_on_site",
          r"^(?:watch|find|show|search|look up|play)\s+(?:me\s+)?(?P<q>.+?)"
          r"\s+on\s+(?P<site>youtube|yt|github|gh|stack\s*overflow|so|reddit|twitter|x|wikipedia|wiki|amazon|google|maps|google\s+maps|npm|pypi)$",
          lambda m, a: a.search_on_site(m.group("q"), m.group("site")))

        R("refresh_apps",
          r"^(?:refresh|rescan|reindex|rebuild|scan(?:\s+for)?(?:\s+new)?)\s+apps?$",
          lambda m, a: a.refresh_apps())

        # Unified open: URL / domain / site alias / installed app / fuzzy
        R("open_target",
          r"^(?:open|launch|start|run|fire\s*up|go\s*to|browse\s*to|visit|navigate\s*to|pull\s*up|load)\s+"
          r"(?:the\s+)?(?P<target>.+?)"
          r"(?:\s+(?:app|application|program|website|site|page))?$",
          lambda m, a: a.open_target(m.group("target")))

        R("close_app",
          r"^(?:close|quit|kill|terminate|exit)\s+(?P<app>.+?)(?:\s+(?:app|application|window|process))?$",
          lambda m, a: a.close_app(m.group("app")))

        R("list_running",
          r"^(?:what'?s? running|list (?:running )?(?:apps|programs|processes)|what apps are open)$",
          lambda m, a: a.list_running())

        R("volume_set",
          r"^(?:set\s+)?volume\s+(?:to\s+)?(?P<pct>\d+)(?:\s*%|\s*percent)?$",
          lambda m, a: _volume_set(m.group("pct"), a))

        R("volume_dir",
          r"^(?:turn\s+|crank\s+)?(?:volume\s+)?(?P<dir>up|down|louder|quieter|lower|higher|raise|reduce)(?:\s+(?:volume|sound|audio))?$",
          lambda m, a: a.volume(_norm_vol(m.group("dir"))))

        R("mute",
          r"^(?:mute|unmute|silence|toggle mute|shut up)\b",
          lambda m, a: a.volume("mute"))

        R("media",
          r"^(?P<cmd>play|pause|resume|stop|next(?:\s+track|\s+song)?|previous(?:\s+track|\s+song)?|skip)\b",
          lambda m, a: a.media(_norm_media(m.group("cmd"))))

        R("brightness_set",
          r"^(?:set\s+)?brightness\s+(?:to\s+)?(?P<pct>\d+)(?:\s*%|\s*percent)?$",
          lambda m, a: a.brightness(m.group("pct")))

        R("brightness_dir",
          r"^(?:make\s+(?:it|screen)\s+)?(?P<dir>brighter|dimmer|darker|lighter|brightness\s+up|brightness\s+down)$",
          lambda m, a: a.brightness(_norm_bright(m.group("dir"))))

        R("time",
          r"^(?:what(?:'?s| is) the time|what time is it|tell me the time|current time)$",
          lambda m, a: a.time_now())
        R("date",
          r"^(?:what(?:'?s| is) (?:the|today'?s) date|what day is it|tell me the date)$",
          lambda m, a: a.date_now())
        R("battery",
          r"^(?:battery(?:\s+status|\s+level)?|how(?:'?s| is) (?:the )?battery)$",
          lambda m, a: a.battery())
        R("system_info",
          r"^(?:system (?:info|status)|how(?:'?s| is) (?:the )?(?:system|computer|cpu|memory))$",
          lambda m, a: a.system_info())

        R("search_web",
          r"^(?:search(?:\s+(?:the\s+)?(?:web|google|internet|online))?\s+for|google(?:\s+for)?|look up|find|search)\s+(?P<q>.+)$",
          lambda m, a: a.search_web(m.group("q")))
        R("learn_url",
          r"^(?:learn|remember|memorize)(?:\s+from)?\s+(?P<url>https?://\S+)$",
          lambda m, a: a.learn_url(m.group("url")))

        R("type_text",
          r"^(?:type|write)\s+(?P<text>.+)$",
          lambda m, a: a.type_text(m.group("text")))
        R("press_keys",
          r"^press\s+(?P<combo>.+)$",
          lambda m, a: a.press_keys(m.group("combo")))

        R("screenshot",
          r"^(?:take (?:a )?screenshot|screenshot|capture (?:the )?screen)$",
          lambda m, a: a.screenshot())

        R("lock",
          r"^(?:lock (?:the )?(?:computer|workstation|screen|pc)|lock it)$",
          lambda m, a: a.lock())
        R("sleep",
          r"^(?:sleep (?:the )?(?:computer|pc|system)|go to sleep|sleep mode)$",
          lambda m, a: a.sleep_system())
        R("shutdown",
          r"^(?:shut\s*down|power off|turn off (?:the )?computer)(?:\s+in\s+(?P<delay>\d+)\s+seconds?)?$",
          lambda m, a: a.shutdown(int(m.group("delay") or 10)))
        R("cancel_shutdown",
          r"^(?:cancel|abort)\s+(?:shutdown|shut\s*down)$",
          lambda m, a: a.cancel_shutdown())
        R("reboot",
          r"^(?:reboot|restart)(?:\s+(?:the\s+)?(?:computer|system|pc))?$",
          lambda m, a: a.reboot())


# ── small normalizers ───────────────────────────────────────────────────────
def _norm_vol(word: str) -> str:
    word = word.lower()
    if word in {"up", "louder", "raise", "higher"}:
        return "up"
    return "down"


def _norm_media(word: str) -> str:
    word = word.lower().split()[0]
    if word in {"resume"}:
        return "play"
    if word == "skip":
        return "next"
    return word


def _norm_bright(word: str) -> str:
    word = word.lower()
    if "brighter" in word or "lighter" in word or "up" in word:
        return "up"
    return "down"


def _doc_fmt_from_word(word: str | None) -> str:
    if not word:
        return "auto"
    w = word.lower().strip()
    if "pdf" in w:
        return "pdf"
    if "markdown" in w or w == "md":
        return "md"
    if "word" in w or w == "doc" or w == "docx":
        return "docx"
    if "text" in w or w == "txt" or w == "note":
        return "txt"
    return "auto"


def _volume_set(pct: str, actions: ActionRegistry) -> ActionResult:
    try:
        target = max(0, min(100, int(pct)))
    except ValueError:
        return ActionResult(False, f"'{pct}' isn't a volume percentage.")
    steps = 50
    actions.volume("mute")
    actions.volume("down", times=steps)
    presses = round(target / 2)
    actions.volume("up", times=presses)
    return ActionResult(True, f"Volume set near {target} percent.", data={"percent": target})
