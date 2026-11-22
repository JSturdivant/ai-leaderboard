"""Cache-aware cost of continuing a conversation on a model, over the next few turns.

Prompt caching makes the earlier part of a conversation cheap to resend: providers bill a cached prefix at a
fraction of the input price (often 10%). The cache lives with the model and provider that served the last turn, so:

* staying on the current model with a warm cache pays the cached-read price for the prefix;
* switching to another model (or letting the cache expire) pays the full input price for the whole conversation
  once, plus a cache-write premium on providers that charge one (e.g. 1.25x), then gets cached reads there.

The simulation, per turn t of a horizon of H turns:

  prompt_t   = prefix_t + new_t                     (tokens sent)
  cached_t   = min(cached_available, prefix_t)      if the channel caches and prefix_t >= min_cacheable
  cost_t     = cached_t * read + (prompt_t - cached_t) * (input + max(0, write - input)) + out * output
  next turn: prefix = prompt_t + out (the reply joins the history), cached_available = prompt_t, new = tokens_per_turn

Long-context price tiers (e.g. higher prices above 200k prompt tokens) are applied per turn when published.
Token counts are one estimate for every model; real tokenizers differ by roughly +/-15%.
"""

MIN_CACHEABLE = 1024


def prices(c, prompt_tokens):
    """(input, output, cache_read | None, cache_write | None) in $/M for one channel at this prompt size."""
    t = c.get("long_tier")
    if t and t.get("min_prompt") and prompt_tokens >= t["min_prompt"] and t.get("in") is not None:
        return t["in"], t.get("out") if t.get("out") is not None else c["out"], t.get("cache_read"), t.get("cache_write")
    return c["in"], c["out"], c.get("cr"), c.get("cw")


def simulate(c, prefix, new, out, turns, warm_tokens=0, tokens_per_turn=None, min_cacheable=MIN_CACHEABLE):
    """Per-turn costs (USD) for `turns` turns on channel c. warm_tokens: prefix already cached here at turn 1."""
    per_turn, cached_avail = [], warm_tokens
    p, n = prefix, new
    step_new = tokens_per_turn if tokens_per_turn is not None else new
    first_cached = 0
    for t in range(max(1, turns)):
        prompt = p + n
        inp, outp, cr, cw = prices(c, prompt)
        cached = min(cached_avail, p) if (cr is not None and p >= min_cacheable) else 0
        uncached = prompt - cached
        write_premium = max(0.0, (cw or 0) - inp) if cr is not None and cw is not None and prompt >= min_cacheable else 0.0
        cost = (cached * cr if cached else 0) + uncached * (inp + write_premium) + out * outp
        per_turn.append(cost / 1e6)
        if t == 0:
            first_cached = cached
        cached_avail, p, n = prompt, prompt + out, step_new
    return per_turn, first_cached


def best_channel(channels, prefix, new, out, turns, warm=None, tokens_per_turn=None, min_cacheable=MIN_CACHEABLE):
    """Cheapest channel over the horizon. warm: {"tokens": n, "provider": optional name} for the cache already built.

    Returns dict(total, first, per_turn, channel, provider, cand, cached_first) or None.
    """
    best = None
    for ch, cands in (channels or {}).items():
        for c in cands:
            w = 0
            if warm and warm.get("tokens"):
                prov = warm.get("provider")
                if not prov or prov.lower() in (c.get("provider") or "").lower():
                    w = warm["tokens"]
            per, cached = simulate(c, prefix, new, out, turns, w, tokens_per_turn, min_cacheable)
            total = sum(per)
            if best is None or total < best["total"] - 1e-15:
                best = {"total": total, "first": per[0], "per_turn": per, "channel": ch, "provider": c.get("provider"),
                        "cand": c, "cached_first": cached}
    return best


def breakeven(stay, switch):
    """First turn (1-based) at which cumulative switching cost <= cumulative staying cost, else None."""
    a = b = 0.0
    for i, (s, w) in enumerate(zip(stay, switch), 1):
        a += s
        b += w
        if b <= a + 1e-12:
            return i
    return None


def supports_cache(channels):
    return any(c.get("cr") is not None for cands in (channels or {}).values() for c in cands)
