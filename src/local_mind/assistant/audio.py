from __future__ import annotations

import io
import logging
import queue
import threading
import wave
from collections import deque
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

FRAME_MS = 80
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"


class MicStream:
    """Low-latency 16 kHz mono mic stream built on sounddevice.

    Subscribers (wake detector, recorder) poll :meth:`read_frame`, which blocks
    for up to ``timeout`` seconds and returns an int16 numpy frame of
    ``FRAME_MS`` duration. The stream keeps a rolling buffer so consumers can
    grab a few hundred ms of pre-roll when a wake word fires.
    """

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        frame_ms: int = FRAME_MS,
        device: int | str | None = None,
        preroll_ms: int = 800,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_samples = int(sample_rate * frame_ms / 1000)
        self.device = device
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=256)
        self._preroll = deque(maxlen=max(1, preroll_ms // frame_ms))
        self._stream: Any = None
        self._lock = threading.Lock()
        self._open = False

    def open(self) -> None:
        if self._open:
            return
        try:
            import sounddevice as sd
        except OSError as e:
            raise RuntimeError(
                "sounddevice requires PortAudio. Install with:  pip install sounddevice"
            ) from e

        def _cb(indata: np.ndarray, frames: int, t: Any, status: Any) -> None:
            if status:
                log.debug("sounddevice status: %s", status)
            frame = indata[:, 0].astype(np.int16, copy=False) if indata.ndim > 1 else indata.astype(np.int16, copy=False)
            if frame.size != self.frame_samples:
                frame = frame[: self.frame_samples]
            with self._lock:
                self._preroll.append(frame.copy())
            try:
                self._queue.put_nowait(frame.copy())
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(frame.copy())
                except queue.Empty:
                    pass

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=self.frame_samples,
            callback=_cb,
            device=self.device,
        )
        self._stream.start()
        self._open = True
        log.info("Mic opened at %d Hz / %d ms frames", self.sample_rate, self.frame_ms)

    def close(self) -> None:
        if not self._open:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self._open = False
        with self._lock:
            self._preroll.clear()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        log.info("Mic closed.")

    @property
    def is_open(self) -> bool:
        return self._open

    def read_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def preroll(self) -> list[np.ndarray]:
        with self._lock:
            return list(self._preroll)

    def drain(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


def samples_to_wav_bytes(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Encode an int16 mono numpy array as a RIFF/WAV byte string."""
    if samples.dtype != np.int16:
        samples = samples.astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()
