"""Best-effort marketplace scrapers.

Scraping HTML is brittle by nature; these helpers return whatever signals
they can extract and fall back to an empty list when the site changes layout.
Always treat as advisory.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PRICE_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d{2})?)")


def _to_price(text: str) -> float | None:
    if not text:
        return None
    m = PRICE_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _get(url: str) -> str | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        log.warning("marketplace fetch %s failed: %s", url, e)
        return None


def check_carsandbids(keyword: str, max_price: float | None = None) -> list[dict[str, Any]]:
    url = f"https://carsandbids.com/search?query={quote_plus(keyword)}"
    html = _get(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    for card in soup.select("li.auction-item, article, .auction-card"):
        title_el = card.select_one("h3, h2, a.title, .title")
        price_el = card.select_one(".bid-value, .price, [class*=price]")
        link_el = card.select_one("a[href]")
        title = title_el.get_text(strip=True) if title_el else None
        price = _to_price(price_el.get_text(strip=True)) if price_el else None
        link = link_el["href"] if link_el else None
        if not title or not link:
            continue
        if link.startswith("/"):
            link = f"https://carsandbids.com{link}"
        if max_price is not None and price is not None and price > max_price:
            continue
        results.append({
            "title": title, "price": price, "link": link, "site": "carsandbids"
        })
    return results


def check_ebay(keyword: str, max_price: float | None = None) -> list[dict[str, Any]]:
    params = f"_nkw={quote_plus(keyword)}&_sop=10"
    if max_price is not None:
        params += f"&_udhi={int(max_price)}"
    url = f"https://www.ebay.com/sch/i.html?{params}"
    html = _get(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    for item in soup.select("li.s-item"):
        title_el = item.select_one(".s-item__title")
        price_el = item.select_one(".s-item__price")
        link_el = item.select_one("a.s-item__link")
        title = title_el.get_text(strip=True) if title_el else None
        if not title or title.lower().startswith("shop on ebay"):
            continue
        price = _to_price(price_el.get_text(strip=True)) if price_el else None
        link = link_el["href"] if link_el else None
        if not link:
            continue
        if max_price is not None and price is not None and price > max_price:
            continue
        results.append({
            "title": title, "price": price, "link": link, "site": "ebay"
        })
    return results


def check_facebook_marketplace(
    keyword: str, location: str = "miami", max_price: float | None = None
) -> list[dict[str, Any]]:
    """Best-effort. Facebook Marketplace is mostly JS-rendered; expect empty results."""
    url = (
        f"https://www.facebook.com/marketplace/{quote_plus(location)}/search/"
        f"?query={quote_plus(keyword)}"
    )
    html = _get(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    for link_el in soup.select("a[href*='/marketplace/item/']"):
        title = link_el.get_text(" ", strip=True)
        href = link_el.get("href", "")
        if href.startswith("/"):
            href = f"https://www.facebook.com{href}"
        if not title:
            continue
        results.append({
            "title": title, "price": None, "link": href, "site": "facebook"
        })
    if not results:
        log.info(
            "facebook marketplace returned no parsable results "
            "(JS-rendered page) for %r",
            keyword,
        )
    return results


__all__ = [
    "check_carsandbids",
    "check_ebay",
    "check_facebook_marketplace",
]
