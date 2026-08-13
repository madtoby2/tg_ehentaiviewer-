"""IQDB reverse image search (https://iqdb.org).

Free, no API key, unlimited-ish (rate-limited, queues under high load).
Covers e-hentai / danbooru / chan imageboards — a good free complement to
Saucenao for the EH/18comic use case.
"""
import logging
import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

IQDB_URL = "https://iqdb.org/"
TIMEOUT = 60

# Pages that are not actual result sets
_QUEUED_MARKERS = ("has been queued", "under high load")
_NO_MATCH_MARKERS = ("no relevant matches", "your image was not found")


def parse_results(html: str) -> list[dict]:
    """Parse IQDB result page into [{similarity, title, urls}], sorted by
    similarity desc. Empty on no-match / queued / error pages."""
    if not html:
        return []
    low = html.lower()
    if any(m in low for m in _QUEUED_MARKERS) or any(m in low for m in _NO_MATCH_MARKERS):
        return []

    out = []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.warning(f"IQDB parse failed: {e}")
        return []

    # Each result row carries a source link + a similarity percentage.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        row = a.find_parent("tr")
        sim = 0.0
        if row:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", row.get_text())
            if m:
                sim = float(m.group(1))
        title = (a.get("title") or "").strip()
        if not title:
            img = a.find("img")
            if img:
                title = (img.get("alt") or "").strip()
        out.append({
            "similarity": sim,
            "title": title or "(无标题)",
            "urls": [href],
        })

    # Deduplicate by url, keep best similarity
    seen = {}
    for r in out:
        u = r["urls"][0]
        if u not in seen or r["similarity"] > seen[u]["similarity"]:
            seen[u] = r
    out = list(seen.values())
    out.sort(key=lambda r: r["similarity"], reverse=True)
    return out


def search(image_path: str) -> list[dict]:
    """Reverse-search an image with IQDB. Returns parsed results or []."""
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                IQDB_URL,
                files={"file": ("image.jpg", f, "image/jpeg")},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=TIMEOUT,
            )
        resp.raise_for_status()
        return parse_results(resp.text)
    except Exception as e:
        logger.warning(f"IQDB search failed: {e}")
        return []
