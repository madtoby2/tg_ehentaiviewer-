"""Yandex Images general reverse-search uploader."""
import logging
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
UPLOAD_URL = ('https://yandex.com/images/search?rpt=imageview&format=json&request='
              '%7B%22blocks%22%3A%5B%7B%22block%22%3A%22b-page_type_search-by-image__link%22%7D%5D%7D')
TIMEOUT = 25


def _clean_url(url: str) -> str:
    try:
        p = urlsplit(url)
        clean_q = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith('utm_') and not k.startswith('__cf_')]
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(clean_q), ''))
    except Exception:
        return url


def parse_sites(html: str) -> list[dict]:
    """Extract Yandex's directly matched web pages from CbirSites cards."""
    if not html:
        return []
    soup = BeautifulSoup(html, 'lxml')
    out = []
    seen = set()
    for card in soup.select('.CbirSites-Item'):
        title_link = card.select_one('a.Link_view_default[href]')
        domain_link = card.select_one('a.CbirSites-ItemDomain[href]')
        thumb_link = card.select_one('a.Thumb[href]')
        if not title_link:
            continue
        url = _clean_url(title_link.get('href', ''))
        if not url or url in seen:
            continue
        seen.add(url)
        title = ' '.join(title_link.get_text(' ', strip=True).split())
        domain = ' '.join(domain_link.get_text(' ', strip=True).split()) if domain_link else urlsplit(url).netloc
        out.append({
            'title': title or domain or '(无标题)',
            'domain': domain,
            'url': url,
            'image_url': thumb_link.get('href', '') if thumb_link else '',
        })
    return out


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
        result = parse_upload(resp.json())
        if not result:
            return None
        sites_resp = requests.get(
            result['search_url'],
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=TIMEOUT,
        )
        sites_resp.raise_for_status()
        result['sites'] = parse_sites(sites_resp.text)[:5]
        return result
    except Exception as e:
        logger.warning("Yandex image search failed: %s", e)
        return None
