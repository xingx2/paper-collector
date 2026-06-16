"""DBLP fetcher: pull the full table-of-contents for each venue/year.

DBLP exposes a venue's table of contents through the publication search API by
querying the magic prefix ``toc:db/<key><year>.bht:``. Results are paginated
(max 1000 per page via ``h``/``f``). We cache raw JSON per venue-year so re-runs
don't hammer DBLP.
"""
import json
import os
import time
import html

import requests

import config

DBLP_API = "https://dblp.org/search/publ/api"
PAGE = 1000
UA = {"User-Agent": "agent-sec-paper-collector/1.0 (research; contact: local)"}


def _get(params, retries=config.HTTP_RETRIES):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(DBLP_API, params=params, headers=UA, timeout=60)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - want to retry on any transport error
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"DBLP request failed after {retries} tries: {last}")


def _normalize_hit(hit, venue, year):
    info = hit.get("info", {})
    authors = info.get("authors", {}).get("author", [])
    if isinstance(authors, dict):
        authors = [authors]
    names = [html.unescape(a.get("text", "") if isinstance(a, dict) else str(a)) for a in authors]
    return {
        "dblp_id": hit.get("@id"),
        "key": info.get("key"),
        "title": html.unescape((info.get("title") or "").rstrip(". ").strip()),
        "authors": names,
        "venue": venue,
        "year": int(info.get("year", year)),
        "type": info.get("type"),
        "doi": info.get("doi"),
        "ee": info.get("ee"),          # publisher / official paper link
        "url": info.get("url"),        # dblp record
        "access": info.get("access"),  # 'open' / 'closed'
    }


def fetch_venue_year(venue, year, force=False):
    """Return list of normalized paper dicts for one venue-year (cached)."""
    key = config.VENUES[venue]
    cache_path = os.path.join(config.RAW_DIR, f"{venue}_{year}.json")
    if os.path.exists(cache_path) and not force:
        with open(cache_path, encoding="utf-8") as fh:
            return json.load(fh)

    toc = f"toc:db/{key}{year}.bht:"
    papers, first = [], 0
    while True:
        data = _get({"q": toc, "format": "json", "h": PAGE, "f": first})
        hits_blk = data.get("result", {}).get("hits", {})
        total = int(hits_blk.get("@total", 0))
        hits = hits_blk.get("hit", [])
        if isinstance(hits, dict):
            hits = [hits]
        for h in hits:
            papers.append(_normalize_hit(h, venue, year))
        first += len(hits)
        if not hits or first >= total:
            break
        time.sleep(config.DBLP_DELAY)

    papers = [p for p in papers if p["title"]]  # drop empty front-matter entries
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(papers, fh, ensure_ascii=False, indent=2)
    return papers


def fetch_all(force=False):
    """Fetch every configured venue-year. Returns {(venue, year): [papers]}."""
    out = {}
    for venue in config.VENUES:
        for year in config.YEARS:
            try:
                papers = fetch_venue_year(venue, year, force=force)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {venue} {year}: fetch error: {e}")
                papers = []
            out[(venue, year)] = papers
            print(f"  {venue} {year}: {len(papers)} papers")
            time.sleep(config.DBLP_DELAY)
    return out


if __name__ == "__main__":
    total = 0
    for (v, y), ps in fetch_all().items():
        total += len(ps)
    print(f"TOTAL: {total} papers across {len(config.VENUES)*len(config.YEARS)} venue-years")
