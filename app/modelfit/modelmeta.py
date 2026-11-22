"""Per-model facts the ranking API filters and scores on, beyond price and benchmarks.

* country       the developer's headquarters (config.json -> vendors), None when unknown
* open_weights  True when the weights are published: OpenRouter's Hugging Face id, else known open families
* vision        accepts image input
* reliability   best healthy-endpoint uptime over the last day, plus provider redundancy (0-100)
* latency       measured time-to-first-token / throughput when the source provides them; otherwise an
                *estimate* from model size signals (parameter count, tier words like "mini"/"pro"), output
                price and reasoning support. Estimates are labelled as such in every response.
"""

import math
import re

# families whose weights are published even when a listing carries no Hugging Face id
OPEN_FAMILIES = re.compile(
    r"(llama|gemma|qwen|deepseek|mistral-(small|nemo|7b)|mixtral|codestral-mamba|gpt-oss|phi-|olmo|granite|"
    r"hermes|nemotron|glm-4\.[0-9]-?air|kimi-k2|minimax-m|ernie-4\.5|hunyuan-a|ling-|ring-|lfm|smollm|falcon|yi-)",
    re.I)
CLOSED_HINTS = re.compile(r"(-max\b|-plus\b|-turbo\b|-pro-preview|qwen-max|qwen-plus|qwen-turbo)", re.I)
SMALL_WORDS = re.compile(r"\b(nano|micro|tiny|mini|lite|flash|haiku|small|air|fast|turbo|instant|swift|8b|7b|3b|1\.?5b)\b", re.I)
LARGE_WORDS = re.compile(r"\b(opus|ultra|max|heavy|large|jumbo|big|pro|405b|671b|1t)\b", re.I)
PARAMS = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([bt])(?:-a(\d+(?:\.\d+)?)b)?(?![a-z])", re.I)


def open_weights(row, hf_id=None):
    if hf_id:
        return True
    text = f"{row['id']} {row['name']}"
    if CLOSED_HINTS.search(text):
        return False
    return bool(OPEN_FAMILIES.search(text))


def country(vendor, vendors_cfg):
    v = "meta" if vendor == "meta-llama" else vendor
    return ((vendors_cfg or {}).get(v) or {}).get("country")


def active_params_b(text):
    """Active parameter count in billions from a name like 'qwen3-235b-a22b' or 'llama-3.1-8b', else None."""
    best = None
    for num, unit, active in PARAMS.findall(text.replace("_", "-")):
        total = float(num) * (1000 if unit.lower() == "t" else 1)
        val = float(active) if active else total
        if 0.3 <= val <= 3000:
            best = val if best is None else max(best, val)
    return best


def latency(row, eps=None):
    """{"basis", "ttft_s", "tokens_per_s", "index"}: index 0-100, higher = faster."""
    measured = [e for e in (eps or []) if e.get("throughput") or e.get("latency")]
    if measured:
        tps = max((e.get("throughput") or 0) for e in measured) or None
        ttft = min((e["latency"] for e in measured if e.get("latency")), default=None)
        ttft_s = ttft / 1000 if ttft and ttft > 20 else ttft  # OpenRouter reports milliseconds
        idx = _index(tps or 60, ttft_s or 1.0)
        return {"basis": "measured", "ttft_s": round(ttft_s, 2) if ttft_s else None, "tokens_per_s": round(tps) if tps else None, "index": idx}
    text = f"{row['id']} {row['name']}"
    out_price = ((row.get("cost") or {}).get("lowest") or {}).get("out") or 0
    params = active_params_b(text)
    # size proxy on a log scale: parameters if the name says so, otherwise the output price
    if params:
        size = math.log10(max(params, 1))                   # 1B -> 0, 10B -> 1, 100B -> 2, 1T -> 3
    else:
        size = 1.2 + 0.55 * math.log10(max(out_price, 0.05) / 0.3)  # $0.30/M ~ 15B-class, $15/M ~ frontier
    if SMALL_WORDS.search(text):
        size -= 0.35
    if LARGE_WORDS.search(text):
        size += 0.3
    size = max(0.0, min(3.2, size))
    tps = 260 / (1 + size ** 1.6)                           # ~260 tok/s tiny models, ~30 tok/s frontier
    ttft = 0.25 + 0.35 * size
    if row.get("reasoning"):
        ttft *= 1.6                                         # thinking before the first visible token
    return {"basis": "estimated", "ttft_s": round(ttft, 2), "tokens_per_s": round(tps), "index": _index(tps, ttft)}


def _index(tps, ttft_s, out_tokens=500):
    """0-100 from the time to produce a typical 500-token answer (0.5 s -> ~100, 30 s+ -> ~0), log scale."""
    t = ttft_s + out_tokens / max(tps, 1)
    return round(max(0.0, min(100.0, 100 * (math.log10(30) - math.log10(max(t, 0.5))) / (math.log10(30) - math.log10(0.5)))), 1)


def reliability(eps):
    """0-100 from the best healthy endpoint's uptime and how many providers serve the model."""
    ok = [e for e in (eps or []) if e.get("status") in (0, None) and e.get("in") is not None]
    if not ok:
        return None, 0
    up = max((e.get("uptime") for e in ok if e.get("uptime") is not None), default=None)
    providers = len({e.get("provider") for e in ok})
    base = up if up is not None else 95.0
    # 95% uptime -> 0, 100% -> 85; up to +15 for redundancy (5+ providers)
    score = max(0.0, (base - 95) / 5 * 85) + min(providers, 5) * 3
    return round(min(100.0, score), 1), providers
