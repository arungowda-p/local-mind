from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

CHUNK_SAMPLES = 1280  # openWakeWord operates on 80 ms @ 16 kHz


class WakeWordDetector:
    """Thin wrapper around openWakeWord.

    Accepts 16-kHz int16 frames of any size, accumulates to 80-ms chunks, and
    returns the highest-scoring model when the score crosses ``threshold``.
    If the underlying model library isn't installed or the model fails to
    load, :attr:`available` is False and :meth:`push` always returns None —
    callers should fall back to the manual hotkey.
    """

    def __init__(self, model_name: str = "hey_jarvis_v0.1", threshold: float = 0.5) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self._oww: Any = None
        self._buffer = np.zeros(0, dtype=np.int16)
        self._last_trigger_at = 0.0
        self._cooldown_s = 1.5
        self._load()

    def _load(self) -> None:
        try:
            from openwakeword.model import Model  # type: ignore

            try:
                self._oww = Model(wakeword_models=[self.model_name], inference_framework="onnx")
            except Exception:
                self._oww = Model(wakeword_models=[self.model_name])
            log.info("Wake-word detector loaded: %s", self.model_name)
        except Exception as e:
            log.warning("Wake-word unavailable (%s); only hotkey trigger will work.", e)
            self._oww = None

    @property
    def available(self) -> bool:
        return self._oww is not None

    def reset(self) -> None:
        if self._oww is not None:
            try:
                self._oww.reset()
            except Exception:
                pass
        self._buffer = np.zeros(0, dtype=np.int16)

    def push(self, frame: np.ndarray) -> tuple[str, float] | None:
        """Feed a frame; return (model_name, score) if triggered."""
        if self._oww is None:
            return None
        if frame.dtype != np.int16:
            frame = frame.astype(np.int16)
        self._buffer = np.concatenate([self._buffer, frame])

        best: tuple[str, float] | None = None
        while self._buffer.size >= CHUNK_SAMPLES:
            chunk = self._buffer[:CHUNK_SAMPLES]
            self._buffer = self._buffer[CHUNK_SAMPLES:]
            try:
                scores = self._oww.predict(chunk)
            except Exception as e:
                log.debug("oww predict failed: %s", e)
                continue
            for name, score in scores.items():
                s = float(score)
                if s >= self.threshold and (best is None or s > best[1]):
                    best = (name, s)

        if best is None:
            return None

        now = time.monotonic()
        if now - self._last_trigger_at < self._cooldown_s:
            return None
        self._last_trigger_at = now
        self.reset()
        return best
