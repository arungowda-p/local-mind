from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path

from local_mind.config import settings

log = logging.getLogger(__name__)

_whisper_model = None


def _ensure_whisper():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "Voice support requires faster-whisper. "
            "Install with:  pip install 'local-mind[voice]'"
        )
    model_dir = settings.whisper_dir
    model_dir.mkdir(parents=True, exist_ok=True)
    size = settings.whisper_model_size
    log.info("Loading Whisper model '%s' (CPU, int8) …", size)
    _whisper_model = WhisperModel(
        size,
        device="cpu",
        compute_type="int8",
        download_root=str(model_dir),
    )
    log.info("Whisper loaded.")
    return _whisper_model


def transcribe_bytes(audio_bytes: bytes, language: str | None = None) -> str:
    model = _ensure_whisper()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        segments, info = model.transcribe(
            tmp_path,
            language=language,
            beam_size=1,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        log.info("Transcribed %d bytes → %d chars (lang=%s)", len(audio_bytes), len(text), info.language)
        return text
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def transcribe_file(path: Path | str, language: str | None = None) -> str:
    return transcribe_bytes(Path(path).read_bytes(), language)
