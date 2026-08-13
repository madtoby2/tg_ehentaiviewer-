"""
18comic (18comic.vip) album scraper using the jmcomic library.
Uses curl_cffi postman to bypass Cloudflare on the API domains.
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 18comic / JMComic URL patterns
COMIC_DOMAINS = {
    '18comic.vip', '18comic.org', '18comic.me', '18comic.io',
    '18comic.biz', '18comic.art', 'jmcomic.me', 'jmcomic.com',
    '18comic1.me', '18comic2.me', 'jmcomic8.com',
}

COMIC_ALBUM_RE = re.compile(
    r'https?://(?:' + '|'.join(re.escape(d) for d in COMIC_DOMAINS) +
    r')/(?:album|photos/[a-z]+|photo)/(\d+)/?.*'
)

# jmcomic config for bypassing Cloudflare via curl_cffi
_JM_CONFIG = '''
client:
  domain: [www.cdnhjk.net, www.cdngwc.cc, www.cdngwc.net, www.cdngwc.club]
  postman:
    type: curl_cffi
    meta_data:
      impersonate: chrome
  impl: api
  retry_times: 5
'''


def is_comic_link(url: str) -> bool:
    """Check if a URL is a supported 18comic link."""
    return bool(COMIC_ALBUM_RE.match(url))


def parse_comic_url(url: str) -> Optional[dict]:
    """Parse comic URL into album_id."""
    m = COMIC_ALBUM_RE.match(url)
    if not m:
        return None
    return {"album_id": m.group(1)}


def _get_client():
    """Create a jmcomic API client with curl_cffi postman and optional cookies."""
    from jmcomic import create_option_by_str, JmModuleConfig
    import os, yaml

    # Suppress jmcomic's own logging (it's very verbose)
    import logging as py_logging
    py_logging.getLogger('jmcomic').setLevel(py_logging.WARNING)
    JmModuleConfig.DEFAULT_PROXIES = {}

    cookies = os.environ.get('EHBOT_JM_COOKIES', '').strip()
    client_config = yaml.safe_load(_JM_CONFIG)
    if cookies:
        client_config['client']['cookies'] = cookies

    option = create_option_by_str(yaml.dump(client_config))
    return option.new_jm_client()


def search_comic(tags: str, max_results: int = 10) -> list[dict]:
    """Search 18comic by tags. Returns [{url, title, tags, total_pages}, ...]."""
    try:
        client = _get_client()
        page = client.search_site(tags, page=1)
        content = getattr(page, 'content', [])
        if not content or not isinstance(content, (list, tuple)):
            return []
        entries = [c for c in content if isinstance(c, (list, tuple)) and len(c) >= 2]
        results = []
        seen = set()
        for entry in entries:
            aid = str(entry[0])
            if not (aid and aid.isdigit()) or aid in seen:
                continue
            seen.add(aid)
            # 过滤韩漫
            cat = entry[1] if isinstance(entry[1], dict) else {}
            entry_cat = cat.get('category', {})
            if isinstance(entry_cat, dict) and entry_cat.get('title', '') in ('韓漫', '韩漫'):
                continue
            cat_id = str(entry_cat.get('id', ''))
            if cat_id in ('5', '7'):
                continue
            url = f"https://18comic.vip/album/{int(aid)}/"
            meta = scrape_metadata(url)
            if meta.get('error'):
                continue
            results.append({
                'url': url,
                'title': meta.get('title', ''),
                'tags': meta.get('tags', []),
                'total_pages': meta.get('total_pages', 0),
            })
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        logger.warning(f"18comic search failed: {e}")
        return []


def scrape_metadata(url: str) -> dict:
    """Lightweight metadata-only scrape (no image download). Returns {title, tags, total_pages, error}"""
    parsed = parse_comic_url(url)
    if not parsed:
        return {"error": "Invalid 18comic URL", "title": ""}

    album_id = parsed['album_id']
    try:
        client = _get_client()
    except Exception as e:
        return {"error": f"Failed to create jmcomic client: {e}", "title": ""}

    try:
        album = client.get_album_detail(int(album_id))
        title = album.title or f"18comic #{album_id}"
        author = getattr(album, 'author', '')
        if author:
            title = f"{title} - {author}"
        # Page count: fetch first episode's photo detail (no image download)
        total_pages = 0
        try:
            ep_list = getattr(album, 'episode_list', None)
            if ep_list and len(ep_list) > 0:
                first_ep = ep_list[0]
                ep_id = first_ep[0] if isinstance(first_ep, tuple) else first_ep.id
                photo = client.get_photo_detail(ep_id)
                total_pages = len(photo)
        except Exception:
            pass
        album_tags = list(album.tags) if hasattr(album, 'tags') and album.tags else []
    except Exception as e:
        return {"error": f"Failed to fetch album detail: {e}", "title": f"18comic #{album_id}"}

    return {
        "title": title,
        "tags": album_tags,
        "total_pages": total_pages,
        "error": None
    }


def scrape_album(url: str, max_workers: int = 5, max_pages: int = 0) -> dict:
    """
    Full scrape: parse URL -> fetch album via jmcomic API -> get image URLs.
    Returns {
        'title': str,
        'pages': int,
        'image_urls': [str, ...],
        'error': str | None
    }
    """
    parsed = parse_comic_url(url)
    if not parsed:
        return {
            "error": "Invalid 18comic URL. Format: https://18comic.vip/album/12345/",
            "image_urls": []
        }

    album_id = parsed['album_id']

    try:
        client = _get_client()
    except Exception as e:
        return {
            "error": f"Failed to create jmcomic client: {e}",
            "image_urls": []
        }

    # Fetch album detail
    try:
        album = client.get_album_detail(int(album_id))
        title = album.title or f"18comic #{album_id}"
        author = getattr(album, 'author', '')
        if author:
            title = f"{title} - {author}"
    except Exception as e:
        logger.error(f"Failed to fetch album detail: {e}")
        return {
            "error": f"Failed to fetch album {album_id}: {e}",
            "image_urls": [],
            "title": f"18comic #{album_id}"
        }

    # Get all photos (episodes/chapters)
    try:
        photos = list(album)
    except Exception:
        photos = []

    if not photos:
        return {
            "error": f"No photos found in album {album_id}",
            "image_urls": [],
            "title": title
        }

    # Fetch image details from all photos. JM images may be scrambled; keep the
    # detail objects so the publisher can decode with JmImageTool.get_num_by_detail().
    all_urls = []
    image_details = []
    for photo in photos:
        try:
            detail = client.get_photo_detail(photo.id)
            for img in detail:
                image_details.append(img)
                all_urls.append(img.download_url)
                if max_pages > 0 and len(all_urls) >= max_pages:
                    break
        except Exception as e:
            logger.warning(f"Failed to fetch photo {photo.id}: {e}")
            continue
        if max_pages > 0 and len(all_urls) >= max_pages:
            all_urls = all_urls[:max_pages]
            image_details = image_details[:max_pages]
            break

    if not all_urls:
        return {
            "error": "Failed to fetch any image URLs",
            "image_urls": [],
            "image_details": [],
            "title": title
        }

    # Get album tags
    try:
        album_tags = list(album.tags) if hasattr(album, 'tags') and album.tags else []
    except Exception:
        album_tags = []

    logger.info(f"Fetched {len(all_urls)} image URLs for '{title}'")

    return {
        "title": title,
        "pages": len(all_urls),
        "total_pages": len(all_urls),
        "image_urls": all_urls,
        "image_details": image_details,
        "tags": album_tags,
        "error": None
    }
