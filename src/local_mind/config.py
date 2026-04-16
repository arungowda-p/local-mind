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
    llm_repeat_penalty: float = 1.3
    llm_top_p: float = 0.9
    llm_frequency_penalty: float = 0.2
    llm_presence_penalty: float = 0.2

    embed_model_name: str = "all-MiniLM-L6-v2"

    whisper_model_size: str = "tiny"

    chunk_size: int = 512
    chunk_overlap: int = 64
    rag_top_k: int = 5

    host: str = "127.0.0.1"
    port: int = 8766


settings = Settings()
