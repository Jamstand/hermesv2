"""Web search, fetch, and extract utilities.

Uses `ddgs` (renamed from `duckduckgo-search`). Adapter pattern so the search
backend can be swapped (SearXNG, Brave) by editing only this file.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 20


def web_search(query: str, num_results: int = 5) -> list[dict[str, str]]:
    """DuckDuckGo search. Returns [{title, href, body}, ...]."""
    try:
        from ddgs import DDGS
    except ImportError:
        log.warning("ddgs not installed; web_search disabled")
        return []
    results: list[dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=num_results):
                results.append(
                    {
                        "title": r.get("title", ""),
                        "href": r.get("href", "") or r.get("url", ""),
                        "body": r.get("body", "") or r.get("description", ""),
                    }
                )
    except Exception as e:
        log.error("web_search failed: %s", e)
    return results


def web_fetch(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """GET the URL and return body text (no HTML)."""
    try:
        r = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout
        )
        r.raise_for_status()
    except requests.RequestException as e:
        log.error("web_fetch %s failed: %s", url, e)
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def web_extract(
    url: str, selectors: list[str], timeout: int = DEFAULT_TIMEOUT
) -> dict[str, list[str]]:
    """Extract CSS-selector-matched text from the URL."""
    out: dict[str, list[str]] = {s: [] for s in selectors}
    try:
        r = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout
        )
        r.raise_for_status()
    except requests.RequestException as e:
        log.error("web_extract %s failed: %s", url, e)
        return out
    soup = BeautifulSoup(r.text, "html.parser")
    for sel in selectors:
        for el in soup.select(sel):
            out[sel].append(el.get_text(" ", strip=True))
    return out


__all__ = ["web_search", "web_fetch", "web_extract"]
