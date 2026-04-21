from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel


def _data_root() -> Path:
    env = os.environ.get("LOCALMIND_DATA")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent.parent / "data"


class Settings(BaseModel):
    data_dir: Path = _data_root()

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def whisper_dir(self) -> Path:
        return self.data_dir / "whisper"

    llm_model_file: str = ""
    llm_n_ctx: int = 4096
    llm_n_gpu_layers: int = 0
    llm_max_tokens: int = 1024
    llm_repeat_penalty: float = 1.1
    llm_top_p: float = 0.9
    llm_frequency_penalty: float = 0.0
    llm_presence_penalty: float = 0.0

    llm_code_max_tokens: int = 1536
    llm_code_repeat_penalty: float = 1.05

    embed_model_name: str = "all-MiniLM-L6-v2"

    whisper_model_size: str = "base"

    chunk_size: int = 512
    chunk_overlap: int = 64
    rag_top_k: int = 5

    host: str = "127.0.0.1"
    port: int = 8766

    # ── Assistant (JARVIS-style voice loop) ──────────────────────────────────
    assistant_wake_word: str = "hey_jarvis_v0.1"
    assistant_wake_threshold: float = 0.5
    assistant_sample_rate: int = 16000
    assistant_max_record_seconds: float = 12.0
    assistant_min_record_seconds: float = 0.6
    assistant_silence_ms: int = 1200
    assistant_followup_seconds: float = 6.0
    assistant_hotkey: str = "<ctrl>+<alt>+j"
    assistant_tts_rate: int = 185
    assistant_tts_voice: str | None = None
    assistant_require_confirmation: bool = True
    assistant_auto_start: bool = False

    @property
    def assistant_docs_dir(self) -> Path:
        env = os.environ.get("LOCALMIND_DOCS_DIR")
        if env:
            return Path(env).expanduser().resolve()
        return Path.home() / "Documents" / "LocalMind"


settings = Settings()
