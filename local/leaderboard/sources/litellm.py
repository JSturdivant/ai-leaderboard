"""LiteLLM's community price table, filtered to first-party ("primary source") providers.

Used as the direct-from-lab list price. Reseller/cloud entries (bedrock, azure, vertex, ...)
are excluded on purpose: they are different channels, not the primary source.
"""

import time

from ..util import fetch_json, num

NAME = "litellm"
LABEL = "Primary providers (LiteLLM price table)"
URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"

DEFAULT_PROVIDERS = ["openai", "anthropic", "gemini", "xai", "deepseek", "mistral", "moonshot",
                     "zai", "minimax", "dashscope", "meta_llama", "tencent", "xiaomi_mimo", "cohere_chat"]


def fetch(cfg, prev=None):
    providers = set(cfg.get("cost", {}).get("primary_litellm_providers") or DEFAULT_PROVIDERS)  # dict keys or list
    table = fetch_json(URL, timeout=120)
    out = []
    for key, v in table.items():
        if not isinstance(v, dict) or v.get("litellm_provider") not in providers:
            continue
        if v.get("mode") not in ("chat", "responses"):
            continue
        i, o = num(v.get("input_cost_per_token")), num(v.get("output_cost_per_token"))
        if i is None or o is None:
            continue
        out.append(
            {
                "key": key,
                "provider": v["litellm_provider"],
                "in": round(i * 1e6, 6),
                "out": round(o * 1e6, 6),
                "cache_read": None if num(v.get("cache_read_input_token_cost")) is None
                else round(num(v["cache_read_input_token_cost"]) * 1e6, 6),
                "deprecated": v.get("deprecation_date"),
            }
        )
    return {"fetched_at": time.time(), "entries": out}


def count(raw):
    return len(raw.get("entries", []))
