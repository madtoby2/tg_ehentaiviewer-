"""
Self-hosted image gallery reader.
Downloads images and creates a reading page served via HTTP.
"""
import os
import sys
import uuid
import json
import html
import logging
import subprocess
import threading
from urllib.parse import urlparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import cloudscraper

logger = logging.getLogger(__name__)

# Server config
READER_BASE_DIR = Path("/var/www/reader")
READER_PORT = 8080
READER_HOST = "0.0.0.0"
SERVER_URL = "http://107.175.69.137:8080"

# Image download settings
MAX_WORKERS = 8
DOWNLOAD_TIMEOUT = 120
MIN_IMAGE_SIZE = 2000


class ReaderServer:
    """Manages the HTTP server for reader pages."""
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def ensure_running(cls):
        """Start the HTTP server if not already running."""
        with cls._lock:
            if cls._instance and cls._instance.poll() is None:
                return cls._instance

            READER_BASE_DIR.mkdir(parents=True, exist_ok=True)

            proc = subprocess.Popen(
                [sys.executable, "-m", "http.server", str(READER_PORT),
                 "--bind", READER_HOST],
                cwd=str(READER_BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            cls._instance = proc
            logger.info(f"Reader HTTP server started on port {READER_PORT} (PID {proc.pid})")
            return proc


def _reader_html_template(title, page_count, images_json):
    """Generate the reader HTML page."""
    safe_title = html.escape(title, quote=True)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{safe_title}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    background: #111;
    color: #fff;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    overflow-x: hidden;
}}
#reader {{
    display: flex;
    flex-direction: column;
    align-items: center;
    min-height: 100vh;
}}
.page-img {{
    display: block;
    width: 100%;
    max-width: 1000px;
    height: auto;
}}
.loading {{
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 50vh;
    color: #666;
    font-size: 18px;
}}
#toolbar {{
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 100;
    background: rgba(0,0,0,0.85);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    padding: 8px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 14px;
    transform: translateY(-100%);
    transition: transform 0.3s ease;
}}
#toolbar.show {{ transform: translateY(0); }}
#toolbar .title {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    margin: 0 12px;
    color: #ccc;
}}
#toolbar .page-info {{
    color: #999;
    white-space: nowrap;
}}
#toolbar button {{
    background: #333;
    border: none;
    color: #fff;
    padding: 6px 14px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
}}
#toolbar button:hover {{ background: #555; }}
#progress-bar {{
    position: fixed;
    top: 0;
    left: 0;
    height: 3px;
    background: #4a9eff;
    z-index: 101;
    transition: width 0.3s ease;
}}
.tap-zone {{
    position: fixed;
    top: 0;
    bottom: 0;
    width: 33.33%;
    z-index: 10;
    cursor: pointer;
}}
.tap-zone.left {{ left: 0; }}
.tap-zone.right {{ right: 0; }}
@media (max-width: 600px) {{
    .tap-zone {{ width: 40%; }}
    #toolbar {{ font-size: 12px; padding: 6px 10px; }}
}}
</style>
</head>
<body>

<div id="progress-bar"></div>
<div id="toolbar">
    <span class="title">{safe_title}</span>
    <span class="page-info" id="pageInfo">1 / {page_count}</span>
    <button onclick="toggleReadingMode()" id="modeBtn">滚动</button>
</div>

<div class="tap-zone left" onclick="prevPage()"></div>
<div class="tap-zone right" onclick="nextPage()"></div>

<div id="reader"></div>

<script>
const IMAGES = {images_json};
const TOTAL = IMAGES.length;

function showToolbar(show) {{
    document.getElementById('toolbar').classList.toggle('show', show);
}}

function updateProgress() {{
    const scrolled = window.scrollY;
    const total = document.body.scrollHeight - window.innerHeight;
    const pct = total > 0 ? Math.min(100, (scrolled / total) * 100) : 0;
    document.getElementById('progress-bar').style.width = pct + '%';
}}

function updatePageInfo() {{
    const imgs = document.querySelectorAll('.page-img');
    let bestIdx = 0, bestDist = Infinity;
    const center = window.scrollY + window.innerHeight / 2;
    imgs.forEach((img, i) => {{
        const rect = img.getBoundingClientRect();
        const imgCenter = rect.top + window.scrollY + rect.height / 2;
        const dist = Math.abs(center - imgCenter);
        if (dist < bestDist) {{ bestDist = dist; bestIdx = i; }}
    }});
    document.getElementById('pageInfo').textContent = (bestIdx + 1) + ' / ' + TOTAL;
}}

let singleMode = false;
let currentIndex = 0;

function showSinglePage(index) {{
    const imgs = document.querySelectorAll('.page-img');
    if (!imgs.length) return;
    currentIndex = Math.max(0, Math.min(index, imgs.length - 1));
    imgs.forEach((img, i) => {{
        img.style.display = i === currentIndex ? 'block' : 'none';
    }});
    window.scrollTo({{ top: 0, behavior: 'smooth' }});
    document.getElementById('pageInfo').textContent = (currentIndex + 1) + ' / ' + TOTAL;
    document.getElementById('progress-bar').style.width = ((currentIndex + 1) / TOTAL * 100) + '%';
}}

function nextPage() {{
    if (singleMode) {{
        showSinglePage(currentIndex + 1);
    }} else {{
        window.scrollBy({{ top: window.innerHeight * 0.9, behavior: 'smooth' }});
        setTimeout(updatePageInfo, 250);
    }}
}}

function prevPage() {{
    if (singleMode) {{
        showSinglePage(currentIndex - 1);
    }} else {{
        window.scrollBy({{ top: -window.innerHeight * 0.9, behavior: 'smooth' }});
        setTimeout(updatePageInfo, 250);
    }}
}}

function toggleReadingMode() {{
    singleMode = !singleMode;
    document.getElementById('modeBtn').textContent = singleMode ? '单页' : '滚动';
    if (singleMode) {{
        updatePageInfo();
        const txt = document.getElementById('pageInfo').textContent.split('/')[0].trim();
        currentIndex = Math.max(0, (parseInt(txt, 10) || 1) - 1);
        showSinglePage(currentIndex);
    }} else {{
        document.querySelectorAll('.page-img').forEach(img => {{
            img.style.display = 'block';
        }});
        setTimeout(() => {{
            const imgs = document.querySelectorAll('.page-img');
            if (imgs[currentIndex]) imgs[currentIndex].scrollIntoView({{ behavior: 'smooth', block: 'start' }});
            updatePageInfo();
            updateProgress();
        }}, 50);
    }}
}}

document.addEventListener('mousemove', () => {{
    showToolbar(true);
    clearTimeout(window._tbTimer);
    window._tbTimer = setTimeout(() => showToolbar(false), 3000);
}});

document.addEventListener('scroll', () => {{
    updateProgress();
    updatePageInfo();
}});

setTimeout(() => {{
    showToolbar(true);
    setTimeout(() => showToolbar(false), 3000);
}}, 500);

document.addEventListener('keydown', (e) => {{
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') nextPage();
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') prevPage();
    if (e.key === 'm' || e.key === 'M') toggleReadingMode();
}});

const reader = document.getElementById('reader');
let loadedCount = 0;
IMAGES.forEach((src, i) => {{
    const img = document.createElement('img');
    img.className = 'page-img';
    img.src = src;
    img.alt = 'Page ' + (i + 1);
    img.loading = 'lazy';
    img.onload = () => {{
        loadedCount += 1;
        if (loadedCount === 1) updatePageInfo();
    }};
    img.onerror = () => {{
        img.alt = '图片加载失败：第 ' + (i + 1) + ' 页';
        img.style.minHeight = '40vh';
        img.style.padding = '40px 12px';
        img.style.color = '#f66';
        img.style.textAlign = 'center';
    }};
    reader.appendChild(img);
}});
</script>
</body>
</html>"""


def download_image(scraper, url: str, dest_stem: Path) -> str | None:
    """Download a single image. Returns the local filename on success, or the
    original URL as fallback on failure so the page can still render."""
    try:
        resp = scraper.get(url, timeout=DOWNLOAD_TIMEOUT)
        resp.raise_for_status()
        content = resp.content
        if len(content) < MIN_IMAGE_SIZE:
            logger.warning(f"Image too small ({len(content)} bytes): {url}, fallback to source URL")
            return url

        ct = resp.headers.get('content-type', '').lower()
        if not ct.startswith('image/'):
            logger.warning(f"Non-image response ({ct or 'no content-type'}): {url}, fallback to source URL")
            return url

        if 'png' in ct:
            ext = 'png'
        elif 'gif' in ct:
            ext = 'gif'
        elif 'webp' in ct:
            ext = 'webp'
        else:
            url_path = urlparse(url).path
            url_ext = url_path.rsplit('.', 1)[-1].lower() if '.' in url_path else ''
            ext = url_ext if url_ext in ('png', 'gif', 'webp', 'jpg', 'jpeg') else 'jpg'
        if ext == 'jpeg':
            ext = 'jpg'

        filename = f"{dest_stem.name}.{ext}"
        full_path = dest_stem.parent / filename
        with open(full_path, 'wb') as f:
            f.write(content)
        return filename
    except Exception as e:
        logger.warning(f"Download failed {url}: {e}, fallback to source URL")
        return url  # Fallback: return original URL so page still renders


def publish_gallery(title: str, image_urls: list[str], max_workers: int = 5) -> dict:
    """
    Download all images and create a reader page.
    Returns { 'url', 'downloaded', 'total', 'error' }
    """
    if not image_urls:
        return {"url": None, "downloaded": 0, "total": 0, "error": "No images"}

    gallery_id = str(uuid.uuid4())[:8]
    gallery_dir = READER_BASE_DIR / gallery_id
    gallery_dir.mkdir(parents=True, exist_ok=True)

    scraper = cloudscraper.create_scraper()
    scraper.headers.update({
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36',
    })

    # Download images
    logger.info(f"Downloading {len(image_urls)} images to {gallery_dir}")
    image_files = []

    executor = ThreadPoolExecutor(max_workers=max_workers)
    future_map = {}
    for i, url in enumerate(image_urls):
        dest_stem = gallery_dir / f"p{i+1:04d}"
        future_map[executor.submit(download_image, scraper, url, dest_stem)] = i

    results = {}
    try:
        for future in as_completed(future_map, timeout=600):
            i = future_map[future]
            try:
                fname = future.result()
                if fname:
                    results[i] = fname
            except Exception as e:
                logger.error(f"Download error for image {i}: {e}")
                if i < len(image_urls):
                    results[i] = image_urls[i]
    except TimeoutError:
        logger.warning(f"Reader download timed out after 600s, falling back uncompleted images to source URLs")
        for future, i in future_map.items():
            if i not in results and i < len(image_urls):
                results[i] = image_urls[i]
                logger.warning(f"Image {i} timed out, using source URL")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    # Sort by index to maintain order
    for i in sorted(results):
        image_files.append(results[i])

    downloaded = len(image_files)
    if downloaded == 0:
        import shutil
        shutil.rmtree(gallery_dir, ignore_errors=True)
        return {"url": None, "downloaded": 0, "total": len(image_urls),
                "error": "Failed to download any images"}

    # Generate reader HTML
    html = _reader_html_template(title, downloaded, json.dumps(image_files))
    with open(gallery_dir / "index.html", 'w', encoding='utf-8') as f:
        f.write(html)

    # Ensure HTTP server is running
    ReaderServer.ensure_running()

    reader_url = f"{SERVER_URL}/{gallery_id}/"
    logger.info(f"Reader page created: {reader_url} ({downloaded}/{len(image_urls)} images)")
    return {
        "url": reader_url,
        "downloaded": downloaded,
        "total": len(image_urls),
        "error": None
    }
