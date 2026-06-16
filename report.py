"""Report generator: render confirmed papers into Markdown + JSON dataset."""
import json
import os
from collections import defaultdict

import config

OPEN_LABEL = {True: "✅ 是", False: "❌ 否", "unknown": "❓ 未知"}


def _open(v):
    if isinstance(v, bool):
        return OPEN_LABEL[v]
    if isinstance(v, str) and v.lower() in ("true", "yes"):
        return OPEN_LABEL[True]
    if isinstance(v, str) and v.lower() in ("false", "no"):
        return OPEN_LABEL[False]
    return OPEN_LABEL["unknown"]


def write_dataset(papers):
    path = os.path.join(config.OUTPUT_DIR, "agent_security_papers.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(papers, fh, ensure_ascii=False, indent=2)
    return path


def _paper_block(i, p):
    a = p["analysis"]
    enr = p.get("enrichment") or {}
    links = []
    if p.get("ee"):
        links.append(f"[官方链接]({p['ee']})")
    if a.get("code_url"):
        links.append(f"[代码]({a['code_url']})")
    elif enr.get("github"):
        links.append(f"[代码]({enr['github']})")
    if p.get("url"):
        links.append(f"[DBLP]({p['url']})")
    links_str = " · ".join(links) if links else "—"

    authors = ", ".join(p.get("authors", [])[:6])
    if len(p.get("authors", [])) > 6:
        authors += " 等"

    basis = "📄 全文" if a.get("evidence_basis") == "full_text" else "📃 仅摘要"
    code = a.get("code_url") or enr.get("github")

    return f"""### {i}. {p['title']}

- **会议/年份**: {p['venue']} {p['year']}
- **作者**: {authors}
- **类别**: {a.get('subcategory', '—')}
- **分析依据**: {basis}
- **是否开源**: {_open(a.get('open_source'))}{(' — ' + code) if code else ''}
- **链接**: {links_str}
- **置信度**: {a.get('confidence', '—')}

**🎯 目标问题**
{a.get('target_problem') or '—'}

**🔧 核心技术**
{a.get('core_technique') or '—'}

**📝 评价**
{a.get('assessment') or '—'}

---
"""


def write_report(confirmed, stats):
    lines = []
    lines.append("# AI Agent 安全领域论文分析报告\n")
    lines.append("> 数据来源: DBLP — 四大安全顶会 (NDSS / CCS / USENIX Security / IEEE S&P), 2025–2026\n")
    lines.append(f"> 生成方式: DBLP 抓取 → 关键词初筛 → 摘要富集 → LLM ({config.LLM_MODEL}) 逐篇分析\n")

    # ---- summary stats ----
    lines.append("## 一、统计概览\n")
    lines.append("| 阶段 | 数量 |")
    lines.append("| --- | --- |")
    lines.append(f"| 抓取论文总数 | {stats['total']} |")
    lines.append(f"| 关键词候选 | {stats['candidates']} |")
    lines.append(f"| LLM 确认为 Agent 安全 | {len(confirmed)} |")
    lines.append(f"| 其中基于**论文全文**深度分析 | {stats.get('fulltext', '—')} |")
    lines.append(f"| 其中开源 | {stats['open_source']} |\n")

    # by venue-year
    grid = defaultdict(int)
    for p in confirmed:
        grid[(p["venue"], p["year"])] += 1
    lines.append("### 按会议 / 年份分布\n")
    header = "| 会议 | " + " | ".join(str(y) for y in config.YEARS) + " | 合计 |"
    sep = "| --- | " + " | ".join("---" for _ in config.YEARS) + " | --- |"
    lines.append(header)
    lines.append(sep)
    for v in config.VENUES:
        row = [v]
        s = 0
        for y in config.YEARS:
            c = grid.get((v, y), 0)
            s += c
            row.append(str(c))
        row.append(f"**{s}**")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # by subcategory
    sub = defaultdict(int)
    for p in confirmed:
        sub[p["analysis"].get("subcategory", "other")] += 1
    if sub:
        lines.append("### 按研究类型分布\n")
        lines.append("| 类型 | 数量 |")
        lines.append("| --- | --- |")
        for k, v in sorted(sub.items(), key=lambda x: -x[1]):
            lines.append(f"| {k} | {v} |")
        lines.append("")

    # ---- detailed entries, grouped by venue ----
    lines.append("## 二、论文详细分析\n")
    by_venue = defaultdict(list)
    for p in confirmed:
        by_venue[p["venue"]].append(p)
    idx = 1
    for v in config.VENUES:
        plist = by_venue.get(v, [])
        if not plist:
            continue
        lines.append(f"## {config.VENUE_FULLNAMES[v]}\n")
        plist.sort(key=lambda x: (x["year"], -float(x["analysis"].get("confidence") or 0)))
        for p in plist:
            lines.append(_paper_block(idx, p))
            idx += 1

    path = os.path.join(config.OUTPUT_DIR, "report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
