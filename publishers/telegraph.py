"""
Telegraph page publisher.
Uploads images to Telegraph and creates a reading page.
"""
import io
import os
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import cloudscraper
import requests

logger = logging.getLogger(__name__)

TELEGRAPH_UPLOAD_URL = "https://telegra.ph/upload"
TELEGRAPH_API_URL = "https://api.telegra.ph"
TELEGRAPH_IMAGE_BASE = "https://telegra.ph"

# Max image size for Telegraph upload (5MB)
MAX_IMAGE_SIZE = 5 * 1024 * 1024
# Max images per page (Telegraph soft limit ~700, but we cap at 200 for speed)
MAX_IMAGES_PER_PAGE = 200
# Delay between uploads to avoid rate limiting
UPLOAD_DELAY = 1.0

# Telegraph access token storage. Overridable so cloned deployments can keep
# the token inside the project dir instead of ~/.hermes.
TOKEN_FILE = os.environ.get(
    "EHBOT_TELEGRAPH_TOKEN_FILE",
    os.path.expanduser("~/.hermes/telegraph_token.json"),
)


def load_token() -> str | None:
    """Load Telegraph access token from file."""
    try:
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE) as f:
                data = json.load(f)
                return data.get('access_token')
    except Exception as e:
        logger.warning(f"Failed to load token: {e}")
    return None


def save_token(token: str):
    """Save Telegraph access token to file."""
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, 'w') as f:
        json.dump({'access_token': token}, f)


def create_account() -> str:
    """Create a Telegraph account and return access token."""
    resp = requests.post(
        f"{TELEGRAPH_API_URL}/createAccount",
        json={
            "short_name": "EH Reader",
            "author_name": "EH Reader Bot"
        },
        timeout=15
    )
    data = resp.json()
    if data.get('ok') and data.get('result', {}).get('access_token'):
        token = data['result']['access_token']
        save_token(token)
        return token
    raise RuntimeError(f"Failed to create Telegraph account: {data}")


def ensure_account() -> str:
    """Get or create Telegraph access token."""
    token = load_token()
    if token:
        return token
    return create_account()


def upload_image(image_data: bytes, filename: str = "image.jpg") -> str | None:
    """
    Upload an image to Telegraph.
    Returns the full Telegraph image URL or None on failure.
    """
    if len(image_data) > MAX_IMAGE_SIZE:
        logger.warning(f"Image too large: {len(image_data)} bytes (max {MAX_IMAGE_SIZE})")
        return None

    try:
        resp = requests.post(
            TELEGRAPH_UPLOAD_URL,
            files={'file': (filename, io.BytesIO(image_data), 'image/jpeg')},
            timeout=60
        )
        data = resp.json()
        if isinstance(data, list) and len(data) > 0 and 'src' in data[0]:
            return f"{TELEGRAPH_IMAGE_BASE}{data[0]['src']}"
        elif isinstance(data, dict) and 'src' in data:
            return f"{TELEGRAPH_IMAGE_BASE}{data['src']}"
        else:
            logger.warning(f"Telegraph upload response: {data}")
            return None
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        return None


def upload_from_url(image_url: str, scraper: cloudscraper.CloudScraper | None = None, index: int = 0) -> str | None:
    """
    Download an image from URL and upload to Telegraph.
    Returns the Telegraph image URL or None on failure.
    """
    if scraper is None:
        scraper = cloudscraper.create_scraper()

    try:
        resp = scraper.get(image_url, timeout=60)
        resp.raise_for_status()
        content = resp.content

        if len(content) < 1000:  # Too small to be a real image
            logger.warning(f"Image too small ({len(content)} bytes) from {image_url}")
            return None

        ext = image_url.rsplit('.', 1)[-1].lower() if '.' in image_url else 'jpg'
        if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
            ext = 'jpg'

        filename = f"page_{index:04d}.{ext}"
        time.sleep(UPLOAD_DELAY)  # Rate limiting
        return upload_image(content, filename)

    except Exception as e:
        logger.error(f"Failed to download {image_url}: {e}")
        return None


def build_page_content(telegraph_urls: list[str]) -> list:
    """
    Build Telegraph Node content from image URLs.
    Each image is wrapped in a <figure> or standalone <img>.
    """
    content = []
    for img_url in telegraph_urls:
        content.append({
            "tag": "img",
            "attrs": {"src": img_url}
        })
        # Add a small break between images
        content.append({"tag": "br"})
    return content


def create_page(
    title: str,
    telegraph_urls: list[str],
    author_name: str = "EH Reader Bot"
) -> str | None:
    """
    Create a Telegraph page with the given images.
    Returns the page URL or None on failure.
    """
    token = ensure_account()
    content = build_page_content(telegraph_urls)

    # Split into multiple pages if too many images
    pages = []
    for i in range(0, len(content), MAX_IMAGES_PER_PAGE * 2):  # *2 because br tags are in between
        chunk = content[i:i + MAX_IMAGES_PER_PAGE * 2]
        if not chunk:
            break
        pages.append(chunk)

    page_urls = []
    for i, page_content in enumerate(pages):
        if len(pages) > 1:
            page_title = f"{title[:40]} ({i+1}/{len(pages)})"
        else:
            page_title = title[:80]

        # Retry with backoff on FLOOD_WAIT
        for attempt in range(5):
            try:
                resp = requests.post(
                    f"{TELEGRAPH_API_URL}/createPage",
                    json={
                        "access_token": token,
                        "title": page_title,
                        "author_name": author_name,
                        "content": page_content
                    },
                    timeout=30
                )
                data = resp.json()
                result_url = data.get('result', {}).get('url')
                result_path = data.get('result', {}).get('path')
                if data.get('ok') and (result_url or result_path):
                    page_url = result_url or f"https://telegra.ph/{result_path}"
                    page_urls.append(page_url)
                    logger.info(f"Created page: {page_url}")
                    break  # success
                elif 'FLOOD_WAIT' in str(data):
                    wait = min(2 ** attempt * 3, 60)
                    logger.warning(f"FLOOD_WAIT, waiting {wait}s (attempt {attempt+1})")
                    time.sleep(wait)
                else:
                    logger.error(f"Create page failed: {data}")
                    break
            except Exception as e:
                logger.error(f"Failed to create page: {e}")
                if attempt < 4:
                    time.sleep(3)
                else:
                    break

        # 2s delay between pages to avoid rate limiting
        if i < len(pages) - 1:
            time.sleep(3)

    if not page_urls:
        return None
    if len(page_urls) == 1:
        return page_urls[0]
    # Return newline-separated if multiple pages
    return "\n".join(page_urls)


def publish_gallery(
    title: str,
    image_urls: list[str],
    max_workers: int = 3,
    progress_callback=None
) -> dict:
    """
    Create a Telegraph reading page.

    Telegraph's /upload endpoint often returns 400 "Unknown error" on this VPS,
    so the default mode embeds the scraped source image URLs directly into the
    Telegraph page instead of uploading them first. Set EHBOT_TELEGRAPH_UPLOAD=1
    to force the old upload-then-create flow.

    Returns {
        'url': str | None,
        'uploaded': int,
        'total': int,
        'error': str | None
    }
    """
    if not image_urls:
        return {"url": None, "uploaded": 0, "total": 0, "error": "No images to publish"}

    if os.environ.get('EHBOT_TELEGRAPH_UPLOAD', '').lower() not in ('1', 'true', 'yes'):
        logger.info(f"Creating Telegraph page with {len(image_urls)} external image URLs...")
        page_url = create_page(title, image_urls)
        return {
            "url": page_url,
            "uploaded": len(image_urls) if page_url else 0,
            "total": len(image_urls),
            "error": None if page_url else "Failed to create Telegraph page"
        }

    # Legacy mode: upload images to Telegraph first.
    telegraph_urls = []
    scraper = cloudscraper.create_scraper()

    logger.info(f"Uploading {len(image_urls)} images to Telegraph...")

    executor = ThreadPoolExecutor(max_workers=max_workers)
    future_map = {
        executor.submit(upload_from_url, url, scraper, i): i
        for i, url in enumerate(image_urls)
    }
    completed = 0
    try:
        for future in as_completed(future_map, timeout=600):
            idx = future_map[future]
            completed += 1
            try:
                result = future.result()
                if result:
                    telegraph_urls.append((idx, result))
                else:
                    logger.warning(f"Image {idx} upload failed, fallback to source URL")
                    telegraph_urls.append((idx, image_urls[idx]))
            except Exception as e:
                logger.error(f"Upload error for image {idx}: {e}, fallback to source URL")
                telegraph_urls.append((idx, image_urls[idx]))

            if progress_callback:
                progress_callback(completed, len(image_urls))
    except TimeoutError:
        logger.warning(f"Telegraph upload timed out after 600s, falling back uncompleted images to source URLs")
        done_indices = {t[0] for t in telegraph_urls}
        for future, idx in future_map.items():
            if idx not in done_indices:
                telegraph_urls.append((idx, image_urls[idx]))
                logger.warning(f"Image {idx} timed out, using source URL")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    # Sort by original index to maintain order
    telegraph_urls.sort(key=lambda x: x[0])
    telegraph_urls = [url for _, url in telegraph_urls]

    if not telegraph_urls:
        return {"url": None, "uploaded": 0, "total": len(image_urls), "error": "Failed to upload any images"}

    # Create Telegraph page
    page_url = create_page(title, telegraph_urls)

    return {
        "url": page_url,
        "uploaded": len(telegraph_urls),
        "total": len(image_urls),
        "error": f"Failed to create page: upload ok ({len(telegraph_urls)}/{len(image_urls)})" if not page_url else None
    }
