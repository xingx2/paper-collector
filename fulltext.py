"""Full-text acquisition: download an open-access PDF for a paper and extract
its text, so the deep-analysis LLM call reads the *paper*, not just the abstract.

Source priority (most-open first):
  1. arXiv preprint (arxiv_id from enrichment)  -> https://arxiv.org/pdf/<id>
  2. Semantic Scholar openAccessPdf url
  3. NDSS open-access PDF (derive from the ndss-symposium.org paper page)
  4. USENIX open-access PDF (derive from the usenix.org presentation page)

IEEE S&P (ieeexplore) and ACM CCS (dl.acm.org) are paywalled, so for many of
those we fall back to the abstract. Extracted text is cached on disk and capped
to a character budget to keep prompt size sane.
"""
import io
import os
import re
import time

import requests

import config

UA = {"User-Agent": "Mozilla/5.0 (agent-sec-paper-collector research)"}
MAX_CHARS = 48000  # ~ first ~20 pages of text; plenty for problem/method/eval
TEXT_DIR = os.path.join(config.CACHE_DIR, "fulltext")   # extracted text
PDF_DIR = os.path.join(config.CACHE_DIR, "pdf")          # archived original PDFs
os.makedirs(TEXT_DIR, exist_ok=True)
os.makedirs(PDF_DIR, exist_ok=True)


def _safe(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s or "unknown")


MAX_PDF_BYTES = 60 * 1024 * 1024   # 60 MB cap
MAX_DL_SECONDS = 60                # hard wall-clock cap for a single download


def _download(url):
    """Stream a PDF with hard caps on total time and size (requests' timeout
    only bounds gaps between bytes, not the full transfer — a slow trickle can
    otherwise hang for many minutes)."""
    import time as _t
    try:
        with requests.get(url, headers=UA, timeout=(10, 30), allow_redirects=True,
                          stream=True) as r:
            if r.status_code != 200:
                return None
            start = _t.monotonic()
            buf = bytearray()
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > MAX_PDF_BYTES:
                    return None
                if _t.monotonic() - start > MAX_DL_SECONDS:
                    return None
            data = bytes(buf)
            return data if data[:4] == b"%PDF" else None
    except Exception:  # noqa: BLE001
        return None


def _extract(pdf_bytes):
    import pypdf
    try:
        rd = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception:  # noqa: BLE001
        return None
    chunks, total = [], 0
    for page in rd.pages:
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        chunks.append(t)
        total += len(t)
        if total >= MAX_CHARS:
            break
    text = re.sub(r"[ \t]+", " ", "\n".join(chunks)).strip()
    if not text:
        return None
    # PDF text extraction can emit lone surrogates that break UTF-8 encoding
    # (json.dump, file writes). Strip them.
    text = text.encode("utf-8", "ignore").decode("utf-8", "ignore")
    return text[:MAX_CHARS]


def _candidate_urls(paper, enr):
    urls = []
    arxiv = (enr or {}).get("arxiv_id")
    if arxiv:
        aid = arxiv.split("v")[0]
        urls.append(f"https://arxiv.org/pdf/{arxiv}")
        urls.append(f"https://arxiv.org/pdf/{aid}")
    if (enr or {}).get("pdf_url"):
        urls.append(enr["pdf_url"])
    ee = paper.get("ee") or ""
    # NDSS open access: paper page -> attached PDF lives under /wp-content/uploads
    if "ndss-symposium.org/ndss-paper/" in ee:
        urls.append(("__ndss__", ee))
    # USENIX open access: presentation page -> *.pdf
    if "usenix.org/conference/" in ee:
        urls.append(("__usenix__", ee))
    return urls


def _resolve_html_pdf(kind, page_url):
    """Scrape a venue paper page for the actual PDF link."""
    try:
        r = requests.get(page_url, headers=UA, timeout=60)
        if r.status_code != 200:
            return None
        html = r.text
    except Exception:  # noqa: BLE001
        return None
    pats = [
        r'href="(https?://[^"]+?\.pdf)"',
        r'href="(/[^"]+?\.pdf)"',
        r'content="(https?://[^"]+?\.pdf)"',
    ]
    for pat in pats:
        m = re.search(pat, html)
        if m:
            u = m.group(1)
            if u.startswith("/"):
                base = re.match(r"(https?://[^/]+)", page_url).group(1)
                u = base + u
            return u
    return None


def get_fulltext(paper, enr):
    """Return (text or None, source_url or None). Cached."""
    cache_path = os.path.join(TEXT_DIR, f"{_safe(paper['key'])}.txt")
    meta_path = cache_path + ".src"
    pdf_path = os.path.join(PDF_DIR, f"{_safe(paper['key'])}.pdf")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            text = fh.read()
        src = open(meta_path).read().strip() if os.path.exists(meta_path) else None
        return (text or None), src

    text, used = None, None
    for entry in _candidate_urls(paper, enr):
        if isinstance(entry, tuple):
            kind, page = entry
            real = _resolve_html_pdf(kind, page)
            if not real:
                continue
            pdf = _download(real)
            url = real
        else:
            pdf = _download(entry)
            url = entry
        if pdf:
            text = _extract(pdf)
            if text and len(text) > 1500:  # got a real body, not a stub
                used = url
                with open(pdf_path, "wb") as fh:  # archive original PDF
                    fh.write(pdf)
                break
        time.sleep(0.5)

    # cache (even empty, to avoid re-fetching misses)
    with open(cache_path, "w", encoding="utf-8") as fh:
        fh.write(text or "")
    with open(meta_path, "w", encoding="utf-8") as fh:
        fh.write(used or "")
    return (text or None), used
