"""Whos.tv screenshot-to-AV search client.

Uses the site's authenticated web endpoints and returns a small, stable result
shape for the Telegram bot. Credentials are supplied by the caller and are
never logged or persisted here.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
BASE_URL = "https://whos.tv"
_SUCCESS_CODES = {0, 200, 200000}


def _float(text: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text or "")
    return float(match.group(1)) if match else 0.0


def parse_result_page(html_text: str, result_url: str) -> dict:
    """Parse video code, similarity, best timestamp and preview from results."""
    soup = BeautifulSoup(html_text or "", "lxml")
    matches = []
    seen = set()

    for link in soup.select('a[href^="/videos/"]'):
        href = link.get("href", "")
        path = urlparse(href).path
        parts = [part for part in path.split("/") if part]
        if len(parts) != 2 or parts[0] != "videos":
            continue
        code = parts[1].upper()
        if code in seen:
            continue

        card = link.find_parent(
            "div", class_=lambda value: value and "rounded-xl" in value and "overflow-hidden" in value
        )
        card = card or link.parent
        text = card.get_text(" ", strip=True) if card else link.get_text(" ", strip=True)
        similarity = _float(text)
        title_node = card.select_one("p .text-primary") if card else None
        title_parent = title_node.find_parent("p") if title_node else None
        title = title_parent.get_text(" ", strip=True) if title_parent else code

        at = ""
        preview = ""
        frame_url = ""
        frame_root = card.select_one(".result-image-best-match-frames") if card else None
        if frame_root:
            frame_link = frame_root.select_one("a[href]")
            if frame_link:
                frame_url = urljoin(BASE_URL, frame_link.get("href", ""))
                frame_text = frame_link.get_text(" ", strip=True)
                tm = re.search(r"\b\d{1,2}:\d{2}:\d{2}\b", frame_text)
                if tm:
                    at = tm.group(0)
                frame_similarity = _float(frame_text)
                if frame_similarity:
                    similarity = frame_similarity
                image = frame_link.find("img", src=True)
                if image:
                    preview = urljoin(BASE_URL, image["src"])

        matches.append({
            "code": code,
            "title": title,
            "similarity": similarity,
            "at": at,
            "url": urljoin(BASE_URL, f"/videos/{parts[1]}"),
            "frame_url": frame_url,
            "preview": preview,
        })
        seen.add(code)

    matches.sort(key=lambda item: item["similarity"], reverse=True)
    return {"result_url": result_url, "matches": matches}


class WhosTvClient:
    def __init__(
        self,
        username: str,
        password: str,
        *,
        request_timeout: int = 30,
        upload_timeout: int = 90,
        max_wait: int = 60,
        poll_interval: float = 2.0,
    ):
        self.username = username
        self.password = password
        self.request_timeout = request_timeout
        self.upload_timeout = upload_timeout
        self.max_wait = max_wait
        self.poll_interval = poll_interval
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Referer": f"{BASE_URL}/",
        })

    def close(self):
        self.session.close()

    def login(self) -> None:
        response = self.session.post(
            f"{BASE_URL}/api/login",
            json={"username": self.username, "password": self.password},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in _SUCCESS_CODES:
            raise RuntimeError(payload.get("message") or "Whos.tv login failed")

    def _upload(self, image_path: str) -> str:
        with open(image_path, "rb") as image:
            response = self.session.post(
                f"{BASE_URL}/upload-search",
                files={"file": (Path(image_path).name, image)},
                headers={"Accept": "text/plain,*/*", "X-Requested-With": "XMLHttpRequest"},
                timeout=self.upload_timeout,
                allow_redirects=False,
            )
        response.raise_for_status()
        return response.text.strip()

    def search(self, image_path: str) -> dict:
        self.login()
        wait_url = self._upload(image_path)
        if "login=1" in wait_url:
            # Session may have expired between login and upload. Refresh once.
            self.login()
            wait_url = self._upload(image_path)
        if "/search-wait/" not in wait_url:
            raise RuntimeError(f"Whos.tv upload rejected: {wait_url[:120]}")

        task_id = urlparse(wait_url).path.rstrip("/").split("/")[-1]
        deadline = time.monotonic() + self.max_wait
        status_url = f"{BASE_URL}/search-status/{task_id}"
        while time.monotonic() < deadline:
            response = self.session.get(
                status_url,
                headers={"Accept": "text/plain,*/*", "X-Requested-With": "XMLHttpRequest"},
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            result_url = response.text.strip()
            if "/search-img/" in result_url:
                page = self.session.get(result_url, timeout=self.request_timeout)
                page.raise_for_status()
                return parse_result_page(page.text, result_url)
            # The status endpoint returns a numeric worker state (observed
            # values include "1") while the asynchronous match is running.
            if not result_url or result_url.isdigit() or "/search-wait/" in result_url:
                time.sleep(self.poll_interval)
                continue
            raise RuntimeError(f"Whos.tv search failed: {result_url[:120]}")
        raise TimeoutError(f"Whos.tv search timed out after {self.max_wait}s")


def search(username: str, password: str, image_path: str) -> dict | None:
    if not username or not password:
        return None
    client = WhosTvClient(username, password)
    try:
        return client.search(image_path)
    except Exception as exc:
        logger.warning("Whos.tv search failed: %s: %s", type(exc).__name__, exc)
        return None
    finally:
        client.close()
