"""
E-Hentai / ExHentai gallery scraper.
"""
import re
import time
import logging
from urllib.parse import urlparse, parse_qs, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import cloudscraper
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

EH_API_URL = "https://api.e-hentai.org/api.php"
EH_GALLERY_RE = re.compile(
    r'https?://(?:e-hentai\.org|exhentai\.org)/g/(\d+)/([a-f0-9]+)/?'
)
EH_PAGE_RE = re.compile(
    r'https?://(?:e-hentai\.org|exhentai\.org)/s/([a-f0-9]+)/(\d+)-(\d+)/?'
)


def is_eh_link(url: str) -> bool:
    return bool(EH_GALLERY_RE.match(url))


def parse_gallery_url(url: str) -> dict | None:
    """Parse an EH gallery URL into (gid, gallery_token)."""
    m = EH_GALLERY_RE.match(url)
    if not m:
        return None
    return {"gid": int(m.group(1)), "gallery_token": m.group(2)}


def fetch_gallery_metadata(scraper: cloudscraper.CloudScraper, gid: int, gallery_token: str) -> dict | None:
    """Fetch gallery metadata via EH API."""
    # First try the API
    try:
        resp = scraper.post(EH_API_URL, json={
            "method": "gdata",
            "gidlist": [[gid, gallery_token]],
            "namespace": 1
        }, timeout=30)
        data = resp.json()
        if data.get("gmetadata") and len(data["gmetadata"]) > 0:
            meta = data["gmetadata"][0]
            logger.info(f"Gallery: {meta.get('title', 'N/A')} | pages: {meta.get('filecount', '?')}")
            return meta
    except Exception as e:
        logger.warning(f"EH API failed: {e}")

    return None


def fetch_page_urls(scraper: cloudscraper.CloudScraper, gallery_url: str, max_pages: int = 0) -> list[str]:
    """
    Scrape the gallery page(s) to get all individual page URLs.
    Returns list of URLs like https://e-hentai.org/s/<token>/<gid>-p-<n>
    """
    page_urls = []
    current_url = gallery_url

    while current_url:
        try:
            resp = scraper.get(current_url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to fetch gallery page {current_url}: {e}")
            break

        soup = BeautifulSoup(resp.text, 'lxml')

        # Find all page links (thumbnails that link to /s/ pages). EH may use
        # relative URLs, e-hentai, or exhentai hosts depending on cookies/domain.
        for a_tag in soup.find_all('a', href=True):
            href = urljoin(current_url, a_tag.get('href', ''))
            if EH_PAGE_RE.match(href) and href not in page_urls:
                page_urls.append(href)

        if max_pages and len(page_urls) >= max_pages:
            page_urls = page_urls[:max_pages]
            break

        # Check for next gallery thumbnail page in the EH pagination table.
        cur_p = int(parse_qs(urlparse(current_url).query).get('p', ['0'])[0] or 0)
        candidates = []
        for a_tag in soup.find_all('a', href=True):
            href = urljoin(current_url, a_tag['href'])
            p_values = parse_qs(urlparse(href).query).get('p')
            if not p_values:
                continue
            try:
                p_num = int(p_values[0])
            except ValueError:
                continue
            if p_num > cur_p:
                candidates.append((p_num, href))
        current_url = min(candidates, key=lambda item: item[0])[1] if candidates else None

    # Sort page URLs by page number
    def page_num(url):
        m = EH_PAGE_RE.match(url)
        return int(m.group(3)) if m else 0

    page_urls = sorted(set(page_urls), key=page_num)
    logger.info(f"Found {len(page_urls)} page URLs")
    return page_urls


def fetch_image_url(scraper: cloudscraper.CloudScraper, page_url: str) -> str | None:
    """Scrape a single page to get the full-size image URL."""
    try:
        resp = scraper.get(page_url, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'lxml')

        # EH puts the image in an img tag with id="img"
        img = soup.select_one('#img')
        if img and img.get('src'):
            return img['src']

        # Fallback: any large image
        img = soup.select_one('img[src*="e-hentai.org"], img[src*="exhentai.org"]')
        if img and img.get('src'):
            return img['src']

    except Exception as e:
        logger.error(f"Failed to fetch page {page_url}: {e}")

    return None


def search_eh(tags: str, max_results: int = 10) -> list[dict]:
    """Search e-hentai by tags. Returns [{url, title, tags, total_pages}, ...]."""
    scraper = cloudscraper.create_scraper()
    query = '+'.join(tags.strip().split())
    search_url = f"https://e-hentai.org/?f_search={query}"
    try:
        resp = scraper.get(search_url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"EH search failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, 'lxml')
    results = []
    seen = set()
    # EH search results use table.itg.gltc with .glink anchor elements
    glink_anchors = soup.select('.glink a[href*=\"/g/\"]')
    if not glink_anchors:
        # Fallback: any gallery link in the gallery table
        for a_tag in soup.select('table.itg.gltc a[href*=\"/g/\"]'):
            glink_anchors.append(a_tag)
    for a in glink_anchors:
        href = urljoin("https://e-hentai.org", a['href'])
        if not is_eh_link(href) or href in seen:
            continue
        seen.add(href)
        meta = scrape_metadata(href)
        if meta.get('error'):
            continue
        results.append({
            'url': href,
            'title': meta.get('title', ''),
            'tags': meta.get('tags', []),
            'total_pages': meta.get('total_pages', 0),
        })
        if len(results) >= max_results:
            break
    return results


def scrape_metadata(url: str) -> dict:
    """Lightweight metadata-only scrape (no image download). Returns {title, tags, category, total_pages, error}"""
    parsed = parse_gallery_url(url)
    if not parsed:
        return {"error": "Invalid EH gallery URL", "title": ""}

    scraper = cloudscraper.create_scraper()
    meta = fetch_gallery_metadata(scraper, parsed['gid'], parsed['gallery_token'])

    if not meta:
        return {"error": "Failed to fetch gallery metadata", "title": ""}

    title = meta.get('title', '') or f"EH Gallery {parsed['gid']}"
    filecount = int(meta.get('filecount', 0))
    category = meta.get('category', '')

    tags = meta.get('tags', [])
    flat_tags = []
    if tags and isinstance(tags, list):
        for t in tags:
            if isinstance(t, list):
                flat_tags.extend(str(x) for x in t)
            else:
                flat_tags.append(str(t))

    return {
        "title": title,
        "tags": flat_tags,
        "category": category,
        "total_pages": filecount,
        "error": None
    }


def scrape_gallery(url: str, max_workers: int = 5, max_pages: int = 0) -> dict:
    """
    Full scrape: parse URL -> get metadata -> get page URLs -> get image URLs.
    Returns {
        'title': str,
        'pages': int,
        'image_urls': [str, ...],
        'error': str | None
    }
    """
    parsed = parse_gallery_url(url)
    if not parsed:
        return {"error": "Invalid EH gallery URL", "image_urls": []}

    scraper = cloudscraper.create_scraper()

    # Get metadata
    meta = fetch_gallery_metadata(scraper, parsed['gid'], parsed['gallery_token'])
    title = (meta.get('title', '') if meta else '') or f"EH Gallery {parsed['gid']}"
    filecount = int(meta.get('filecount', 0)) if meta else 0
    if max_pages > 0:
        filecount = min(filecount, max_pages) if filecount else 0

    # Get page URLs
    gallery_url = f"https://e-hentai.org/g/{parsed['gid']}/{parsed['gallery_token']}/"
    page_urls = fetch_page_urls(scraper, gallery_url, max_pages)
    if not page_urls:
        return {"error": "Could not find any pages in gallery", "image_urls": [], "title": title}

    # Fetch image URLs in parallel
    image_urls = [None] * len(page_urls)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(fetch_image_url, scraper, url): i
            for i, url in enumerate(page_urls)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                img_url = future.result()
                if img_url:
                    image_urls[idx] = img_url
                else:
                    # Retry once with delay
                    time.sleep(1)
                    img_url = fetch_image_url(scraper, page_urls[idx])
                    if img_url:
                        image_urls[idx] = img_url
            except Exception as e:
                logger.error(f"Fetch error for page {idx}: {e}")

    # Filter out None
    image_urls = [u for u in image_urls if u]
    logger.info(f"Successfully fetched {len(image_urls)}/{len(page_urls)} image URLs")

    # Extract tags and category from metadata
    tags = meta.get('tags', []) if meta else []
    category = meta.get('category', '') if meta else ''
    # Flatten tag list - EH returns tags as nested arrays
    flat_tags = []
    if tags and isinstance(tags, list):
        for t in tags:
            if isinstance(t, list):
                flat_tags.extend(str(x) for x in t)
            else:
                flat_tags.append(str(t))

    return {
        "title": title,
        "pages": len(image_urls),
        "total_pages": filecount or len(page_urls),
        "image_urls": image_urls,
        "tags": flat_tags,
        "category": category,
        "error": None
    }
