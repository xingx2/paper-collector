"""Candidate filtering + abstract enrichment.

Stage 1 (keyword prefilter): cheap, recall-oriented. Flags any title that could
plausibly concern AI agents / LLMs so the expensive LLM stage only sees a small
fraction of the ~1500 papers.

Stage 2 (enrichment): DBLP has no abstracts, so we fetch them from Semantic
Scholar (by DOI when available, else by title match) and arXiv as a fallback.
The abstract + any GitHub link found feeds the LLM analysis stage.
"""
import json
import os
import re
import time
import urllib.parse

import requests

import config

S2_BY_DOI = "https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search/match"
S2_FIELDS = "title,abstract,openAccessPdf,externalIds,url"
ARXIV_API = "http://export.arxiv.org/api/query"
GITHUB_RE = re.compile(r"https?://github\.com/[\w.\-]+/[\w.\-]+", re.I)
UA = {"User-Agent": "agent-sec-paper-collector/1.0 (research)"}


# --------------------------------------------------------------------------- #
# Stage 1: keyword prefilter
# --------------------------------------------------------------------------- #
def keyword_hits(title):
    t = title.lower()
    hits = []
    for kw in config.AGENT_KEYWORDS:
        # use word-ish boundaries so "agent" doesn't fire inside "agentic"? we
        # actually WANT agentic too, so plain substring is fine here.
        if kw in t:
            hits.append(kw)
    return hits


def is_candidate(paper):
    hits = keyword_hits(paper["title"])
    if not hits:
        return False, [], "none"
    strong = [h for h in hits if h not in config.WEAK_ALONE]
    priority = "high" if strong else "low"
    return True, hits, priority


def select_candidates(by_venue_year):
    """Return flat list of candidate papers, each annotated with kw hits."""
    cands = []
    for (_venue, _year), papers in by_venue_year.items():
        for p in papers:
            ok, hits, priority = is_candidate(p)
            if ok:
                q = dict(p)
                q["kw_hits"] = hits
                q["kw_priority"] = priority
                cands.append(q)
    return cands


# --------------------------------------------------------------------------- #
# Stage 2: abstract enrichment
# --------------------------------------------------------------------------- #
def _s2_get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=40)
            if r.status_code == 429:
                time.sleep(4 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    return None


def _norm_title(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def fetch_semantic_scholar(paper):
    data = None
    if paper.get("doi"):
        data = _s2_get(S2_BY_DOI.format(doi=paper["doi"]), {"fields": S2_FIELDS})
    if not data:
        time.sleep(config.S2_DELAY)
        data = _s2_get(S2_SEARCH, {"query": paper["title"], "fields": S2_FIELDS})
        if data and "data" in data:
            data = data["data"][0] if data["data"] else None
    if not data:
        return None
    # guard against a wildly wrong title match
    if _norm_title(data.get("title")) and paper["title"]:
        a, b = _norm_title(data["title"]), _norm_title(paper["title"])
        if not (a in b or b in a or _token_overlap(a, b) >= 0.6):
            return None
    pdf = (data.get("openAccessPdf") or {}).get("url")
    return {
        "abstract": data.get("abstract"),
        "pdf_url": pdf,
        "s2_url": data.get("url"),
        "arxiv_id": (data.get("externalIds") or {}).get("ArXiv"),
    }


def _token_overlap(a, b):
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def fetch_arxiv(paper):
    q = urllib.parse.quote(f'ti:"{paper["title"]}"')
    try:
        r = requests.get(ARXIV_API, params={"search_query": q, "max_results": 1},
                         headers=UA, timeout=40)
        r.raise_for_status()
    except Exception:  # noqa: BLE001
        return None
    txt = r.text
    m = re.search(r"<summary>(.*?)</summary>", txt, re.S)
    tm = re.search(r"<title>(.*?)</title>", txt, re.S)
    if not m:
        return None
    cand_title = _norm_title(re.sub(r"\s+", " ", (tm.group(1) if tm else "")))
    if cand_title and _token_overlap(cand_title, _norm_title(paper["title"])) < 0.5:
        return None
    idm = re.search(r"<id>(http://arxiv.org/abs/[^<]+)</id>", txt)
    return {
        "abstract": re.sub(r"\s+", " ", m.group(1)).strip(),
        "pdf_url": idm.group(1).replace("/abs/", "/pdf/") if idm else None,
        "arxiv_id": idm.group(1).split("/abs/")[-1] if idm else None,
    }


def enrich(paper):
    """Attach abstract/pdf/github to a candidate (cached on disk)."""
    cache_path = os.path.join(config.CACHE_DIR, f"enrich_{_safe(paper['key'])}.json")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            return json.load(fh)

    enr = {"abstract": None, "pdf_url": None, "arxiv_id": None, "github": None,
           "source": None}
    s2 = fetch_semantic_scholar(paper)
    time.sleep(config.S2_DELAY)
    if s2 and s2.get("abstract"):
        enr.update(s2)
        enr["source"] = "semantic_scholar"
    else:
        ax = fetch_arxiv(paper)
        if ax and ax.get("abstract"):
            enr.update(ax)
            enr["source"] = "arxiv"
        elif s2:  # keep pdf/arxiv id even without abstract
            enr.update({k: v for k, v in s2.items() if v})
            enr["source"] = "semantic_scholar_partial"

    blob = " ".join(filter(None, [enr.get("abstract"), paper.get("ee"), enr.get("pdf_url")]))
    gh = GITHUB_RE.search(blob)
    if gh:
        enr["github"] = gh.group(0)

    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(enr, fh, ensure_ascii=False, indent=2)
    return enr


def _safe(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s or "unknown")
