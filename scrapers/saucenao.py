"""Saucenao reverse image search client.

https://saucenao.com — free API key (register on the site), ~100 searches/day.
Returns top matches for an image across pixiv / e-hentai / danbooru / twitter
etc. Search results that carry an e-hentai URL can be fed straight into the
existing gallery pipeline (scrape → telegraph).
"""
import logging

import requests

logger = logging.getLogger(__name__)

SAUCENAO_URL = "https://saucenao.com/search.php"
TIMEOUT = 30

# index_id → display name (common ones; unknown ids fall back to the number)
INDEX_NAMES = {
    0: "H-Game CG", 1: "H-Misc", 2: "H-Anime", 3: "Pixiv", 4: "Pixiv",
    5: "Danbooru", 6: "Drawr", 7: "Nijie", 8: "Yande.re", 9: "Danbooru",
    10: "Drawr", 11: "MangaDex", 12: "E-Hentai", 15: "E-Shuushuu",
    16: "Zerochan", 18: "E-Hentai", 19: "Pixiv", 20: "Anime-Pictures",
    21: "Anime-Pictures", 22: "Anime-Pictures", 23: "Anime-Pictures",
    24: "IMDB", 25: "Shutterstock", 26: "Bcy.net", 27: "Bcy.net",
    28: "Kemono", 29: "Kemono", 30: "FAKKU", 31: "H-Anime", 32: "H-Anime",
    33: "H-Anime", 34: "Sankaku", 35: "Sankaku", 36: "H-Anime",
    37: "Konachan", 38: "Sankaku", 39: "Anime-Pictures", 40: "3D",
    41: "Anime-Pictures", 42: "Anime-Pictures", 43: "Anime-Pictures",
    44: "E-Hentai", 45: "E-Hentai", 46: "Twitter", 47: "FurAffinity",
    48: "Twitter", 49: "Furry Network", 50: "FAKKU", 51: "Pawoo",
    52: "Twitter", 53: "Twitter", 54: "Twitter", 55: "Twitter",
    56: "Twitter", 57: "Twitter", 58: "Twitter", 59: "Twitter",
    60: "Twitter", 61: "Twitter", 62: "Twitter", 63: "Twitter",
    64: "MangaDex", 65: "MyAnimeList", 66: "MyAnimeList", 67: "MyAnimeList",
    68: "MangaDex", 69: "AniDb", 70: "AniDb", 71: "AniDb", 72: "AniDb",
    73: "AniDb", 74: "AniDb", 75: "AniDb", 76: "AniDb", 77: "AniDb",
    78: "AniDb", 79: "AniDb", 80: "AniDb", 81: "AniDb", 82: "AniDb",
    83: "AniDb", 84: "AniDb", 85: "AniDb", 86: "AniDb", 87: "AniDb",
    88: "AniDb", 89: "AniDb", 90: "AniDb", 91: "AniDb", 92: "AniDb",
    93: "AniDb", 94: "AniDb", 95: "AniDb", 96: "AniDb", 97: "AniDb",
    98: "AniDb", 99: "AniDb", 100: "AniDb", 101: "AniDb", 102: "AniDb",
    103: "AniDb", 104: "AniDb", 105: "AniDb", 106: "AniDb", 107: "AniDb",
    108: "AniDb", 109: "AniDb", 110: "AniDb", 111: "AniDb", 112: "AniDb",
    113: "AniDb", 114: "AniDb", 115: "AniDb", 116: "AniDb", 117: "AniDb",
    118: "AniDb", 119: "AniDb", 120: "AniDb", 121: "AniDb", 122: "AniDb",
    123: "AniDb", 124: "AniDb", 125: "AniDb", 126: "AniDb", 127: "AniDb",
    128: "AniDb", 129: "AniDb", 130: "AniDb", 131: "AniDb", 132: "AniDb",
    133: "AniDb", 134: "AniDb", 135: "AniDb", 136: "AniDb", 137: "AniDb",
    138: "AniDb", 139: "AniDb", 140: "AniDb", 141: "AniDb", 142: "AniDb",
    143: "AniDb", 144: "AniDb", 145: "AniDb", 146: "AniDb", 147: "AniDb",
    148: "AniDb", 149: "AniDb", 150: "AniDb", 151: "AniDb", 152: "AniDb",
    153: "AniDb", 154: "AniDb", 155: "AniDb", 156: "AniDb", 157: "AniDb",
    158: "AniDb", 159: "AniDb", 160: "AniDb", 161: "AniDb", 162: "AniDb",
    163: "AniDb", 164: "AniDb", 165: "AniDb", 166: "AniDb", 167: "AniDb",
    168: "AniDb", 169: "AniDb", 170: "AniDb", 171: "AniDb", 172: "AniDb",
    173: "AniDb", 174: "AniDb", 175: "AniDb", 176: "AniDb", 177: "AniDb",
    178: "AniDb", 179: "AniDb", 180: "AniDb", 181: "AniDb", 182: "AniDb",
    183: "AniDb", 184: "AniDb", 185: "AniDb", 186: "AniDb", 187: "AniDb",
    188: "AniDb", 189: "AniDb", 190: "AniDb", 191: "AniDb", 192: "AniDb",
    193: "AniDb", 194: "AniDb", 195: "AniDb", 196: "AniDb", 197: "AniDb",
    198: "AniDb", 199: "AniDb", 200: "AniDb", 201: "AniDb", 202: "AniDb",
    203: "AniDb", 204: "AniDb", 205: "AniDb", 206: "AniDb", 207: "AniDb",
    208: "AniDb", 209: "AniDb", 210: "AniDb", 211: "AniDb", 212: "AniDb",
}


def index_name(index_id: int) -> str:
    """Human-readable source name for a Saucenao index id."""
    return INDEX_NAMES.get(int(index_id), str(index_id))


def parse_results(data: dict) -> list[dict]:
    """Convert Saucenao JSON into [{similarity, title, index_name, urls}],
    sorted by similarity descending. Empty on error / no matches."""
    if not data or data.get("header", {}).get("status") != 0:
        return []
    out = []
    for res in data.get("results", []) or []:
        hdr = res.get("header", {})
        info = res.get("data", {}) or {}
        try:
            sim = float(hdr.get("similarity", 0))
        except (TypeError, ValueError):
            sim = 0.0
        out.append({
            "similarity": sim,
            "title": info.get("title") or info.get("member_name") or "(无标题)",
            "index_name": index_name(hdr.get("index_id", 0)),
            "urls": [u for u in (info.get("ext_urls") or []) if isinstance(u, str)],
        })
    out.sort(key=lambda r: r["similarity"], reverse=True)
    return out


def search(api_key: str, image_path: str, numres: int = 5) -> list[dict]:
    """Reverse-search an image file with Saucenao. Returns parsed results
    (empty list on any failure)."""
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                SAUCENAO_URL,
                data={
                    "api_key": api_key,
                    "output_type": "2",
                    "numres": str(numres),
                    "db": "999",  # all indexes
                },
                files={"file": ("image.jpg", f, "image/jpeg")},
                timeout=TIMEOUT,
            )
        resp.raise_for_status()
        return parse_results(resp.json())
    except Exception as e:
        logger.warning(f"Saucenao search failed: {e}")
        return []
