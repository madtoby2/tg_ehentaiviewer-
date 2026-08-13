"""
JMComic/18comic Telegraph publisher with image descrambling + Litterbox hosting.
Fixed: retries, skip broken images, pagination.
"""
import os
import time
import uuid
import shutil
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from jmcomic import JmImageTool

from publishers.telegraph import create_page

logger = logging.getLogger(__name__)

# ── Shared retry/session helpers ──────────────────────────────────────────

_RETRY_TOTAL = int(os.environ.get('EHBOT_DOWNLOAD_RETRIES', '3'))
_RETRY_BACKOFF = float(os.environ.get('EHBOT_DOWNLOAD_BACKOFF', '1'))
_RETRY_STATUSES = {429, 500, 502, 503, 504}

def _build_retry_session(pool_maxsize: int = 8) -> requests.Session:
    """Create a requests.Session with connection pooling and retry-on-failure.

    Retry covers: connection errors, read timeouts, SSL errors, and
    server-side 5xx/429 responses.  Shared across a single gallery so
    TCP connections are reused and CLOSE-WAIT leaks are avoided.
    """
    retry = Retry(
        total=_RETRY_TOTAL,
        backoff_factor=_RETRY_BACKOFF,
        status_forcelist=list(_RETRY_STATUSES),
        allowed_methods=frozenset({'GET', 'POST'}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        pool_connections=pool_maxsize,
        pool_maxsize=pool_maxsize,
        max_retries=retry,
    )
    s = requests.Session()
    s.mount('https://', adapter)
    s.mount('http://', adapter)
    return s

TMP_ROOT = Path(os.environ.get('EHBOT_TMP_ROOT', '/tmp/ehreader-jm'))
CATBOX_API_URL = os.environ.get(
    'EHBOT_CATBOX_API_URL',
    'https://catbox.moe/user/api.php',
)
CATBOX_USERHASH = os.environ.get('EHBOT_CATBOX_USERHASH', '').strip()
LITTERBOX_API_URL = os.environ.get(
    'EHBOT_LITTERBOX_API_URL',
    'https://litterbox.catbox.moe/resources/internals/api.php',
)
LITTERBOX_EXPIRE = os.environ.get('EHBOT_LITTERBOX_EXPIRE', '72h')
# catbox = permanent third-party hosting, no server storage after upload.
# litterbox = temporary 1h/12h/24h/72h fallback.
# local_static = save decoded images under a local HTTPS static directory and embed those URLs in Telegraph.
IMAGE_HOST = os.environ.get('EHBOT_IMAGE_HOST', 'catbox').strip().lower()
STATIC_ROOT = Path(os.environ.get('EHBOT_STATIC_IMAGE_ROOT', '/var/www/ehreader-images'))
STATIC_BASE_URL = os.environ.get('EHBOT_STATIC_IMAGE_BASE_URL', 'https://107.175.69.137.sslip.io/ehimg').rstrip('/')
STATIC_TTL_SECONDS = int(os.environ.get('EHBOT_STATIC_IMAGE_TTL_SECONDS', str(7 * 24 * 3600)))
DOWNLOAD_TIMEOUT = int(os.environ.get('EHBOT_IMAGE_DOWNLOAD_TIMEOUT', '60'))
UPLOAD_TIMEOUT = int(os.environ.get('EHBOT_IMAGE_UPLOAD_TIMEOUT', '120'))
MAX_WORKERS = int(os.environ.get('EHBOT_IMAGE_MAX_WORKERS', '4'))
PUBLISH_TIMEOUT = int(os.environ.get('EHBOT_PUBLISH_TIMEOUT', '300'))
DELAY_BETWEEN_PAGES = 3
MAX_PAGES_PER_TELEGRAPH = 200  # Max images per Telegraph page (soft limit ~700)


def _collect_completed_results(jobs, max_workers: int, timeout: float):
    """Run indexed callables and never return while a worker is still running.

    Pending jobs are cancelled at the deadline. Already-running jobs are allowed
    to finish before callers close the shared Session or remove temporary files.
    """
    results = {}
    timed_out = False
    executor = ThreadPoolExecutor(max_workers=max_workers)
    future_map = {executor.submit(job): idx for idx, job in jobs}
    try:
        for future in as_completed(future_map, timeout=timeout):
            idx = future_map[future]
            value = future.result()
            if value:
                results[idx] = value
    except TimeoutError:
        timed_out = True
        for future in future_map:
            future.cancel()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return results, timed_out


def _release_process_memory() -> None:
    """Return completed gallery allocations to the OS on long-lived workers."""
    import ctypes
    import gc

    gc.collect()
    try:
        ctypes.CDLL(None).malloc_trim(0)
    except (AttributeError, OSError):
        pass


def cleanup_tmp() -> int:
    if not TMP_ROOT.exists():
        return 0
    removed = 0
    now = time.time()
    for child in TMP_ROOT.iterdir():
        if not child.is_dir():
            continue
        try:
            if now - child.stat().st_mtime > 6 * 3600:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except Exception as e:
            logger.warning(f"Failed to cleanup {child}: {e}")
    return removed


def cleanup_static() -> int:
    """Remove old locally-hosted decoded image directories."""
    if not STATIC_ROOT.exists():
        return 0
    removed = 0
    now = time.time()
    for child in STATIC_ROOT.iterdir():
        if not child.is_dir():
            continue
        try:
            if now - child.stat().st_mtime > STATIC_TTL_SECONDS:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except Exception as e:
            logger.warning(f"Failed to cleanup static images {child}: {e}")
    return removed


def _publish_local_static(path: Path, gallery_id: str) -> str | None:
    """Copy a decoded image into the HTTPS static directory and return its public URL."""
    try:
        target_dir = STATIC_ROOT / gallery_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        shutil.copy2(path, target)
        try:
            target.chmod(0o644)
        except OSError:
            pass
        return f"{STATIC_BASE_URL}/{gallery_id}/{path.name}"
    except Exception as e:
        logger.warning(f"Local static publish failed for {path}: {e}")
        return None


def _image_filename(index: int) -> str:
    return f"p{index + 1:04d}.jpg"


def _post_file(path: Path, url: str, data: dict, service_name: str, session: requests.Session | None = None) -> str | None:
    s = session or _build_retry_session(pool_maxsize=4)
    with open(path, 'rb') as f:
        resp = s.post(
            url,
            data=data,
            files={'fileToUpload': (path.name, f, 'image/jpeg')},
            timeout=UPLOAD_TIMEOUT,
            headers={'User-Agent': 'Mozilla/5.0'},
        )
    body = resp.text.strip()
    if resp.status_code == 200 and body.startswith('https://'):
        return body
    logger.warning(f"{service_name} failed: status={resp.status_code}, body={body[:120]}")
    return None


def _upload_to_catbox(path: Path, retries=3, session: requests.Session | None = None) -> str | None:
    """Upload to Catbox permanent hosting."""
    s = session or _build_retry_session(pool_maxsize=4)
    data = {'reqtype': 'fileupload'}
    if CATBOX_USERHASH:
        data['userhash'] = CATBOX_USERHASH
    for attempt in range(1, retries + 1):
        try:
            uploaded = _post_file(path, CATBOX_API_URL, data, 'Catbox', session=s)
            if uploaded:
                return uploaded
        except Exception as e:
            logger.warning(f"Catbox attempt {attempt}/{retries} exception: {e}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    logger.error(f"Catbox upload failed after {retries} retries: {path.name}")
    return None


def _upload_to_litterbox(path: Path, retries=3, session: requests.Session | None = None) -> str | None:
    """Upload to temporary Litterbox hosting."""
    s = session or _build_retry_session(pool_maxsize=4)
    for attempt in range(1, retries + 1):
        try:
            uploaded = _post_file(
                path,
                LITTERBOX_API_URL,
                {'reqtype': 'fileupload', 'time': LITTERBOX_EXPIRE},
                'Litterbox',
                session=s,
            )
            if uploaded:
                return uploaded
        except Exception as e:
            logger.warning(f"Litterbox attempt {attempt}/{retries} exception: {e}")
        if attempt < retries:
            time.sleep(2 ** attempt)  # exponential backoff: 2s, 4s, 8s
    logger.error(f"Litterbox upload failed after {retries} retries: {path.name}")
    return None


def _upload_image_host(path: Path, session: requests.Session | None = None) -> str | None:
    """Upload/publish to configured image host. Default is Catbox; local_static uses this VPS."""
    if IMAGE_HOST in ('local_static', 'static', 'local'):
        return _publish_local_static(path, path.parent.name)

    if IMAGE_HOST == 'litterbox':
        return _upload_to_litterbox(path, session=session)

    uploaded = _upload_to_catbox(path, session=session)
    if uploaded:
        return uploaded

    logger.warning("Catbox unavailable; falling back to Litterbox temporary hosting")
    return _upload_to_litterbox(path, session=session)


def _download_decode_upload_one(img_detail, gallery_dir: Path, index: int,
                                 session: requests.Session | None = None) -> str | None:
    """Download one JM image, descramble, upload to Litterbox, delete local files.
    Returns uploaded URL, or None to skip the image entirely (avoid broken scrambled URLs)."""
    s = session or _build_retry_session()
    url = img_detail.download_url
    filename = _image_filename(index)
    raw_path = gallery_dir / f".{filename}.raw"
    decoded_path = gallery_dir / filename

    response = None
    source_image = None
    decoded_image = None
    try:
        response = s.get(
            url,
            timeout=DOWNLOAD_TIMEOUT,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36',
                'Referer': 'https://18comic.vip/',
                'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            },
        )
        response.raise_for_status()
        content = response.content
        if len(content) < 1000:
            logger.warning(f"JM image too small ({len(content)} bytes): {url}")
            return None  # Skip, don't include broken URL

        raw_path.write_bytes(content)
        source_image = JmImageTool.open_image(content)
        decoded_image = source_image.convert('RGB')
        num = JmImageTool.get_num_by_detail(img_detail)
        JmImageTool.decode_and_save(num, decoded_image, str(decoded_path))

        uploaded = _upload_image_host(decoded_path, session=s)
        if uploaded:
            return uploaded
        logger.warning(f"Image host upload failed for image {index + 1} after retries, SKIPPING")
        return None  # Skip rather than include scrambled URL
    except Exception as e:
        logger.warning(f"Failed to decode/upload JM image {index + 1}: {e}, SKIPPING")
        return None
    finally:
        for image in (decoded_image, source_image):
            try:
                if image is not None:
                    image.close()
            except Exception:
                pass
        try:
            if response is not None:
                response.close()
        except Exception:
            pass
        for p in (raw_path, decoded_path):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


def publish_jm_gallery(title: str, image_details: list, max_workers: int = MAX_WORKERS) -> dict:
    """Decode JM images → upload to Litterbox → create Telegraph page(s).
    Returns {'url', 'uploaded', 'total', 'error'}.
    Multiple Telegraph pages are joined with newlines."""
    if not image_details:
        return {"url": None, "uploaded": 0, "total": 0, "error": "No JM images to publish"}

    cleanup_tmp()
    if IMAGE_HOST in ('local_static', 'static', 'local'):
        cleanup_static()
    gallery_id = uuid.uuid4().hex[:10]
    gallery_dir = TMP_ROOT / gallery_id
    gallery_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Decoding {len(image_details)} JM images, uploading to {IMAGE_HOST or 'catbox'}, temp={gallery_dir}")
    results: dict[int, str] = {}
    session = _build_retry_session(pool_maxsize=max_workers * 2)
    try:
        jobs = [
            (i, lambda img=img, i=i: _download_decode_upload_one(img, gallery_dir, i, session))
            for i, img in enumerate(image_details)
        ]
        results, timed_out = _collect_completed_results(jobs, max_workers, PUBLISH_TIMEOUT)
        if timed_out:
            logger.warning(f"JM publish timed out after {PUBLISH_TIMEOUT}s")

        if not results:
            return {"url": None, "uploaded": 0, "total": len(image_details), "error": "Failed to upload any decoded JM images"}

        image_urls = [results[i] for i in sorted(results)]
        page_url = create_page(title, image_urls)
        if not page_url:
            return {
                "url": None,
                "uploaded": len(image_urls),
                "total": len(image_details),
                "error": "Failed to create Telegraph page after Litterbox upload",
            }

        return {"url": page_url, "uploaded": len(image_urls), "total": len(image_details), "error": None}
    except Exception as e:
        logger.exception("JM publish failed")
        return {"url": None, "uploaded": 0, "total": len(image_details), "error": str(e)[:200]}
    finally:
        session.close()
        shutil.rmtree(gallery_dir, ignore_errors=True)
        _release_process_memory()


def _download_upload_one(image_url: str, gallery_dir: Path, index: int,
                          session: requests.Session | None = None) -> str | None:
    """Download one EH image and upload to image host. Returns uploaded URL or None."""
    s = session or _build_retry_session()
    filename = _image_filename(index)
    local_path = gallery_dir / filename

    try:
        resp = s.get(
            image_url,
            timeout=DOWNLOAD_TIMEOUT,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36',
                'Referer': 'https://e-hentai.org/',
                'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            },
        )
        resp.raise_for_status()
        content = resp.content
        if len(content) < 1000:
            logger.warning(f"EH image too small ({len(content)} bytes): {image_url[:60]}")
            return None
        local_path.write_bytes(content)
        uploaded = _upload_image_host(local_path, session=s)
        if uploaded:
            return uploaded
        logger.warning(f"Image host upload failed for EH image {index + 1}, SKIPPING")
        return None
    except Exception as e:
        logger.warning(f"Failed to download/upload EH image {index + 1}: {e}, SKIPPING")
        return None
    finally:
        try:
            local_path.unlink(missing_ok=True)
        except Exception:
            pass


def publish_eh_gallery(title: str, image_urls: list[str], max_workers: int = MAX_WORKERS) -> dict:
    """Download EH images → upload to Litterbox → create Telegraph page(s).
    Returns {'url', 'uploaded', 'total', 'error'}.
    Multiple Telegraph pages are joined with newlines."""
    if not image_urls:
        return {"url": None, "uploaded": 0, "total": 0, "error": "No EH images to publish"}

    cleanup_tmp()
    if IMAGE_HOST in ('local_static', 'static', 'local'):
        cleanup_static()
    gallery_id = uuid.uuid4().hex[:10]
    gallery_dir = TMP_ROOT / gallery_id
    gallery_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading {len(image_urls)} EH images, uploading to {IMAGE_HOST or 'catbox'}, temp={gallery_dir}")
    results: dict[int, str] = {}
    session = _build_retry_session(pool_maxsize=max_workers * 2)
    try:
        jobs = [
            (i, lambda url=url, i=i: _download_upload_one(url, gallery_dir, i, session))
            for i, url in enumerate(image_urls)
        ]
        results, timed_out = _collect_completed_results(jobs, max_workers, PUBLISH_TIMEOUT)
        if timed_out:
            logger.warning(f"EH publish timed out after {PUBLISH_TIMEOUT}s")

        if not results:
            return {"url": None, "uploaded": 0, "total": len(image_urls), "error": "Failed to upload any EH images"}

        ordered_urls = [results[i] for i in sorted(results)]
        page_url = create_page(title, ordered_urls)
        if not page_url:
            return {
                "url": None,
                "uploaded": len(ordered_urls),
                "total": len(image_urls),
                "error": "Failed to create Telegraph page after Litterbox upload",
            }

        return {"url": page_url, "uploaded": len(ordered_urls), "total": len(image_urls), "error": None}
    except Exception as e:
        logger.exception("EH publish failed")
        return {"url": None, "uploaded": 0, "total": len(image_urls), "error": str(e)[:200]}
    finally:
        session.close()
        shutil.rmtree(gallery_dir, ignore_errors=True)
        _release_process_memory()
