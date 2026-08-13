"""trace.moe anime screenshot recognition client."""
import logging
import requests

logger = logging.getLogger(__name__)
API_URL = "https://api.trace.moe/search?anilistInfo"
TIMEOUT = 25


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def parse_results(data: dict, min_similarity: float = 0.87) -> list[dict]:
    raw = data.get("result", []) if isinstance(data, dict) else []
    # trace.moe always returns nearest neighbors, even for unrelated photos.
    # If two different anime tie at the top, this is not a trustworthy match.
    if len(raw) >= 2:
        first_sim = float(raw[0].get("similarity") or 0)
        second_sim = float(raw[1].get("similarity") or 0)
        first_id = (raw[0].get("anilist") or {}).get("id")
        second_id = (raw[1].get("anilist") or {}).get("id")
        if first_id != second_id and first_sim - second_sim < 0.01:
            return []
    out = []
    for item in raw:
        sim = float(item.get("similarity") or 0)
        if sim < min_similarity:
            continue
        ani = item.get("anilist") or {}
        titles = ani.get("title") or {}
        title = titles.get("native") or titles.get("romaji") or titles.get("english") or item.get("filename") or "未知动画"
        out.append({
            "title": title,
            "episode": item.get("episode"),
            "at": _clock(item.get("from") or 0),
            "similarity": sim * 100,
            "preview": item.get("video") or "",
            "image": item.get("image") or "",
            "anilist_id": ani.get("id"),
        })
    out.sort(key=lambda x: x["similarity"], reverse=True)
    return out


def search(image_path: str) -> list[dict]:
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                API_URL,
                files={"image": ("image.jpg", f, "image/jpeg")},
                timeout=TIMEOUT,
            )
        resp.raise_for_status()
        return parse_results(resp.json())
    except Exception as e:
        logger.warning("trace.moe search failed: %s", e)
        return []
