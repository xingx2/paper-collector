"""LLM analysis pipeline (third-party Anthropic-compatible provider).

Two tiers, both returning strict JSON:

1. gate(paper, enr)         - CHEAP. Uses title + abstract only. Decides whether
                              the paper is really about AI-agent / LLM SECURITY.
                              Removes keyword-prefilter false positives before we
                              spend bandwidth downloading PDFs.

2. deep_analyze(paper, enr, fulltext)
                            - EXPENSIVE. Reads the PAPER FULL TEXT (extracted PDF)
                              and produces the user-requested fields IN CHINESE:
                              目标问题 / 核心技术 / 评价 / 是否开源.
                              Falls back to the abstract only if no full text was
                              obtainable (paywalled IEEE/ACM), and says so.

Both results are cached per paper key so re-runs are cheap and resumable.
"""
import json
import os
import re
import time

import requests

import config

# ------------------------------------------------------------------ transport
def _headers():
    tok = config.LLM_AUTH_TOKEN
    return {
        "x-api-key": tok,
        "authorization": f"Bearer {tok}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def _call(prompt, max_tokens=config.LLM_MAX_TOKENS, retries=config.HTTP_RETRIES):
    body = {
        "model": config.LLM_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    url = f"{config.LLM_BASE_URL}/v1/messages"
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=_headers(), json=body, timeout=config.LLM_TIMEOUT)
            if r.status_code in (429, 529):
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            parts = data.get("content", [])
            text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
            return text.strip()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"LLM call failed after {retries} tries: {last}")


def _repair_inner_quotes(s):
    """Escape unescaped ASCII double quotes that appear *inside* JSON string
    values. The LLM frequently emits Chinese text like  "约束优化"（...）  whose
    inner quotes prematurely terminate the string and break json.loads. We walk
    the text tracking string state: a `"` only closes a string if the next
    non-space char is a structural delimiter (, } ] :) or end-of-input;
    otherwise it is an inner quote and gets escaped.
    """
    out = []
    in_str = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if not in_str:
            out.append(c)
            if c == '"':
                in_str = True
            i += 1
            continue
        if c == "\\":                       # keep existing escape pair intact
            out.append(c)
            if i + 1 < n:
                out.append(s[i + 1])
                i += 2
            else:
                i += 1
            continue
        if c == '"':
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            nxt = s[j] if j < n else ""
            if nxt in (",", "}", "]", ":", ""):
                out.append(c)               # legitimate closing quote
                in_str = False
            else:
                out.append('\\"')           # stray inner quote -> escape
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _extract_json(text):
    # strip code fences
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    text = text[start:]
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    # remove trailing commas
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(cleaned)
    except Exception:  # noqa: BLE001
        pass
    # escape stray inner double quotes inside Chinese string values
    repaired = _repair_inner_quotes(cleaned)
    try:
        return json.loads(repaired)
    except Exception:  # noqa: BLE001
        pass
    # salvage truncated output: close an unterminated string + dangling braces
    salv = repaired.rstrip()
    if salv.count('"') % 2 == 1:          # unterminated string value
        salv += '"'
    salv = re.sub(r",\s*$", "", salv)
    salv += "}" * max(0, salv.count("{") - salv.count("}"))
    return json.loads(salv)


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "是")
    return bool(v)


def _safe(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s or "unknown")


# ------------------------------------------------------------------ tier 1: gate
def gate(paper, enr):
    """Cheap abstract-based relevance gate. Cached."""
    cache_path = os.path.join(config.CACHE_DIR, f"gate_{_safe(paper['key'])}.json")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            return json.load(fh)

    abstract = (enr or {}).get("abstract") or "(no abstract available)"
    prompt = (
        "你正在筛选与「AI 智能体 / 大语言模型(LLM)安全」相关的论文，用于构建一份"
        "专题综述。判定下面这篇论文是否**主要研究 AI agent / LLM 系统的安全或安全性问题**"
        "（例如：提示注入、越狱、工具调用滥用、多智能体攻击、智能体沙箱/隔离、"
        "自主攻防、RAG 投毒、智能体专属防御、LLM 供应链后门等）。\n\n"
        "不合格的情况：仅把 LLM 当作工具去做传统安全任务（如用 LLM 做漏洞检测/逆向/"
        "模糊测试）、与 agent/LLM 无关的通用 ML 隐私或鲁棒性、仅字面出现 'agent'"
        "（网络 user-agent、移动 agent、SNMP agent 等）。这些都判为 false。\n"
        "注意：'用 LLM 做安全任务' 与 '研究 LLM/agent 本身的安全' 不同，只有后者合格。\n\n"
        "只返回如下 JSON：\n"
        '{"is_agent_security": true/false, '
        '"subcategory": "attack|defense|benchmark/measurement|framework/system|survey|other", '
        '"agent_relevance": "一句话中文说明它是/不是 agent 安全论文", '
        '"confidence": 0.0-1.0}\n\n'
        "=== 论文 ===\n"
        f"标题: {paper['title']}\n"
        f"会议/年份: {paper['venue']} {paper['year']}\n"
        f"摘要: {abstract}\n"
    )
    text = _call(prompt, max_tokens=600)
    try:
        res = _extract_json(text)
    except Exception:  # noqa: BLE001
        res = {"is_agent_security": False, "subcategory": "other",
               "agent_relevance": "解析失败", "confidence": 0.0, "_raw": text[:500]}
    res["is_agent_security"] = _as_bool(res.get("is_agent_security"))
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    return res


# -------------------------------------------------------- tier 2: deep analysis
DEEP_KEYS = ["subcategory", "target_problem", "core_technique", "assessment",
             "open_source", "code_url", "evidence_basis", "confidence"]


def deep_analyze(paper, enr, fulltext, source_url=None):
    """Full-text deep analysis, Chinese output. Cached."""
    cache_path = os.path.join(config.CACHE_DIR, f"deep_{_safe(paper['key'])}.json")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            return json.load(fh)

    if fulltext and len(fulltext) > 1500:
        basis = "full_text"
        body = (
            f"以下是论文全文（已从 PDF 抽取，可能含少量格式噪声，已截断至前约 4.8 万字符）：\n"
            f"来源: {source_url}\n\n{fulltext}"
        )
        basis_note = "已基于论文全文"
    else:
        basis = "abstract_only"
        abstract = (enr or {}).get("abstract") or "(无摘要)"
        body = (
            "未能获取该论文的开放获取全文（可能是 IEEE/ACM 付费墙），以下仅为摘要：\n\n"
            f"标题: {paper['title']}\n摘要: {abstract}"
        )
        basis_note = "仅基于摘要（全文不可得）"

    gh = (enr or {}).get("github")
    prompt = (
        "你是一位资深安全研究者，正在为「AI 智能体 / LLM 安全」专题综述撰写逐篇深度分析。"
        "请仔细阅读下面提供的论文内容，用**中文**输出严谨、具体、有批判性的分析。"
        "不要套话，结论要落到论文的具体方法、实验与数据上。\n\n"
        "只返回如下 JSON（所有文本字段用中文）：\n"
        "{\n"
        '  "subcategory": "attack|defense|benchmark/measurement|framework/system|survey|other",\n'
        '  "target_problem": "本文解决的目标安全问题，讲清威胁模型/场景/为何重要，3-5句",\n'
        '  "core_technique": "核心技术方案与关键设计/创新点，讲清怎么做的，3-6句",\n'
        '  "assessment": "你的评价：创新性、亮点、实验是否充分、局限与不足，4-6句，需有批判性",\n'
        '  "open_source": "true|false|unknown —— 论文是否开源代码/数据",\n'
        '  "code_url": "代码仓库链接，没有则 null（不要编造）",\n'
        '  "evidence_basis": "full_text 或 abstract_only",\n'
        '  "confidence": 0.0-1.0\n'
        "}\n\n"
        f"分析依据: {basis_note}。已探测到的代码链接: {gh or '无'}。"
        "若文中出现 github/zenodo/gitlab 等链接或明确 'we release/open-source'，"
        "open_source 取 true 并填 code_url；若明确未提供则 false；不确定填 unknown。\n\n"
        "=== 论文内容 ===\n"
        f"{body}\n"
    )
    text = _call(prompt, max_tokens=config.LLM_DEEP_MAX_TOKENS)
    try:
        res = _extract_json(text)
    except Exception:  # noqa: BLE001
        res = {"subcategory": "other", "target_problem": None, "core_technique": None,
               "assessment": "LLM 输出无法解析。", "open_source": "unknown",
               "code_url": None, "evidence_basis": basis, "confidence": 0.0,
               "_raw": text}
    res["evidence_basis"] = basis
    if gh and not res.get("code_url"):
        res["code_url"] = gh
        if str(res.get("open_source")).lower() not in ("true", "yes"):
            res["open_source"] = "true"
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    return res


if __name__ == "__main__":
    print(_call("只回复一个词: READY"))
