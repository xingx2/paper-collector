# AI Agent 安全论文采集与分析系统

从 [DBLP](https://dblp.uni-trier.de/) 抓取**四大安全顶会**（NDSS / CCS / USENIX Security / IEEE S&P）**2025–2026** 年论文，自动识别 **AI Agent / LLM 安全**方向论文，并基于**论文全文**用 LLM 生成中文深度分析（目标问题 / 核心技术 / 评价 / 是否开源）。

## 流水线

```
DBLP TOC 抓取  →  关键词初筛  →  摘要富集  →  相关性 Gate(摘要)  →  全文获取  →  深度分析(全文·中文)  →  报告
  dblp_fetch     enrich         enrich       analyze.gate         fulltext      analyze.deep_analyze    report
```

1. **抓取** (`dblp_fetch.py`)：用 DBLP 的 `toc:db/<venue><year>.bht:` 接口拉取每个会议-年份的完整目录，分页缓存原始 JSON。
2. **关键词初筛** (`enrich.select_candidates`)：召回导向，标题命中 agent/llm/jailbreak/prompt injection 等关键词即成为候选（约 1570 → ~194）。
3. **摘要富集** (`enrich.py`)：DBLP 无摘要，故从 **Semantic Scholar**（按 DOI/标题）与 **arXiv** 补全摘要、PDF 链接、arXiv id，并检测 GitHub 链接。
4. **相关性 Gate** (`analyze.gate`)：**便宜**的一次 LLM 调用，仅凭标题+摘要剔除初筛假阳性（如「用 LLM 做漏洞检测」「网络 user-agent」）。只保留真正研究 **agent/LLM 本身安全**的论文。
5. **全文获取** (`fulltext.py`)：对通过 Gate 的论文下载开放获取 PDF（arXiv → S2 openAccessPdf → NDSS → USENIX），抽取正文文本，**原始 PDF 一并归档**。
6. **深度分析** (`analyze.deep_analyze`)：**昂贵**的一次 LLM 调用，读**论文全文**，用**中文**输出结构化分析。全文不可得（IEEE/ACM 付费墙）时回退到摘要并在报告中标注「📃 仅摘要」。
7. **报告** (`report.py`)：生成 `report.md`（人读）与 `agent_security_papers.json`（数据集）。

全流程**逐项磁盘缓存**，可随时中断/续跑，重跑近乎零成本。

## 使用

```bash
# 配置第三方 Anthropic 兼容 provider（已从环境变量读取）
export ANTHROPIC_BASE_URL=...        # 如 https://yunwu.ai
export ANTHROPIC_AUTH_TOKEN=sk-...
export COLLECTOR_MODEL=claude-sonnet-4-6   # 可选，默认 sonnet

python3 -m pip install requests pypdf

python3 main.py                 # 全量运行
python3 main.py --limit 20      # 仅分析前 20 个候选（测试）
python3 main.py --high-only     # 仅高优先级候选
python3 main.py --refetch       # 忽略 DBLP 缓存重新抓取
```

## 配置（`config.py`）

- `VENUES` / `YEARS`：目标会议与年份。
- `AGENT_KEYWORDS` / `WEAK_ALONE`：初筛关键词（召回导向，假阳性交给 Gate）。
- `LLM_*`：模型、token 预算、超时。
- `*_DELAY` / `HTTP_RETRIES`：抓取限速与重试。

## 输出

- `data/output/report.md` —— 中文分析报告（统计概览 + 按会议分组的逐篇分析）。
- `data/output/agent_security_papers.json` —— 结构化数据集（含 enrichment、analysis、全文来源）。
- `data/cache/pdf/*.pdf` —— 归档的论文原始 PDF。
- `data/cache/fulltext/*.txt` —— 抽取的全文文本。
- `data/raw/*.json` —— DBLP 原始目录。

## 说明

- **2026 年数据**：截至运行时，DBLP 仅收录了 NDSS 2026；CCS/USENIX/S&P 2026 尚未举办或编入。流水线已覆盖这些会议，待 DBLP 放出数据后 `--refetch` 即可自动纳入。
- **付费墙**：IEEE S&P (ieeexplore) 与 ACM CCS (dl.acm.org) 正文需订阅。无 arXiv 预印本的论文将仅基于摘要分析，报告中明确标注。

## 维护记录

### 2026-06 修复深度分析 JSON 解析失败（82 篇中 52 篇）

**现象**：首轮全量运行后，82 篇确认论文里有 **52 篇**深度分析在报告中显示「LLM 输出无法解析」、置信度 0.0、核心字段全为「—」，有效分析仅 30 篇（失败率 63%），报告基本不可用。

**根因**：`analyze._extract_json` 无法处理 LLM 在中文文本里夹带的**半角双引号**。例如模型输出
`"core_technique": "第一阶段为"约束优化"（Constrained Optimization）：..."`，其中 `"约束优化"` 的引号提前闭合了 JSON 字符串，`json.loads` 报 `Expecting ',' delimiter`；原有的去尾逗号 / 补未闭合引号兜底均无法救回这类内嵌非法引号。

**修复**：

1. `analyze.py` 新增 `_repair_inner_quotes()`，作为 `_extract_json` 的第三层兜底——逐字符跟踪字符串状态，一个 `"` 只有在其后紧跟结构分隔符（`, } ] :` 或结尾）时才视为闭合引号，否则判定为内嵌引号并转义为 `\"`。
2. 解析失败时 `_raw` 改为存全文（原截断至 1000 字符，无法事后诊断）。
3. `config.LLM_DEEP_MAX_TOKENS` 4096 → 6144，降低中文长输出被真截断的概率。

**结果**：仅清除 52 个失败的 `deep_*.json` 缓存并续跑（gate/enrich/fulltext 缓存全复用，零额外成本），**解析失败 52 → 0**，82 篇全部产出有效分析。修复前先用 52 个真实失败样本离线验证新解析器（48 篇可直接从旧缓存恢复）才动缓存。
