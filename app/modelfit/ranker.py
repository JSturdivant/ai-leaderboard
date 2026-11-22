"""The model ranking API: score every purchasable model for one request and return them best first.

Inputs (all optional): a prompt, a conversation, a task description, weights for the factors that matter, cost
limits and filters. The request is triaged (triage.py) into a mix of benchmark dimensions, a complexity and its
needs; then every model in the market snapshot that passes the hard filters is scored 0-100 on each factor:

  performance  weighted mean of the model's coding / agentic / reasoning / chat scores (the same 0-100 category
               scores as the leaderboard), using your mix or the triage's
  savings      estimated $ for this request, or over the next few turns of a conversation with prompt caching
               priced in (convcost.py), log-scaled against the other candidates (cheapest = 100)
  latency      time to a ~500-token answer, from measured endpoint data or an estimate (labelled)
  reliability  best endpoint uptime over the last day plus provider redundancy
  context      context window, log-scaled from 8k (0) to 2M (100)
  recency      release date, 100 today falling to 0 at two years old

The final score is the weighted mean of the factor scores. Everything is explained in the response.

Cache-aware switching: with `current_model` (and a conversation), the model the conversation is already on is costed
with its warm prompt cache, every other model with a cold one (full input price once, plus any cache-write premium).
The response's `switching` block compares staying with switching over the horizon and recommends one.
"""

import math
import secrets
import time

from . import convcost
from . import scoring as sc
from . import triage as triage_mod

FACTORS = {
    "performance": "Benchmark quality for this request: weighted coding / agentic / reasoning / chat scores.",
    "savings": "Estimated cost of this request (or of the next turns, cache-aware), log-scaled across candidates (cheapest = 100).",
    "latency": "Speed to a ~500-token answer (measured when available, otherwise estimated).",
    "reliability": "Best endpoint uptime over the last day plus number of providers serving the model.",
    "context": "Context window size, log-scaled from 8k tokens (0) to 2M tokens (100).",
    "recency": "How new the model is: 100 when released today, 0 at two years old.",
}
DIMENSIONS = ("coding", "agentic", "reasoning", "chat")
ALIASES = {"quality": "performance", "capability": "performance", "cost": "savings", "price": "savings", "cheap": "savings",
           "speed": "latency", "fast": "latency", "uptime": "reliability", "stability": "reliability", "context_window": "context",
           "freshness": "recency", "new": "recency", "code": "coding", "agents": "agentic", "agent": "agentic",
           "tool_use": "agentic", "math": "reasoning", "conversation": "chat", "writing": "chat"}
MAX_PROMPT = 400_000
MAX_MESSAGES = 1000
MAX_LIMIT = 50
NEUTRAL = 50.0
CTX_LO, CTX_HI = 8_000, 2_000_000


class RankError(ValueError):
    """A request problem, reported to the caller with the offending field."""

    def __init__(self, field, message):
        super().__init__(f"{field}: {message}")
        self.field = field


# ── request validation ──────────────────────────────────────────────────────────────
def _num(v, field, lo=0.0, hi=1e9, allow_none=True):
    if v is None and allow_none:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise RankError(field, f"must be a number between {lo:g} and {hi:g}")
    return float(v)


def _bool(v, field):
    if v is None:
        return None
    if not isinstance(v, bool):
        raise RankError(field, "must be true or false")
    return v


def _strs(v, field, upper=False, max_items=200):
    if v is None:
        return None
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or len(v) > max_items or not all(isinstance(x, str) and 0 < len(x) <= 120 for x in v):
        raise RankError(field, "must be a list of strings")
    return [x.strip().upper() if upper else x.strip().lower() for x in v]


def parse(body):
    """Validate a /v1/rank body into a normalised request dict. Raises RankError."""
    if not isinstance(body, dict):
        raise RankError("body", "expected a JSON object")
    known = {"prompt", "messages", "task", "weights", "performance", "limits", "filters", "expected_output_tokens",
             "input_tokens", "tools", "response_format", "limit", "dedupe", "explain", "current_model", "cache",
             "min_switch_gain"}
    unknown = sorted(set(body) - known)
    if unknown:
        raise RankError(unknown[0], f"unknown field (expected one of: {', '.join(sorted(known))})")
    req = {}
    prompt = body.get("prompt")
    if prompt is not None and (not isinstance(prompt, str) or len(prompt) > MAX_PROMPT):
        raise RankError("prompt", f"must be a string of at most {MAX_PROMPT:,} characters")
    req["prompt"] = prompt or None
    msgs = body.get("messages")
    if msgs is not None:
        if not isinstance(msgs, list) or len(msgs) > MAX_MESSAGES:
            raise RankError("messages", f"must be a list of at most {MAX_MESSAGES} chat messages")
        size = 0
        for i, m in enumerate(msgs):
            if not isinstance(m, dict) or not isinstance(m.get("role"), str):
                raise RankError(f"messages[{i}]", 'must be an object like {"role": "user", "content": "..."}')
            c = m.get("content")
            if c is not None and not isinstance(c, (str, list)):
                raise RankError(f"messages[{i}].content", "must be a string or a list of content parts")
            size += len(c) if isinstance(c, str) else sum(len(str(p)) for p in (c or []))
        if size > MAX_PROMPT * 3:
            raise RankError("messages", "conversation is too large")
    req["messages"] = msgs or None
    task = body.get("task")
    if task is not None and (not isinstance(task, str) or len(task) > 4000):
        raise RankError("task", "must be a string of at most 4,000 characters")
    req["task"] = task or None
    req["tools"] = body.get("tools") if isinstance(body.get("tools"), list) and body.get("tools") else None
    req["response_format"] = body.get("response_format") or None

    weights = body.get("weights")
    if isinstance(weights, dict):  # friendly names: "speed" -> latency, "cost" -> savings, "quality" -> performance ...
        merged = {}
        for k, v in weights.items():
            key = ALIASES.get(str(k).lower(), str(k).lower())
            merged[key] = merged.get(key, 0) + v if isinstance(v, (int, float)) and not isinstance(v, bool) and key in merged else v
        weights = merged
    if weights is not None:
        if not isinstance(weights, dict) or not weights:
            raise RankError("weights", "must be an object like {\"performance\": 0.6, \"savings\": 0.4}")
        for k, v in weights.items():
            if k not in FACTORS and k not in DIMENSIONS:
                raise RankError(f"weights.{k}", f"unknown factor (factors: {', '.join(FACTORS)}; dimensions: {', '.join(DIMENSIONS)})")
            _num(v, f"weights.{k}", 0, 100, allow_none=False)
        if not any(v > 0 for v in weights.values()):
            raise RankError("weights", "give at least one factor a weight above 0")
    req["weights"] = weights
    perf = body.get("performance")
    if isinstance(perf, dict):
        perf = {ALIASES.get(str(k).lower(), str(k).lower()): v for k, v in perf.items()}
    if perf is not None:
        if not isinstance(perf, dict) or not perf:
            raise RankError("performance", 'must be an object like {"coding": 0.7, "reasoning": 0.3}')
        for k, v in perf.items():
            if k not in DIMENSIONS:
                raise RankError(f"performance.{k}", f"unknown dimension (expected {', '.join(DIMENSIONS)})")
            _num(v, f"performance.{k}", 0, 100, allow_none=False)
        if not any(v > 0 for v in perf.values()):
            raise RankError("performance", "give at least one dimension a weight above 0")
    req["performance"] = perf

    lim = body.get("limits") or {}
    if not isinstance(lim, dict):
        raise RankError("limits", "must be an object")
    allowed = {"max_cost_per_request", "max_blended_cost", "max_input_cost", "max_output_cost"}
    for k in lim:
        if k not in allowed:
            raise RankError(f"limits.{k}", f"unknown limit (expected {', '.join(sorted(allowed))})")
    req["limits"] = {k: _num(lim.get(k), f"limits.{k}", 0, 1e6) for k in allowed}

    f = body.get("filters") or {}
    if not isinstance(f, dict):
        raise RankError("filters", "must be an object")
    allowed_f = {"countries", "exclude_countries", "include_unknown_country", "open_weights", "vendors", "exclude_vendors",
                 "models", "exclude_models", "tools", "reasoning", "vision", "min_context", "free", "max_age_days",
                 "min_benchmark_coverage", "min_reliability", "min_performance"}
    for k in f:
        if k not in allowed_f:
            raise RankError(f"filters.{k}", f"unknown filter (expected {', '.join(sorted(allowed_f))})")
    req["filters"] = {
        "countries": _strs(f.get("countries"), "filters.countries", upper=True),
        "exclude_countries": _strs(f.get("exclude_countries"), "filters.exclude_countries", upper=True),
        "include_unknown_country": _bool(f.get("include_unknown_country"), "filters.include_unknown_country"),
        "open_weights": _bool(f.get("open_weights"), "filters.open_weights"),
        "vendors": _strs(f.get("vendors"), "filters.vendors"),
        "exclude_vendors": _strs(f.get("exclude_vendors"), "filters.exclude_vendors"),
        "models": _strs(f.get("models"), "filters.models", max_items=500),
        "exclude_models": _strs(f.get("exclude_models"), "filters.exclude_models", max_items=500),
        "tools": _bool(f.get("tools"), "filters.tools"),
        "reasoning": _bool(f.get("reasoning"), "filters.reasoning"),
        "vision": _bool(f.get("vision"), "filters.vision"),
        "free": _bool(f.get("free"), "filters.free"),
        "min_context": _num(f.get("min_context"), "filters.min_context", 0, 1e8),
        "max_age_days": _num(f.get("max_age_days"), "filters.max_age_days", 0, 1e5),
        "min_benchmark_coverage": _num(f.get("min_benchmark_coverage"), "filters.min_benchmark_coverage", 0, 1),
        "min_reliability": _num(f.get("min_reliability"), "filters.min_reliability", 0, 100),
        "min_performance": _num(f.get("min_performance"), "filters.min_performance", 0, 100),
    }
    cur = body.get("current_model")
    if cur is not None and (not isinstance(cur, str) or "/" not in cur or len(cur) > 120):
        raise RankError("current_model", "must be a model id like vendor/model")
    req["current_model"] = cur.strip().lower() if cur else None
    cache = body.get("cache") or {}
    if not isinstance(cache, dict):
        raise RankError("cache", "must be an object")
    allowed_c = {"cached_tokens", "seconds_since_last_request", "ttl_seconds", "expected_turns", "tokens_per_turn", "provider"}
    for k in cache:
        if k not in allowed_c:
            raise RankError(f"cache.{k}", f"unknown field (expected {', '.join(sorted(allowed_c))})")
    prov = cache.get("provider")
    if prov is not None and (not isinstance(prov, str) or len(prov) > 80):
        raise RankError("cache.provider", "must be a provider name")
    turns = cache.get("expected_turns")
    if turns is not None and (isinstance(turns, bool) or not isinstance(turns, int) or not 1 <= turns <= 200):
        raise RankError("cache.expected_turns", "must be an integer from 1 to 200")
    req["cache"] = {
        "cached_tokens": _num(cache.get("cached_tokens"), "cache.cached_tokens", 0, 50_000_000),
        "seconds_since_last_request": _num(cache.get("seconds_since_last_request"), "cache.seconds_since_last_request", 0, 1e9),
        "ttl_seconds": _num(cache.get("ttl_seconds"), "cache.ttl_seconds", 0, 1e7),
        "expected_turns": turns,
        "tokens_per_turn": _num(cache.get("tokens_per_turn"), "cache.tokens_per_turn", 0, 10_000_000),
        "provider": prov.strip() if prov else None,
    }
    req["min_switch_gain"] = _num(body.get("min_switch_gain"), "min_switch_gain", 0, 100)
    req["expected_output_tokens"] = _num(body.get("expected_output_tokens"), "expected_output_tokens", 1, 2_000_000)
    req["input_tokens"] = _num(body.get("input_tokens"), "input_tokens", 0, 100_000_000)
    lim_n = body.get("limit", 10)
    if isinstance(lim_n, bool) or not isinstance(lim_n, int) or not 1 <= lim_n <= MAX_LIMIT:
        raise RankError("limit", f"must be an integer from 1 to {MAX_LIMIT}")
    req["limit"] = lim_n
    req["dedupe"] = _bool(body.get("dedupe"), "dedupe") is not False
    req["explain"] = _bool(body.get("explain"), "explain") is not False
    return req


# ── weights ─────────────────────────────────────────────────────────────────────────
def resolve_weights(req, tri):
    """(factor weights summing to 1, performance mix summing to 1, how they were chosen)."""
    w = dict(req["weights"] or {})
    dims_in_weights = {k: float(w.pop(k)) for k in list(w) if k in DIMENSIONS}
    basis = "request"
    if req["weights"] is None:
        basis = "auto"
        # defaults lean on the triage: hard tasks buy quality, easy ones buy savings and speed
        w = {"performance": 0.55, "savings": 0.30, "latency": 0.10, "reliability": 0.05}
        if tri["complexity"] == "high":
            w.update(performance=0.70, savings=0.17, latency=0.06, reliability=0.07)
        elif tri["complexity"] == "low":
            w.update(performance=0.45, savings=0.35, latency=0.15, reliability=0.05)
        if tri["needs"]["tools"] or tri["category"] == "agentic":
            w["reliability"] += 0.05
    if dims_in_weights:  # {"coding": 0.4, "reasoning": 0.2, ...} at the top level = performance with that mix
        w["performance"] = w.get("performance", 0) + sum(dims_in_weights.values())
    w = {k: float(v) for k, v in w.items() if v and v > 0}
    total = sum(w.values())
    w = {k: round(v / total, 4) for k, v in w.items()}
    mix_src = req["performance"] or (dims_in_weights if any(v > 0 for v in dims_in_weights.values()) else None)
    if mix_src:
        mix_basis = "request"
        mix = {d: float(mix_src.get(d, 0)) for d in DIMENSIONS}
    else:
        mix_basis = "triage"
        mix = dict(tri["dimensions"])
    z = sum(mix.values()) or 1
    mix = {d: round(v / z, 4) for d, v in mix.items() if v > 0}
    return w, mix, {"weights": basis, "performance_mix": mix_basis}


# ── scoring helpers ─────────────────────────────────────────────────────────────────
def _log_scale(v, lo, hi):
    if v is None:
        return None
    if hi <= lo:
        return 100.0
    return max(0.0, min(100.0, 100 * (math.log(hi) - math.log(max(v, lo))) / (math.log(hi) - math.log(lo))))


def _usd(x):
    if x == 0:
        return "free"
    if x < 0.01:
        if x < 1e-6:
            return "<$0.000001"
        decimals = -math.floor(math.log10(x)) + 1          # two significant figures, never scientific notation
        return f"≈${x:.{decimals}f}"
    return f"≈${x:.2f}" if x >= 1 else f"≈${x:.3f}"


# ── the conversation being priced ───────────────────────────────────────────────────
def _msg_tokens(m):
    text, _ = triage_mod._content_text(m.get("content"))
    return triage_mod.estimate_tokens(text) + 4  # per-message framing


def conversation_plan(req, tri, cache_cfg, snapshot):
    """Token and cache assumptions: prefix already in the conversation, new tokens this turn, horizon, warm cache."""
    msgs = req["messages"] or []
    k = len(msgs)
    while k > 0 and isinstance(msgs[k - 1], dict) and msgs[k - 1].get("role") == "user":
        k -= 1                                           # trailing user turn(s) are this request's new tokens
    prefix = sum(_msg_tokens(m) for m in msgs[:k] if isinstance(m, dict))
    new = sum(_msg_tokens(m) for m in msgs[k:] if isinstance(m, dict))
    if req["prompt"]:
        new += triage_mod.estimate_tokens(req["prompt"])
    if req["tools"]:
        prefix += triage_mod.estimate_tokens(str(req["tools"]))  # tool schemas ride along in every request
    if req["input_tokens"] is not None:
        new = max(0, int(req["input_tokens"]) - prefix)
    if not prefix and not new:
        new = 1000
    c = req["cache"]
    if c["cached_tokens"] is not None:
        prefix = max(prefix, int(c["cached_tokens"]))
    users = [_msg_tokens(m) for m in msgs if isinstance(m, dict) and m.get("role") == "user"]
    per_turn = int(c["tokens_per_turn"]) if c["tokens_per_turn"] is not None else max(100, int(sum(users) / len(users)) if users else new)
    conversational = bool(req["current_model"]) or prefix > 0
    turns = c["expected_turns"] or (int(cache_cfg.get("default_turns", 5)) if conversational else 1)
    cur = None
    if req["current_model"]:
        cur = next((m for m in snapshot["models"] if m["id"] == req["current_model"]), None) or \
            next((m for m in snapshot["models"] if m["base"] == req["current_model"].split(":")[0]), None)
    vendor = (req["current_model"] or "").split("/")[0]
    ttl = c["ttl_seconds"] if c["ttl_seconds"] is not None else \
        (cache_cfg.get("ttl_seconds_by_vendor") or {}).get(vendor, cache_cfg.get("default_ttl_seconds", 300))
    age = c["seconds_since_last_request"] or 0
    min_cache = int(cache_cfg.get("min_cacheable_tokens", convcost.MIN_CACHEABLE))
    supports = bool(cur) and convcost.supports_cache(cur["cost"].get("channels"))
    warm_tokens = int(c["cached_tokens"]) if c["cached_tokens"] is not None else prefix
    warm = bool(cur) and supports and age < ttl and warm_tokens >= min_cache
    return {"prefix": prefix, "new": new, "turns": turns, "tokens_per_turn": per_turn, "current": cur, "ttl": ttl, "age": age,
            "warm": warm, "warm_tokens": warm_tokens if warm else 0, "provider": c["provider"], "min_cacheable": min_cache,
            "current_supports_cache": supports, "conversational": conversational}


# ── the ranking ─────────────────────────────────────────────────────────────────────
def rank(snapshot, req, tri=None):
    t0 = time.time()
    tri = tri or triage_mod.classify(req["prompt"], req["messages"], req["task"], req["tools"], req["response_format"])
    weights, mix, basis = resolve_weights(req, tri)
    f, lim = req["filters"], req["limits"]
    cache_cfg = snapshot["config"].get("cache") or {}
    plan = conversation_plan(req, tri, cache_cfg, snapshot)
    in_tok = plan["prefix"] + plan["new"]
    out_tok = int(req["expected_output_tokens"] or tri["suggested_output_tokens"])
    cur_base = plan["current"]["base"] if plan["current"] else None
    cur_drop = {}
    needs_tools = f["tools"] if f["tools"] is not None else (True if tri["needs"]["tools"] else None)
    needs_vision = f["vision"] if f["vision"] is not None else (True if tri["needs"]["vision"] else None)
    min_ctx = f["min_context"] or (in_tok + out_tok if in_tok + out_tok > 8000 else None)
    vendors_cfg = snapshot["config"].get("vendors") or {}
    today = time.time()

    excluded, cands = {}, []

    def drop(reason):
        excluded[reason] = excluded.get(reason, 0) + 1
        if cur_base and m["base"] == cur_base:
            cur_drop.setdefault(m["id"], reason)

    for m in snapshot["models"]:
        vendor = "meta" if m["vendor"] == "meta-llama" else m["vendor"]
        country = m.get("country")
        if f["models"] and m["id"] not in f["models"] and m["base"] not in f["models"]:
            drop("not_in_models_allowlist"); continue
        if f["exclude_models"] and (m["id"] in f["exclude_models"] or m["base"] in f["exclude_models"]):
            drop("excluded_model"); continue
        if f["vendors"] and vendor not in f["vendors"] and m["vendor"] not in f["vendors"]:
            drop("vendor_filter"); continue
        if f["exclude_vendors"] and (vendor in f["exclude_vendors"] or m["vendor"] in f["exclude_vendors"]):
            drop("vendor_filter"); continue
        if f["countries"]:
            if country is None and not f["include_unknown_country"]:
                drop("country_unknown"); continue
            if country is not None and country not in f["countries"]:
                drop("country_filter"); continue
        if f["exclude_countries"] and country in f["exclude_countries"]:
            drop("country_filter"); continue
        if f["open_weights"] is not None and bool(m.get("open_weights")) != f["open_weights"]:
            drop("open_weights_filter"); continue
        if needs_tools and not m.get("tools"):
            drop("needs_tool_use"); continue
        if f["reasoning"] is not None and bool(m.get("reasoning")) != f["reasoning"]:
            drop("reasoning_filter"); continue
        if needs_vision and not m.get("vision"):
            drop("needs_vision"); continue
        if min_ctx and (m.get("ctx") or 0) < min_ctx:
            drop("context_too_small"); continue
        if f["max_age_days"] is not None and m.get("age_days") is not None and m["age_days"] > f["max_age_days"]:
            drop("too_old"); continue
        is_cur = cur_base is not None and m["base"] == cur_base
        warm = {"tokens": plan["warm_tokens"], "provider": plan["provider"]} if is_cur and plan["warm"] else None
        est = convcost.best_channel(m["cost"].get("channels"), plan["prefix"], plan["new"], out_tok, plan["turns"], warm,
                                    plan["tokens_per_turn"], plan["min_cacheable"])
        if est is None:
            drop("no_price"); continue
        usd, p_in, p_out, ch, prov = est["first"], est["cand"]["in"], est["cand"]["out"], est["channel"], est["provider"]
        if f["free"] is True and usd > 0:
            drop("not_free"); continue
        if f["free"] is False and usd == 0:
            drop("free_excluded"); continue
        blended = sc.blended(p_in, p_out, snapshot["config"]["blend"])
        if (lim["max_cost_per_request"] is not None and usd > lim["max_cost_per_request"] + 1e-12) or \
           (lim["max_blended_cost"] is not None and blended > lim["max_blended_cost"] + 1e-12) or \
           (lim["max_input_cost"] is not None and p_in > lim["max_input_cost"] + 1e-12) or \
           (lim["max_output_cost"] is not None and p_out > lim["max_output_cost"] + 1e-12):
            drop("over_cost_limit"); continue
        rel = m.get("reliability")
        if f["min_reliability"] is not None and (rel is None or rel < f["min_reliability"]):
            drop("below_min_reliability"); continue
        cat = {d: (m["scores"].get(d) or {}).get("s") for d in DIMENSIONS}
        perf, share = sc.fit_score(cat, mix, 0.5)
        if "performance" in weights and perf is None:
            drop("insufficient_benchmark_data"); continue
        if f["min_benchmark_coverage"] is not None and share < f["min_benchmark_coverage"]:
            drop("insufficient_benchmark_data"); continue
        if f["min_performance"] is not None and (perf is None or perf < f["min_performance"]):
            drop("below_min_performance"); continue
        cands.append({"m": m, "vendor": vendor, "usd": usd, "est": est, "is_cur": is_cur, "in": p_in, "out": p_out, "channel": ch, "provider": prov,
                      "blended": blended, "perf": perf, "share": share, "cat": cat, "rel": rel})

    # automatic quality floor: with auto weights, don't let a near-useless model win on price alone
    floor = None
    if basis["weights"] == "auto" and f["min_performance"] is None and "performance" in weights:
        best = max((c["perf"] for c in cands if c["perf"] is not None), default=None)
        if best:
            floor = round(best * {"low": 0.6, "medium": 0.75, "high": 0.85}[tri["complexity"]], 1)
            kept = [c for c in cands if c["perf"] is not None and c["perf"] >= floor]
            for _ in range(len(cands) - len(kept)):
                drop("below_auto_quality_floor")
            cands = kept
    for c in cands:
        c["horizon"] = c["est"]["total"]
    paid = [c["horizon"] for c in cands if c["horizon"] > 0]
    lo, hi = (min(paid), max(paid)) if paid else (0, 0)
    for c in cands:
        m = c["m"]
        lat = m.get("latency") or {}
        age = m.get("age_days")
        f_scores = {
            "performance": c["perf"],
            "savings": 100.0 if c["horizon"] == 0 else _log_scale(c["horizon"], lo, hi),
            "latency": lat.get("index"),
            "reliability": c["rel"],
            "context": max(0.0, min(100.0, 100 * (math.log(max(m.get("ctx") or CTX_LO, CTX_LO)) - math.log(CTX_LO)) / (math.log(CTX_HI) - math.log(CTX_LO)))),
            "recency": None if age is None else max(0.0, 100 * (1 - age / 730)),
        }
        missing = [k for k in weights if f_scores.get(k) is None]
        c["factors"] = {k: round(v, 1) if v is not None else None for k, v in f_scores.items()}
        c["score"] = round(math.fsum(w * (f_scores[k] if f_scores[k] is not None else NEUTRAL) for k, w in weights.items()), 2)
        c["missing"] = missing

    cands.sort(key=lambda c: (-c["score"], c["horizon"], c["m"]["id"]))
    if req["dedupe"]:
        seen, uniq = set(), []
        for c in cands:
            if c["m"]["base"] not in seen:
                seen.add(c["m"]["base"])
                uniq.append(c)
        cands = uniq
    n = len(cands)
    perf_order = sorted((c["perf"] for c in cands if c["perf"] is not None), reverse=True)
    cost_order = sorted(c["horizon"] for c in cands)
    results = []
    for i, c in enumerate(cands[: req["limit"]], 1):
        m, lat = c["m"], c["m"].get("latency") or {}
        item = {
            "rank": i, "model": m["id"], "name": m["name"], "vendor": c["vendor"],
            "vendor_name": (vendors_cfg.get(c["vendor"]) or {}).get("label"),
            "country": m.get("country"), "open_weights": bool(m.get("open_weights")),
            "score": c["score"], "factors": c["factors"],
            "contributions": {k: round(w * (c["factors"][k] if c["factors"][k] is not None else NEUTRAL), 2) for k, w in weights.items()},
            "performance": {"score": c["perf"], "coverage": c["share"], "dimensions": c["cat"]},
            "cost": {"estimated_request_usd": round(c["usd"], 6), "input_per_m": c["in"], "output_per_m": c["out"],
                     "blended_per_m": round(c["blended"], 6), "channel": c["channel"], "provider": c["provider"],
                     "horizon_usd": round(c["horizon"], 6), "horizon_turns": plan["turns"]},
            "cache": _cache_view(c, plan, out_tok),
            "is_current": c["is_cur"],
            "latency": {"basis": lat.get("basis"), "ttft_s": lat.get("ttft_s"), "tokens_per_s": lat.get("tokens_per_s")},
            "reliability": {"score": c["rel"], "providers": m.get("providers")},
            "context": m.get("ctx"), "tools": bool(m.get("tools")), "reasoning": bool(m.get("reasoning")),
            "vision": bool(m.get("vision")), "released": m.get("released"),
        }
        if req["explain"]:
            item["explanation"] = _explain(c, weights, mix, perf_order, cost_order, n)
        results.append(item)
    switching = _switching(cands, plan, req, out_tok) if req["current_model"] else None
    if switching and switching.get("excluded_reason") is None and plan["current"] and not any(c["is_cur"] for c in cands):
        switching["excluded_reason"] = next(iter(cur_drop.values()), "not_ranked")
    return {
        "object": "ranking",
        "id": "rank_" + secrets.token_hex(8),
        "generated_at": snapshot["generated_at"],
        "triage": {k: v for k, v in tri.items() if k != "empty"},
        "weights": weights,
        "performance_mix": mix,
        "basis": basis,
        "switching": switching,
        "assumptions": {"input_tokens": in_tok, "output_tokens": out_tok, "min_context": min_ctx,
                        "conversation": {"prefix_tokens": plan["prefix"], "new_tokens": plan["new"], "horizon_turns": plan["turns"],
                                         "tokens_per_turn": plan["tokens_per_turn"], "cache_ttl_seconds": plan["ttl"],
                                         "seconds_since_last_request": plan["age"], "current_model_cache_warm": plan["warm"]},
                        "requires_tools": bool(needs_tools), "requires_vision": bool(needs_vision),
                        "auto_quality_floor": floor,
                        "latency": "estimated from model size, price and reasoning support unless basis is 'measured'"},
        "candidates": n,
        "excluded": dict(sorted(excluded.items(), key=lambda kv: -kv[1])),
        "results": results,
        "took_ms": int((time.time() - t0) * 1000),
    }


def _explain(c, weights, mix, perf_order, cost_order, n):
    out = []
    if c["perf"] is not None:
        dims = sorted(((d, c["cat"][d]) for d in mix if c["cat"].get(d) is not None), key=lambda kv: -mix[kv[0]])
        detail = ", ".join(f"{d} {s:.0f}" for d, s in dims[:3])
        pos = perf_order.index(c["perf"]) + 1 if c["perf"] in perf_order else None
        out.append(f"Performance {c['perf']:.1f} ({detail}){f', #{pos} of {n} candidates' if pos else ''}")
    usd = c["usd"]
    pos = cost_order.index(c["horizon"]) + 1
    turns = len(c["est"]["per_turn"])
    if turns > 1:
        out.append(f"Cost {_usd(usd)} this turn, {_usd(c['horizon'])} over {turns} turns via {c['provider']} (#{pos} cheapest of {n})")
    else:
        out.append(f"Cost {_usd(usd)} for this request via {c['provider']} (#{pos} cheapest of {n})")
    if c.get("is_cur") and c["est"]["cached_first"]:
        out.append(f"Your current model: {c['est']['cached_first']:,} prompt tokens already cached")
    lat = c["m"].get("latency") or {}
    if lat.get("ttft_s") is not None:
        out.append(f"Latency ~{lat['ttft_s']} s to first token, ~{lat['tokens_per_s']} tok/s ({lat.get('basis')})")
    if c["share"] is not None and c["share"] < 0.8 and c["perf"] is not None:
        out.append(f"Caution: benchmarks cover only {round(c['share'] * 100)}% of the performance mix")
    if c["missing"]:
        out.append(f"No data for {', '.join(c['missing'])}: scored as neutral (50)")
    return out


def _cache_view(c, plan, out_tok):
    """Cache facts for one result: does it cache, is it warm for this conversation, what caching saves this turn."""
    cand = c["est"]["cand"]
    prompt = plan["prefix"] + plan["new"]
    inp, outp, cr, cw = convcost.prices(cand, prompt)
    no_cache = (prompt * inp + out_tok * outp) / 1e6
    return {"supported": cr is not None, "warm": bool(c["is_cur"] and plan["warm"]), "cached_tokens": c["est"]["cached_first"],
            "read_per_m": cr, "write_per_m": cw, "saved_this_turn_usd": round(no_cache - c["usd"], 6)}


def _switching(cands, plan, req, out_tok, sim_turns=50):
    """Stay on the current model (warm cache) or switch (cold cache on the new model)? Compared over the horizon."""
    cur_m = plan["current"]
    min_gain = req["min_switch_gain"] if req["min_switch_gain"] is not None else 2.0
    base = {"current_model": req["current_model"], "known": cur_m is not None, "min_switch_gain": min_gain,
            "horizon_turns": plan["turns"],
            "cache": {"supported": plan["current_supports_cache"], "warm": plan["warm"], "cached_tokens": plan["warm_tokens"],
                      "ttl_seconds": plan["ttl"], "seconds_since_last_request": plan["age"],
                      "expires_in_seconds": max(0, round(plan["ttl"] - plan["age"])) if plan["warm"] else 0},
            "excluded_reason": None}
    if cur_m is None:
        return {**base, "recommendation": "switch" if cands else "no_alternative",
                "best_alternative": cands[0]["m"]["id"] if cands else None,
                "reason": "The current model is not in today's market data (unknown id or no longer sold); use the top result."}
    cur = next((c for c in cands if c["is_cur"]), None)
    alt = next((c for c in cands if not c["is_cur"]), None)
    if alt is None:
        return {**base, "recommendation": "stay", "best_alternative": None, "reason": "No other model passes your filters."}

    def sim(c, warm):
        w = plan["warm_tokens"] if warm else 0
        per, _ = convcost.simulate(c["est"]["cand"], plan["prefix"], plan["new"], out_tok, max(plan["turns"], sim_turns), w,
                                   plan["tokens_per_turn"], plan["min_cacheable"])
        return per

    alt_per = sim(alt, False)
    out = {**base, "current_name": cur_m["name"], "best_alternative": alt["m"]["id"], "best_alternative_name": alt["m"]["name"],
           "best_alternative_score": alt["score"]}
    if cur is None:
        return {**out, "recommendation": "switch", "current_score": None,
                "reason": "Your current model does not pass this request's filters, limits or requirements; switch to the top result."}
    stay_per = sim(cur, plan["warm"])
    h = plan["turns"]
    stay_h, alt_h = sum(stay_per[:h]), sum(alt_per[:h])
    gain = round(alt["score"] - cur["score"], 2)
    be = convcost.breakeven(stay_per, alt_per)
    out.update({
        "current_score": cur["score"], "score_gain": gain,
        "stay_horizon_usd": round(stay_h, 6), "switch_horizon_usd": round(alt_h, 6), "horizon_savings_usd": round(stay_h - alt_h, 6),
        "switch_now_extra_usd": round(alt_per[0] - stay_per[0], 6),
        "breakeven_turn": be,
    })
    # a cheaper model of (nearly) the same quality, judged on cost alone
    perf_cur = cur["perf"]
    eq = None
    if perf_cur is not None:
        for c in cands:
            if c["is_cur"] or c["perf"] is None or c["perf"] < perf_cur - 2:
                continue
            per = sim(c, False)
            saving = stay_h - sum(per[:h])
            if saving > 0 and (eq is None or saving > eq["horizon_savings_usd"]):
                eq = {"model": c["m"]["id"], "performance": c["perf"], "horizon_savings_usd": round(saving, 6),
                      "breakeven_turn": convcost.breakeven(stay_per, per)}
    out["cheaper_equivalent"] = eq
    if not plan["warm"]:
        cache_txt = "Your cache is cold (expired, unsupported or too short), so switching carries no cache penalty right now"
    elif out["switch_now_extra_usd"] > 0:
        cache_txt = (f"Your cache is warm ({plan['warm_tokens']:,} tokens, ~{out['cache']['expires_in_seconds']} s left), so switching "
                     f"costs {_usd(out['switch_now_extra_usd'])} more on the first turn")
    else:
        cache_txt = (f"Your cache is warm ({plan['warm_tokens']:,} tokens), but the alternative is cheaper even after paying "
                     "full price to rebuild it")
    if gain >= min_gain:
        rec = "switch"
        why = f"{alt['m']['id']} scores {gain:+.1f} higher with cache costs priced in (threshold {min_gain:g})."
    else:
        rec = "stay"
        why = f"The best alternative scores only {gain:+.1f} against your current model with cache costs priced in (threshold {min_gain:g})."
    if be == 1:
        why += " Switching to it is cheaper from the first turn."
    elif be:
        why += f" Switching to it breaks even on cost at turn {be}."
    elif stay_h < alt_h:
        why += " It never catches up on cost within 50 turns."
    if rec == "stay" and eq and eq["breakeven_turn"] and eq["breakeven_turn"] <= h:
        why += (f" For cost alone, {eq['model']} is about as good and saves {_usd(eq['horizon_savings_usd'])} over {h} turns "
                f"(breaks even at turn {eq['breakeven_turn']}).")
    out.update(recommendation=rec, reason=f"{why} {cache_txt}.")
    return out


def options(snapshot):
    """What /v1/rank accepts, with the values present in today's data (for docs, forms and validation)."""
    models = snapshot["models"]
    countries, vendors = {}, {}
    vcfg = snapshot["config"].get("vendors") or {}
    for m in models:
        c = m.get("country") or "unknown"
        countries[c] = countries.get(c, 0) + 1
        v = "meta" if m["vendor"] == "meta-llama" else m["vendor"]
        vendors[v] = vendors.get(v, 0) + 1
    return {
        "factors": FACTORS,
        "dimensions": {d: snapshot["config"]["categories"][d]["label"] for d in DIMENSIONS if d in snapshot["config"]["categories"]},
        "task_types": {k: {"label": v[0], "dimensions": v[1]} for k, v in triage_mod.TASKS.items()},
        "countries": dict(sorted(countries.items(), key=lambda kv: -kv[1])),
        "vendors": {v: {"label": (vcfg.get(v) or {}).get("label"), "country": (vcfg.get(v) or {}).get("country"), "models": n}
                    for v, n in sorted(vendors.items(), key=lambda kv: -kv[1])},
        "models": len(models),
        "open_weight_models": sum(1 for m in models if m.get("open_weights")),
        "generated_at": snapshot["generated_at"],
        "limits": {"max_results": MAX_LIMIT, "max_prompt_chars": MAX_PROMPT, "max_messages": MAX_MESSAGES},
        "cache": snapshot["config"].get("cache") or {},
    }
