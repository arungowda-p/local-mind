from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np

from local_mind.assistant.actions import ActionRegistry, ActionResult
from local_mind.assistant.apps import AppIndex
from local_mind.assistant.audio import MicStream, samples_to_wav_bytes
from local_mind.assistant.events import EventBus
from local_mind.assistant.router import IntentRouter
from local_mind.assistant.tts import TextToSpeech
from local_mind.assistant.vad import EndpointDetector
from local_mind.assistant.wake import WakeWordDetector
from local_mind.config import settings

log = logging.getLogger(__name__)

CONFIRM_PHRASES = {"yes", "yep", "yeah", "confirm", "do it", "go ahead", "affirmative"}
CANCEL_PHRASES = {"no", "nope", "cancel", "stop", "abort", "never mind", "nevermind"}
SENSITIVE_INTENTS = {"shutdown", "reboot", "sleep", "close_app"}


class AssistantEngine:
    """State machine: idle → (wake or hotkey) → listening → processing → speaking → follow-up → idle."""

    def __init__(self) -> None:
        self.events = EventBus()
        self._apps = AppIndex()
        self._actions = ActionRegistry(apps=self._apps)
        self._router = IntentRouter(actions=self._actions)
        self._tts = TextToSpeech(rate=settings.assistant_tts_rate, voice=settings.assistant_tts_voice)
        self._mic: MicStream | None = None
        self._wake: WakeWordDetector | None = None
        self._state = "stopped"
        self._running = False
        self._thread: threading.Thread | None = None
        self._trigger = threading.Event()
        self._hotkey_listener: Any = None
        self._last_transcript = ""
        self._pending_confirm: tuple[str, str] | None = None  # (intent_name, original_text)

    # ── public ──────────────────────────────────────────────────────────────
    @property
    def state(self) -> str:
        return self._state

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "state": self._state,
            "wake_available": bool(self._wake and self._wake.available),
            "tts_available": self._tts.available(),
            "last_transcript": self._last_transcript,
            "app_count": len(self._apps.refresh()),
            "hotkey": settings.assistant_hotkey,
            "wake_word": settings.assistant_wake_word,
        }

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tts.start()
        self._apps.refresh(force=True)
        watching = self._apps.start_watcher()
        self._apps.add_listener(self._on_apps_changed)
        self._thread = threading.Thread(target=self._run, name="assistant-engine", daemon=True)
        self._thread.start()
        self._start_hotkey()
        self._set_state("starting")
        self.events.publish("info", {
            "message": f"Assistant started. Watching Start Menu: {watching}.",
        })

    def _on_apps_changed(self, apps: list[Any]) -> None:
        self.events.publish("apps", {"count": len(apps)})

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._trigger.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self._thread = None
        self._stop_hotkey()
        self._apps.stop_watcher()
        self._tts.stop()
        if self._mic:
            self._mic.close()
            self._mic = None
        self._set_state("stopped")
        self.events.publish("info", {"message": "Assistant stopped."})

    def run_command(self, text: str, speak: bool = True) -> dict[str, Any]:
        """Execute a typed command (no mic). Safe to call from any thread."""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "speech": "Empty command."}
        self.events.publish("transcript", {"text": text, "source": "typed"})
        self._set_state("processing")
        try:
            intent, result = self._route_with_confirm(text)
        finally:
            self._set_state("idle" if self._running else "stopped")
        self.events.publish("action", {
            "intent": intent.name,
            "slots": intent.slots,
            "result": result.to_dict(),
        })
        if speak and result.speech and self._tts.available():
            self._set_state("speaking")
            self._tts.say(result.speech)
            self._tts.wait_until_done(timeout=20.0)
            self._set_state("idle" if self._running else "stopped")
        return {
            "ok": result.ok,
            "speech": result.speech,
            "detail": result.detail,
            "intent": intent.name,
            "data": result.data,
        }

    def trigger(self) -> None:
        """Manually start a listen turn without the wake word."""
        self._trigger.set()

    def list_apps(self, limit: int = 200) -> list[dict[str, Any]]:
        self._apps.refresh()
        return [
            {"name": a.name, "kind": a.kind, "path": a.path}
            for a in self._apps.refresh()[:limit]
        ]

    # ── main loop ───────────────────────────────────────────────────────────
    def _run(self) -> None:
        try:
            self._mic = MicStream(sample_rate=settings.assistant_sample_rate)
            self._mic.open()
        except Exception as e:
            log.exception("Mic failed to open: %s", e)
            self.events.publish("error", {"message": f"Mic failed: {e}"})
            self._running = False
            self._set_state("error")
            return

        self._wake = WakeWordDetector(
            model_name=settings.assistant_wake_word,
            threshold=settings.assistant_wake_threshold,
        )
        if not self._wake.available:
            self.events.publish("info", {
                "message": "Wake word unavailable — use the hotkey or the web button.",
            })

        self._set_state("idle")
        follow_up_deadline = 0.0
        calibration_frames: list[np.ndarray] = []

        while self._running:
            triggered = False
            waiting_follow_up = time.monotonic() < follow_up_deadline

            frame = self._mic.read_frame(timeout=0.25)
            if frame is None:
                if self._trigger.is_set():
                    self._trigger.clear()
                    triggered = True
                elif waiting_follow_up:
                    continue
                else:
                    continue
            else:
                if len(calibration_frames) < 30:
                    calibration_frames.append(frame)
                if self._trigger.is_set():
                    self._trigger.clear()
                    triggered = True
                elif self._wake.available and not waiting_follow_up:
                    res = self._wake.push(frame)
                    if res is not None:
                        name, score = res
                        self.events.publish("wake", {"model": name, "score": score})
                        triggered = True

            if waiting_follow_up and not triggered:
                # still within follow-up window; listen continuously
                triggered = True
                follow_up_deadline = 0.0

            if not triggered:
                continue

            try:
                self._handle_turn(calibration_frames)
            except Exception as e:
                log.exception("Turn failed: %s", e)
                self.events.publish("error", {"message": str(e)})
                self._set_state("idle")
                continue

            follow_up_deadline = time.monotonic() + settings.assistant_followup_seconds
            if self._wake:
                self._wake.reset()

    def _handle_turn(self, calibration_frames: list[np.ndarray]) -> None:
        assert self._mic is not None
        vad = EndpointDetector(
            sample_rate=settings.assistant_sample_rate,
            silence_ms=settings.assistant_silence_ms,
            max_seconds=settings.assistant_max_record_seconds,
            min_seconds=settings.assistant_min_record_seconds,
        )
        vad.calibrate(calibration_frames[-20:] if len(calibration_frames) >= 10 else calibration_frames)
        vad.reset(self._mic.preroll())

        self._set_state("listening")
        self.events.publish("listening", {})
        self._mic.drain()

        tick = 0
        while self._running:
            frame = self._mic.read_frame(timeout=0.5)
            if frame is None:
                continue
            _, done = vad.push(frame)
            tick += 1
            if tick % 3 == 0:
                self.events.publish("level", {"rms": vad.level})
            if done:
                break

        samples = vad.collect()
        if samples.size == 0 or vad.speech_seconds() < 0.25:
            self.events.publish("info", {"message": "Didn't catch any speech."})
            self._set_state("idle")
            return

        self._set_state("processing")
        wav = samples_to_wav_bytes(samples, settings.assistant_sample_rate)
        try:
            from local_mind.voice import transcribe_bytes
            transcript = transcribe_bytes(wav, language=None, filename="assistant.wav")
        except Exception as e:
            log.exception("Transcription failed")
            self.events.publish("error", {"message": f"Transcription failed: {e}"})
            self._set_state("idle")
            return

        transcript = transcript.strip()
        self._last_transcript = transcript
        self.events.publish("transcript", {"text": transcript, "source": "mic"})
        if not transcript:
            self._set_state("idle")
            return

        intent, result = self._route_with_confirm(transcript)
        self.events.publish("action", {
            "intent": intent.name,
            "slots": intent.slots,
            "result": result.to_dict(),
        })

        if result.speech:
            self._set_state("speaking")
            self.events.publish("speech", {"text": result.speech})
            if self._tts.available():
                self._tts.say(result.speech)
                self._tts.wait_until_done(timeout=30.0)
        self._set_state("idle")

    # ── confirmation flow ──────────────────────────────────────────────────
    def _route_with_confirm(self, text: str) -> tuple[Any, ActionResult]:
        lowered = text.lower().strip().rstrip("?.!,")
        if self._pending_confirm is not None:
            intent_name, original = self._pending_confirm
            self._pending_confirm = None
            if any(p in lowered for p in CONFIRM_PHRASES):
                intent, result = self._router.route(original)
                return intent, result
            if any(p in lowered for p in CANCEL_PHRASES):
                from local_mind.assistant.router import Intent
                intent = Intent(intent_name, 1.0, {}, lambda *_: ActionResult(True, "Cancelled."), original)
                return intent, ActionResult(True, "Cancelled.")

        intent, result = self._router.route(text)
        if (
            settings.assistant_require_confirmation
            and intent.name in SENSITIVE_INTENTS
            and result.ok
            and intent.name != "close_app"  # close is fine, too cumbersome to confirm
        ):
            self._pending_confirm = (intent.name, text)
            return intent, ActionResult(True, f"Are you sure you want to {intent.name.replace('_', ' ')}? Say yes or no.")
        return intent, result

    # ── hotkey ─────────────────────────────────────────────────────────────
    def _start_hotkey(self) -> None:
        try:
            from pynput import keyboard  # type: ignore
        except ImportError:
            log.info("pynput not installed; hotkey disabled.")
            return
        try:
            listener = keyboard.GlobalHotKeys({settings.assistant_hotkey: self.trigger})
            listener.daemon = True
            listener.start()
            self._hotkey_listener = listener
            log.info("Hotkey registered: %s", settings.assistant_hotkey)
        except Exception as e:
            log.warning("Hotkey registration failed: %s", e)

    def _stop_hotkey(self) -> None:
        listener = self._hotkey_listener
        self._hotkey_listener = None
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:
            pass

    # ── util ───────────────────────────────────────────────────────────────
    def _set_state(self, new: str) -> None:
        if new == self._state:
            return
        prev = self._state
        self._state = new
        self.events.publish("state", {"state": new, "previous": prev})


_engine: AssistantEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> AssistantEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = AssistantEngine()
        return _engine
