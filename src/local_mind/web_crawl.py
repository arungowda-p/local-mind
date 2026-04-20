"""
Web crawl fallback for LocalMind.

When the local knowledge base cannot answer a query (low confidence or
empty), this module:
  1. Searches the web via DuckDuckGo's HTML endpoint (no API key needed).
  2. Fetches the top N result pages.
  3. Ingests them into the knowledge base via `knowledge.learn_url`.
  4. Re-queries the KB so the chat layer can answer using fresh context.

Designed to fail soft: any network error returns an empty result and the
caller continues with whatever context (or fallback prompt) it already has.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from local_mind.knowledge import knowledge

log = logging.getLogger(__name__)

DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_MAX_RESULTS = 4
DEFAULT_TIMEOUT_S = 15

_BLOCKED_HOSTS = {
    "duckduckgo.com",
    "google.com",
    "bing.com",
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
}


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str


def _normalize_ddg_link(href: str) -> str | None:
    """DDG wraps result links in /l/?uddg=<encoded>; unwrap to the real URL."""
    if not href:
        return None
    try:
        parsed = urlparse(href)
        if parsed.path.startswith("/l/") or "uddg" in parsed.query:
            qs = parse_qs(parsed.query)
            target = qs.get("uddg", [None])[0]
            if target:
                return unquote(target)
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("http"):
            return href
    except Exception:
        return None
    return None


def _is_allowed(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    for blocked in _BLOCKED_HOSTS:
        if host == blocked or host.endswith("." + blocked):
            return False
    return True


def search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> list[SearchHit]:
    """Run a DuckDuckGo HTML search and return up to N parsed hits."""
    if not query.strip():
        return []
    try:
        resp = httpx.post(
            DDG_HTML_ENDPOINT,
            data={"q": query, "kl": "wt-wt"},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=DEFAULT_TIMEOUT_S,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning("Web search failed: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for result in soup.select("div.result, div.web-result, div.results_links"):
        link_el = result.select_one("a.result__a, a.result__url, h2 a")
        if not link_el:
            continue
        target = _normalize_ddg_link(link_el.get("href", ""))
        if not target or target in seen or not _is_allowed(target):
            continue
        snippet_el = result.select_one(".result__snippet, .snippet, p")
        title = link_el.get_text(" ", strip=True)
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        if not title:
            continue
        seen.add(target)
        hits.append(SearchHit(title=title, url=target, snippet=snippet))
        if len(hits) >= max_results:
            break

    log.info("Web search '%s' → %d hits", query, len(hits))
    return hits


def _shorten(text: str, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def crawl_and_learn(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """
    Search the web, fetch the top results, and ingest them into the
    knowledge base. Returns a summary the caller can show to the user.
    """
    hits = search(query, max_results=max_results)
    if not hits:
        return {"status": "no_results", "query": query, "sources": []}

    sources: list[dict[str, Any]] = []
    learned_chunks = 0
    for hit in hits:
        try:
            result = knowledge.learn_url(hit.url)
        except Exception as e:
            log.info("Skipping %s: %s", hit.url, e)
            sources.append(
                {
                    "url": hit.url,
                    "title": hit.title,
                    "snippet": _shorten(hit.snippet),
                    "status": "error",
                    "reason": str(e)[:200],
                }
            )
            continue
        sources.append(
            {
                "url": hit.url,
                "title": hit.title,
                "snippet": _shorten(hit.snippet),
                "status": result.get("status", "unknown"),
                "chunks": result.get("chunks", 0),
            }
        )
        if result.get("status") == "learned":
            learned_chunks += int(result.get("chunks", 0))

    return {
        "status": "learned" if learned_chunks > 0 else "fetched",
        "query": query,
        "chunks": learned_chunks,
        "sources": sources,
    }
