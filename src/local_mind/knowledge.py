from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import chromadb
import httpx
from sentence_transformers import SentenceTransformer

from local_mind.config import settings

log = logging.getLogger(__name__)


def _extract_text(html: str, url: str) -> str:
    """Best-effort article extraction; falls back to BS4 if trafilatura fails."""
    try:
        import trafilatura

        text = trafilatura.extract(html, url=url, include_comments=False)
        if text and len(text) > 100:
            return text
    except Exception:
        pass
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = start + size
        chunks.append(" ".join(words[start:end]))
        start += size - overlap
    return chunks


@dataclass
class KnowledgeStore:
    _embed_model: SentenceTransformer | None = field(default=None, repr=False)
    _chroma: chromadb.ClientAPI | None = field(default=None, repr=False)
    _collection: Any = field(default=None, repr=False)
    _collection_name: str = "knowledge"

    def _ensure_embed(self) -> SentenceTransformer:
        if self._embed_model is None:
            log.info("Loading embedding model: %s", settings.embed_model_name)
            self._embed_model = SentenceTransformer(
                settings.embed_model_name,
                device="cpu",
            )
        return self._embed_model

    def _ensure_chroma(self) -> Any:
        if self._collection is not None:
            return self._collection
        settings.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._chroma = chromadb.PersistentClient(path=str(settings.chroma_dir))
        self._collection = self._chroma.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        return self._collection

    def _embed(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_embed()
        vecs = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return vecs.tolist()

    def learn_url(self, url: str) -> dict[str, Any]:
        log.info("Fetching %s", url)
        resp = httpx.get(url, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        text = _extract_text(resp.text, url)
        if len(text.strip()) < 50:
            return {"url": url, "status": "skipped", "reason": "too little text extracted"}
        chunks = _chunk_text(text, settings.chunk_size, settings.chunk_overlap)
        if not chunks:
            return {"url": url, "status": "skipped", "reason": "no chunks"}
        embeddings = self._embed(chunks)
        col = self._ensure_chroma()
        ids = [
            hashlib.sha256(f"{url}::{i}".encode()).hexdigest()[:16]
            for i in range(len(chunks))
        ]
        col.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=[{"url": url, "chunk_idx": i} for i in range(len(chunks))],
        )
        log.info("Stored %d chunks from %s", len(chunks), url)
        return {"url": url, "status": "learned", "chunks": len(chunks)}

    def learn_text(self, text: str, source: str = "paste") -> dict[str, Any]:
        chunks = _chunk_text(text, settings.chunk_size, settings.chunk_overlap)
        if not chunks:
            return {"source": source, "status": "empty"}
        embeddings = self._embed(chunks)
        col = self._ensure_chroma()
        ids = [
            hashlib.sha256(f"{source}::{i}".encode()).hexdigest()[:16]
            for i in range(len(chunks))
        ]
        col.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=[{"source": source, "chunk_idx": i} for i in range(len(chunks))],
        )
        return {"source": source, "status": "learned", "chunks": len(chunks)}

    def query(self, text: str, top_k: int | None = None) -> list[dict[str, Any]]:
        col = self._ensure_chroma()
        if col.count() == 0:
            return []
        k = min(top_k or settings.rag_top_k, col.count())
        emb = self._embed([text])
        results = col.query(query_embeddings=emb, n_results=k, include=["documents", "metadatas", "distances"])
        out: list[dict[str, Any]] = []
        docs = results.get("documents") or [[]]
        metas = results.get("metadatas") or [[]]
        dists = results.get("distances") or [[]]
        for doc, meta, dist in zip(docs[0], metas[0], dists[0]):
            out.append({"text": doc, "meta": meta, "distance": dist})
        return out

    def stats(self) -> dict[str, Any]:
        col = self._ensure_chroma()
        return {"collection": self._collection_name, "documents": col.count()}

    def clear(self) -> dict[str, str]:
        if self._chroma is not None:
            self._chroma.delete_collection(self._collection_name)
            self._collection = None
            self._collection = self._ensure_chroma()
        return {"status": "cleared"}


knowledge = KnowledgeStore()
