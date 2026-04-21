from __future__ import annotations

import logging
import queue
import subprocess
import sys
import threading
from typing import Any

log = logging.getLogger(__name__)


class TextToSpeech:
    """Offline TTS with a small worker thread.

    Uses pyttsx3 (SAPI5 on Windows, NSSS on macOS, espeak on Linux) when
    available; falls back to PowerShell System.Speech on Windows. A single
    background thread serializes utterances so :meth:`say` never blocks the
    caller.
    """

    def __init__(self, rate: int = 185, voice: str | None = None) -> None:
        self._rate = rate
        self._voice = voice
        self._engine: Any = None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._speaking = threading.Event()
        self._stop_flag = threading.Event()
        self._backend = "none"
        self._init_engine()

    # ── init ────────────────────────────────────────────────────────────────
    def _init_engine(self) -> None:
        try:
            import pyttsx3

            eng = pyttsx3.init()
            eng.setProperty("rate", self._rate)
            if self._voice:
                for v in eng.getProperty("voices"):
                    if self._voice.lower() in (getattr(v, "name", "") or "").lower():
                        eng.setProperty("voice", v.id)
                        break
            self._engine = eng
            self._backend = "pyttsx3"
            log.info("TTS backend: pyttsx3 (rate=%d)", self._rate)
        except Exception as e:
            log.warning("pyttsx3 init failed (%s); falling back", e)
            if sys.platform == "win32":
                self._backend = "sapi-powershell"
                log.info("TTS backend: SAPI via PowerShell")
            else:
                self._backend = "none"
                log.warning("No TTS backend available — responses will be text only.")

    # ── public API ─────────────────────────────────────────────────────────
    def available(self) -> bool:
        return self._backend != "none"

    def list_voices(self) -> list[dict[str, str]]:
        if self._backend != "pyttsx3" or self._engine is None:
            return []
        out = []
        for v in self._engine.getProperty("voices"):
            out.append({
                "id": getattr(v, "id", ""),
                "name": getattr(v, "name", ""),
                "lang": ",".join(getattr(v, "languages", []) or []) or "",
            })
        return out

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._loop, name="tts-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def say(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.start()
        self._queue.put(text)

    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    def wait_until_done(self, timeout: float | None = None) -> None:
        start = threading.Event()
        start.wait(0)
        import time
        deadline = None if timeout is None else time.monotonic() + timeout
        while self._speaking.is_set() or not self._queue.empty():
            if deadline is not None and time.monotonic() > deadline:
                return
            threading.Event().wait(0.05)

    # ── worker ─────────────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop_flag.is_set():
            item = self._queue.get()
            if item is None:
                break
            self._speaking.set()
            try:
                self._speak_one(item)
            except Exception as e:
                log.exception("TTS speak failed: %s", e)
            finally:
                self._speaking.clear()

    def _speak_one(self, text: str) -> None:
        if self._backend == "pyttsx3" and self._engine is not None:
            self._engine.say(text)
            self._engine.runAndWait()
            return
        if self._backend == "sapi-powershell":
            ps = (
                "Add-Type -AssemblyName System.Speech;"
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                f"$s.Rate = {max(-10, min(10, int((self._rate - 200) / 20)))};"
                "$s.Speak([Console]::In.ReadToEnd())"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                input=text,
                text=True,
                check=False,
                creationflags=_no_window_flag(),
            )
            return
        log.info("[tts:text-only] %s", text)


def _no_window_flag() -> int:
    if sys.platform == "win32":
        return 0x08000000  # CREATE_NO_WINDOW
    return 0
