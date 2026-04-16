from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from huggingface_hub import hf_hub_download
from llama_cpp import Llama

from local_mind.config import settings

log = logging.getLogger(__name__)

KNOWN_MODELS: dict[str, dict[str, str]] = {
    "tinyllama-1.1b": {
        "repo": "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
        "file": "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
        "chat_format": "chatml",
    },
    "phi-3-mini": {
        "repo": "bartowski/Phi-3.1-mini-4k-instruct-GGUF",
        "file": "Phi-3.1-mini-4k-instruct-Q4_K_M.gguf",
        "chat_format": "chatml",
    },
    "qwen2.5-1.5b": {
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "chat_format": "chatml",
    },
}


def ensure_model(name: str) -> Path:
    info = KNOWN_MODELS.get(name)
    if not info:
        p = Path(name)
        if p.is_file():
            return p
        raise FileNotFoundError(
            f"Unknown model '{name}'. Known: {list(KNOWN_MODELS)}. "
            "Or pass an absolute path to a .gguf file."
        )

    dest_dir = settings.models_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    local = dest_dir / info["file"]
    if local.is_file():
        log.info("Model already cached: %s", local)
        return local
    log.info("Downloading %s / %s …", info["repo"], info["file"])
    path = hf_hub_download(
        repo_id=info["repo"],
        filename=info["file"],
        local_dir=str(dest_dir),
        local_dir_use_symlinks=False,
    )
    return Path(path)


@dataclass
class ModelManager:
    _llm: Llama | None = field(default=None, repr=False)
    _current_path: Path | None = None
    _chat_format: str | None = None

    @property
    def loaded(self) -> bool:
        return self._llm is not None

    @property
    def model_name(self) -> str | None:
        if self._current_path is None:
            return None
        return self._current_path.stem

    def load(self, name_or_path: str) -> None:
        path = ensure_model(name_or_path)
        if self._current_path == path and self._llm is not None:
            return
        self.unload()
        info = KNOWN_MODELS.get(name_or_path, {})
        chat_fmt = info.get("chat_format", "chatml")
        log.info("Loading %s  (ctx=%d, gpu_layers=%d) …", path.name, settings.llm_n_ctx, settings.llm_n_gpu_layers)
        self._llm = Llama(
            model_path=str(path),
            n_ctx=settings.llm_n_ctx,
            n_gpu_layers=settings.llm_n_gpu_layers,
            chat_format=chat_fmt,
            verbose=False,
        )
        self._current_path = path
        self._chat_format = chat_fmt
        log.info("Model loaded: %s", path.name)

    def unload(self) -> None:
        if self._llm is not None:
            del self._llm
            self._llm = None
            self._current_path = None
            self._chat_format = None

    def complete_chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float = 0.7,
        stream: bool = False,
        repeat_penalty: float | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ) -> Any:
        if self._llm is None:
            raise RuntimeError("No model loaded. Call /api/models/load first.")
        return self._llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens or settings.llm_max_tokens,
            temperature=temperature,
            stream=stream,
            repeat_penalty=repeat_penalty if repeat_penalty is not None else settings.llm_repeat_penalty,
            top_p=top_p if top_p is not None else settings.llm_top_p,
            frequency_penalty=frequency_penalty if frequency_penalty is not None else settings.llm_frequency_penalty,
            presence_penalty=presence_penalty if presence_penalty is not None else settings.llm_presence_penalty,
        )

    def list_available(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, info in KNOWN_MODELS.items():
            local = settings.models_dir / info["file"]
            out.append({
                "name": name,
                "repo": info["repo"],
                "file": info["file"],
                "downloaded": local.is_file(),
                "loaded": self._current_path == local if local.is_file() else False,
                "size_mb": round(local.stat().st_size / 1_048_576, 1) if local.is_file() else None,
            })
        return out


model_manager = ModelManager()
