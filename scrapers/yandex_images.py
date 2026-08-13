"""Yandex Images general reverse-search uploader."""
import logging
from urllib.parse import quote
import requests

logger = logging.getLogger(__name__)
UPLOAD_URL = ('https://yandex.com/images/search?rpt=imageview&format=json&request='
              '%7B%22blocks%22%3A%5B%7B%22block%22%3A%22b-page_type_search-by-image__link%22%7D%5D%7D')
TIMEOUT = 25


def parse_upload(data: dict) -> dict | None:
    try:
        params = data["blocks"][0]["params"]
        cbir_id = params["cbirId"]
        original = params.get("originalImageUrl", "")
    except (KeyError, IndexError, TypeError):
        return None
    return {
        "cbir_id": cbir_id,
        "original_image_url": original,
        "search_url": f"https://yandex.com/images/search?rpt=imageview&cbir_id={quote(cbir_id, safe='')}",
    }


def search(image_path: str) -> dict | None:
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                UPLOAD_URL,
                files={"upfile": ("blob", f, "image/jpeg")},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=TIMEOUT,
            )
        resp.raise_for_status()
        return parse_upload(resp.json())
    except Exception as e:
        logger.warning("Yandex image search failed: %s", e)
        return None
