from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


class EndpointDetector:
    """Record until the user stops talking.

    Uses an RMS energy threshold that adapts to background noise. The caller
    repeatedly feeds frames via :meth:`push`; when the stream finishes (speech
    followed by enough silence, or hard time limit reached) it returns the
    accumulated waveform.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 80,
        silence_ms: int = 1200,
        max_seconds: float = 12.0,
        min_seconds: float = 0.6,
        noise_floor_rms: float = 120.0,
        speech_boost: float = 2.2,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.silence_frames_limit = max(1, silence_ms // frame_ms)
        self.max_frames = int(max_seconds * 1000 / frame_ms)
        self.min_frames = int(min_seconds * 1000 / frame_ms)
        self._noise = noise_floor_rms
        self._boost = speech_boost
        self._frames: list[np.ndarray] = []
        self._silence_frames = 0
        self._speech_frames = 0
        self._level = 0.0

    @property
    def level(self) -> float:
        """Latest RMS (0..1-ish after normalization to int16 max)."""
        return self._level

    def calibrate(self, frames: list[np.ndarray]) -> None:
        if not frames:
            return
        rms_vals = [_rms(f) for f in frames]
        base = float(np.median(rms_vals))
        self._noise = max(80.0, base * 1.3)
        log.debug("VAD calibrated noise floor: %.1f", self._noise)

    def reset(self, preroll: list[np.ndarray] | None = None) -> None:
        self._frames = list(preroll or [])
        self._silence_frames = 0
        self._speech_frames = len(self._frames)
        self._level = 0.0

    def push(self, frame: np.ndarray) -> tuple[bool, bool]:
        """Append a frame. Returns (is_speech, done)."""
        r = _rms(frame)
        self._level = min(1.0, r / 6000.0)
        thr = self._noise * self._boost
        is_speech = r > thr
        self._frames.append(frame)

        if is_speech:
            self._silence_frames = 0
            self._speech_frames += 1
        else:
            self._silence_frames += 1

        done = False
        if len(self._frames) >= self.max_frames:
            done = True
        elif (
            self._speech_frames >= self.min_frames
            and self._silence_frames >= self.silence_frames_limit
        ):
            done = True
        return is_speech, done

    def collect(self) -> np.ndarray:
        if not self._frames:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(self._frames).astype(np.int16)

    def speech_seconds(self) -> float:
        return self._speech_frames * self.frame_ms / 1000.0


def _rms(frame: np.ndarray) -> float:
    if frame.size == 0:
        return 0.0
    x = frame.astype(np.float32)
    return float(np.sqrt(np.mean(x * x)))
