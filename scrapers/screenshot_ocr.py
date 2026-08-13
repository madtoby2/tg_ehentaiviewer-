"""Local screenshot OCR and AV product-code extraction."""
import logging
import re
import subprocess

logger = logging.getLogger(__name__)
# Common AV identifiers: SSIS-123, IPX456, ABP-001 etc.
# Tesseract often confuses I/S/O inside the alphabetic prefix with 1/5/0.
_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2,6})[\s_-]?(\d{2,5})(?![A-Z0-9])", re.I)
_CONFUSED_RE = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z015]{2,5})[\s_-]+(\d{2,5})(?![A-Z0-9])", re.I)


def _normalize_prefix(prefix: str) -> str:
    return prefix.upper().translate(str.maketrans({'0': 'O', '1': 'I', '5': 'S'}))


def ocr(image_path: str) -> str:
    try:
        done = subprocess.run(
            ["tesseract", image_path, "stdout", "-l", "eng+chi_sim", "--psm", "11"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return done.stdout.strip() if done.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        logger.warning("OCR killed after 20s")
        return ""
    except Exception as e:
        logger.warning("OCR failed: %s", e)
        return ""


def extract_av_codes(text: str) -> list[str]:
    seen = set()
    out = []
    # Consume confused-prefix matches first and remove them so the fallback
    # regex cannot mis-split SS15-123 as SS-15.
    remaining = text or ""
    spans = []
    for m in _CONFUSED_RE.finditer(remaining):
        code = f"{_normalize_prefix(m.group(1))}-{m.group(2)}"
        if code not in seen:
            seen.add(code)
            out.append(code)
        spans.append(m.span())
    if spans:
        chars = list(remaining)
        for start, end in spans:
            chars[start:end] = ' ' * (end - start)
        remaining = ''.join(chars)
    for prefix, digits in _CODE_RE.findall(remaining):
        code = f"{prefix.upper()}-{digits}"
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out
