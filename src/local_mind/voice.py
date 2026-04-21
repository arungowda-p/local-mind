from __future__ import annotations

import logging
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from local_mind.config import settings

log = logging.getLogger(__name__)

_models: dict[str, Any] = {}

_KNOWN_SIZES = {
    "tiny", "tiny.en",
    "base", "base.en",
    "small", "small.en",
    "medium", "medium.en",
    "large", "large-v1", "large-v2", "large-v3",
    "large-v3-turbo", "distil-large-v3",
}


def _resolve_size(size: str | None) -> str:
    return (size or settings.whisper_model_size).strip()


def _get_model(size: str | None = None):
    key = _resolve_size(size)
    cached = _models.get(key)
    if cached is not None:
        return cached
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "Voice support requires faster-whisper. "
            "Install with:  pip install faster-whisper"
        ) from e
    model_dir = settings.whisper_dir
    model_dir.mkdir(parents=True, exist_ok=True)
    log.info("Loading Whisper model '%s' (CPU, int8) …", key)
    model = WhisperModel(
        key,
        device="cpu",
        compute_type="int8",
        download_root=str(model_dir),
    )
    _models[key] = model
    log.info("Whisper '%s' loaded.", key)
    return model


def _suffix_for(filename: str | None) -> str:
    if not filename:
        return ".audio"
    suf = Path(filename).suffix.lower()
    return suf if suf else ".audio"


def transcribe_stream(
    audio_bytes: bytes,
    language: str | None = None,
    model_size: str | None = None,
    filename: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield event dicts as Whisper produces them.

    Event types:
      {"type": "info", "language": str, "language_probability": float, "duration": float}
      {"type": "segment", "index": int, "start": float, "end": float, "text": str}
      {"type": "done", "text": str}
    """
    model = _get_model(model_size)
    suffix = _suffix_for(filename)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        segments, info = model.transcribe(
            tmp_path,
            language=language,
            beam_size=1,
            vad_filter=True,
        )
        yield {
            "type": "info",
            "language": info.language,
            "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
            "duration": float(getattr(info, "duration", 0.0) or 0.0),
        }
        parts: list[str] = []
        for idx, seg in enumerate(segments):
            text = (seg.text or "").strip()
            parts.append(text)
            yield {
                "type": "segment",
                "index": idx,
                "start": float(seg.start),
                "end": float(seg.end),
                "text": text,
            }
        full = " ".join(p for p in parts if p).strip()
        log.info(
            "Transcribed %d bytes → %d chars (lang=%s, model=%s)",
            len(audio_bytes), len(full), info.language, _resolve_size(model_size),
        )
        yield {"type": "done", "text": full}
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def transcribe_bytes(
    audio_bytes: bytes,
    language: str | None = None,
    model_size: str | None = None,
    filename: str | None = None,
) -> str:
    parts: list[str] = []
    for evt in transcribe_stream(
        audio_bytes,
        language=language,
        model_size=model_size,
        filename=filename,
    ):
        if evt["type"] == "segment":
            parts.append(evt["text"])
    return " ".join(p for p in parts if p).strip()


def transcribe_file(
    path: Path | str,
    language: str | None = None,
    model_size: str | None = None,
) -> str:
    p = Path(path)
    return transcribe_bytes(
        p.read_bytes(),
        language=language,
        model_size=model_size,
        filename=p.name,
    )


def known_model_sizes() -> list[str]:
    return sorted(_KNOWN_SIZES)
