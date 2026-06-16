#!/usr/bin/env python3
"""End-to-end orchestrator for the AI-agent-security paper collector.

Pipeline:
    1. fetch     - pull DBLP TOCs for all venue-years (cached)
    2. filter    - keyword prefilter -> candidate set
    3. enrich    - fetch abstracts (Semantic Scholar / arXiv), detect code links
    4. analyze   - LLM gate + structured analysis per candidate
    5. report    - render report.md + dataset json

Everything is cached on disk, so the run is resumable and re-runs are cheap.

Usage:
    python3 main.py              # full run
    python3 main.py --limit 30   # only analyze first 30 candidates (testing)
    python3 main.py --refetch    # ignore DBLP cache
"""
import argparse
import sys
import time

import analyze
import config
import dblp_fetch
import enrich as enrich_mod
import fulltext as ft_mod
import report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="cap candidates analyzed (0=all)")
    ap.add_argument("--refetch", action="store_true", help="ignore DBLP cache")
    ap.add_argument("--high-only", action="store_true", help="only analyze high-priority candidates")
    args = ap.parse_args()

    t0 = time.time()
    print("=" * 70)
    print("AI-Agent-Security Paper Collector")
    print(f"Venues: {', '.join(config.VENUES)} | Years: {config.YEARS}")
    print(f"LLM: {config.LLM_MODEL} @ {config.LLM_BASE_URL}")
    print("=" * 70)

    # 1. fetch -------------------------------------------------------------
    print("\n[1/5] Fetching DBLP table-of-contents ...")
    by_vy = dblp_fetch.fetch_all(force=args.refetch)
    total = sum(len(v) for v in by_vy.values())

    # 2. filter ------------------------------------------------------------
    print("\n[2/5] Keyword prefilter ...")
    candidates = enrich_mod.select_candidates(by_vy)
    if args.high_only:
        candidates = [c for c in candidates if c["kw_priority"] == "high"]
    # analyze high-priority first
    candidates.sort(key=lambda c: 0 if c["kw_priority"] == "high" else 1)
    if args.limit:
        candidates = candidates[: args.limit]
    print(f"      {len(candidates)} candidates "
          f"({sum(1 for c in candidates if c['kw_priority']=='high')} high-priority)")

    # 3. enrich + gate -----------------------------------------------------
    print("\n[3/5] Enriching abstracts + relevance gate (abstract-based) ...")
    passed = []
    for i, cand in enumerate(candidates, 1):
        try:
            enr = enrich_mod.enrich(cand)
            g = analyze.gate(cand, enr)
        except Exception as e:  # noqa: BLE001
            print(f"   [{i}/{len(candidates)}] ! gate error: {e}")
            continue
        if g.get("is_agent_security"):
            cand["enrichment"] = enr
            cand["gate"] = g
            passed.append(cand)
            print(f"   [{i}/{len(candidates)}] ✓ {g.get('subcategory','?'):20} {cand['title'][:55]}")
    print(f"      gate passed: {len(passed)} papers")

    # 4. full-text deep analysis (Chinese) --------------------------------
    print("\n[4/5] Fetching full text + deep analysis (Chinese) ...")
    confirmed = []
    open_cnt = ft_cnt = 0
    for i, p in enumerate(passed, 1):
        try:
            text, src = ft_mod.get_fulltext(p, p["enrichment"])
            res = analyze.deep_analyze(p, p["enrichment"], text, src)
        except Exception as e:  # noqa: BLE001
            print(f"   [{i}/{len(passed)}] ! analyze error: {e}")
            continue
        basis = "全文" if res.get("evidence_basis") == "full_text" else "摘要"
        if res.get("evidence_basis") == "full_text":
            ft_cnt += 1
        p["analysis"] = res
        p["fulltext_source"] = src
        confirmed.append(p)
        if str(res.get("open_source")).lower() in ("true", "yes"):
            open_cnt += 1
        print(f"   [{i}/{len(passed)}] [{basis}] {p['title'][:58]}")
    print(f"      analyzed on full text: {ft_cnt}/{len(confirmed)}")

    # 5. report ------------------------------------------------------------
    print("\n[5/5] Writing report ...")
    stats = {"total": total, "candidates": len(candidates),
             "confirmed": len(confirmed), "open_source": open_cnt,
             "fulltext": ft_cnt}
    ds = report.write_dataset(confirmed)
    rp = report.write_report(confirmed, stats)

    dt = time.time() - t0
    print("\n" + "=" * 70)
    print(f"DONE in {dt:.0f}s")
    print(f"  Total papers scanned : {total}")
    print(f"  Candidates (keyword) : {len(candidates)}")
    print(f"  Confirmed agent-sec  : {len(confirmed)}  (open-source: {open_cnt})")
    print(f"  Analyzed on fulltext : {ft_cnt}/{len(confirmed)}")
    print(f"  Report  : {rp}")
    print(f"  Dataset : {ds}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
