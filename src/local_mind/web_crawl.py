"""
Web crawl fallback for LocalMind.

When the local knowledge base cannot answer a query (low confidence or
empty), this module:
  1. Searches the web — Google first, DuckDuckGo as an automatic fallback
     when Google serves us its JS-challenge or consent page (which it does
     aggressively for non-browser clients).
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

GOOGLE_SEARCH_ENDPOINT = "https://www.google.com/search"
DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
# Short-circuit Google's consent interstitial (EU / first-time visitors).
_CONSENT_COOKIE = "CONSENT=YES+cb; SOCS=CAESHAgBEhJnd3NfMjAyMzA1MjktMF9SQzIaAmVuIAEaBgiA4OzBBg"

DEFAULT_MAX_RESULTS = 4
DEFAULT_TIMEOUT_S = 15

_BLOCKED_HOSTS = {
    "google.com",
    "google.co",
    "googleusercontent.com",
    "webcache.googleusercontent.com",
    "duckduckgo.com",
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


def _normalize_google_link(href: str) -> str | None:
    """Google wraps result links in /url?q=<encoded>&sa=...; unwrap to the real URL."""
    if not href:
        return None
    try:
        if href.startswith("/url") or href.startswith("/search"):
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            for key in ("q", "url"):
                target = qs.get(key, [None])[0]
                if target and target.startswith("http"):
                    return unquote(target)
            return None
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


def _normalize_ddg_link(href: str) -> str | None:
    if not href:
        return None
    try:
        parsed = urlparse(href)
        if parsed.path.startswith("/l/") or "uddg" in parsed.query:
            target = parse_qs(parsed.query).get("uddg", [None])[0]
            if target:
                return unquote(target)
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("http"):
            return href
    except Exception:
        return None
    return None


def _search_google(query: str, max_results: int) -> list[SearchHit]:
    try:
        resp = httpx.get(
            GOOGLE_SEARCH_ENDPOINT,
            params={
                "q": query,
                "num": max(10, max_results * 3),
                "hl": "en",
                "gl": "us",
                "pws": 0,
            },
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "Cookie": _CONSENT_COOKIE,
            },
            timeout=DEFAULT_TIMEOUT_S,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        log.info("Google search failed (%s); will try fallback.", e)
        return []

    final = str(resp.url)
    if "consent.google.com" in final or "/sorry/" in final:
        log.info("Google returned a consent/block page; will try fallback.")
        return []
    # Detect the JS-challenge page Google serves to non-browser clients.
    if "/httpservice/retry/enablejs" in resp.text or "enablejs" in resp.text[:2000]:
        log.info("Google returned a JS-challenge page; will try fallback.")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    hits: list[SearchHit] = []
    seen: set[str] = set()

    containers = soup.select("div.g, div.tF2Cxc, div[data-hveid]") or [soup]
    for result in containers:
        link_el = result.select_one('a[href^="/url?q="], a[href^="/url?"], a[href^="http"]')
        if not link_el:
            continue
        target = _normalize_google_link(link_el.get("href", ""))
        if not target or target in seen or not _is_allowed(target):
            continue
        title_el = result.select_one("h3") or link_el
        snippet_el = result.select_one(
            "div.VwiC3b, span.VwiC3b, div[data-sncf], .lEBKkf, .lyLwlc"
        )
        title = title_el.get_text(" ", strip=True)
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        if not title:
            continue
        seen.add(target)
        hits.append(SearchHit(title=title, url=target, snippet=snippet))
        if len(hits) >= max_results:
            break

    log.info("Google search '%s' → %d hits", query, len(hits))
    return hits


def _search_ddg(query: str, max_results: int) -> list[SearchHit]:
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
        log.warning("DuckDuckGo search failed: %s", e)
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

    log.info("DuckDuckGo fallback '%s' → %d hits", query, len(hits))
    return hits


def search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> list[SearchHit]:
    """Run a web search and return up to N parsed hits.

    Tries Google first; if Google returns zero hits (consent gate, JS
    challenge, or 429), falls back to DuckDuckGo HTML so the KB ingest
    pipeline keeps working without user intervention.
    """
    if not query.strip():
        return []
    hits = _search_google(query, max_results)
    if hits:
        return hits
    return _search_ddg(query, max_results)


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
