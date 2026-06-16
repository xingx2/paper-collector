"""Central configuration for the AI-agent-security paper collector."""
import os

# ---------------------------------------------------------------------------
# Target venues (DBLP) and years
# ---------------------------------------------------------------------------
# DBLP TOC keys are of the form db/conf/<venue>/<venue><year>.bht
VENUES = {
    "NDSS": "conf/ndss/ndss",   # Network and Distributed System Security Symposium
    "CCS": "conf/ccs/ccs",      # ACM Conference on Computer and Communications Security
    "USENIX": "conf/uss/uss",   # USENIX Security Symposium
    "SP": "conf/sp/sp",         # IEEE Symposium on Security and Privacy (S&P / Oakland)
}
VENUE_FULLNAMES = {
    "NDSS": "Network and Distributed System Security Symposium (NDSS)",
    "CCS": "ACM Conference on Computer and Communications Security (CCS)",
    "USENIX": "USENIX Security Symposium",
    "SP": "IEEE Symposium on Security and Privacy (S&P)",
}
YEARS = [2025, 2026]

# ---------------------------------------------------------------------------
# Keyword pre-filter for AI-agent-security candidates.
# Recall-oriented: the LLM stage removes false positives. Lowercased substring
# match against the paper title.
# ---------------------------------------------------------------------------
AGENT_KEYWORDS = [
    "agent", "agentic", "multi-agent", "multiagent",
    "llm", "large language model", "language model", "gpt", "chatgpt",
    "autonomous", "tool use", "tool-use", "tool-calling", "function calling",
    "prompt injection", "jailbreak", "jailbreaking", "prompt-injection",
    "mcp", "model context protocol", "react agent",
    "web agent", "gui agent", "computer use", "computer-use",
    "copilot", "rag", "retrieval-augmented",
    "foundation model", "ai assistant", "code agent", "coding agent",
]
# Terms that, alone, are too noisy ("agent" matches "user agent", "SIP agent").
# A title matching ONLY these weak terms still becomes a candidate but is flagged
# low-priority; the LLM makes the final call.
WEAK_ALONE = {"agent", "rag"}

# ---------------------------------------------------------------------------
# LLM (third-party Anthropic-compatible provider, configured via env)
# ---------------------------------------------------------------------------
LLM_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
LLM_AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.environ.get("COLLECTOR_MODEL", "claude-sonnet-4-6")
LLM_MAX_TOKENS = 2048          # gate / short calls
LLM_DEEP_MAX_TOKENS = 6144     # full-text deep analysis (Chinese is token-heavy)
LLM_TIMEOUT = (int(os.environ.get("API_TIMEOUT_MS", "300000")) // 1000) or 120

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")          # raw DBLP json per venue-year
CACHE_DIR = os.path.join(DATA_DIR, "cache")      # abstracts + llm analyses
OUTPUT_DIR = os.path.join(DATA_DIR, "output")    # final report + dataset

for _d in (RAW_DIR, CACHE_DIR, OUTPUT_DIR):
    os.makedirs(_d, exist_ok=True)

# Politeness / robustness
DBLP_DELAY = 1.2     # seconds between DBLP requests
S2_DELAY = 1.1       # seconds between Semantic Scholar requests
HTTP_RETRIES = 4
