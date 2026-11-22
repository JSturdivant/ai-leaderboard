"""Model routing: the live, stable route behind every alias, and the OpenAI-compatible gateway that uses it.

Agents rely on a route, not on a ranking. So the route for an alias is deliberately sticky:

* auto   (default) follow the top pick, but only switch when a new model beats the current one by at least
         `min_gain` fit points (hysteresis), or when the current one stops being eligible/purchasable;
* pinned always use one model (it is still scored, so you can see what you are missing).

Each route carries ordered fallbacks: the next-best distinct models. The gateway tries the route, then the
fallbacks, skipping models that cannot serve the request (tool calls on a model without tool use, a prompt larger
than the context window, a model not sold where the gateway buys it).

Route state lives in ws_routes; every change is written to pick_history (kind='route') and sent to the
workspace webhook as `route.changed`.
"""

import json
import threading
import time

from . import db, plans, secretbox, settings
from .util import log

DEFAULTS = {"mode": "auto", "pinned": None, "min_gain": 1.0, "fallbacks": 2}
MAX_MIN_GAIN = 20
POOL = 8                      # distinct candidates kept per route (route + fallbacks + spares for request filtering)
TTL = 300                     # seconds a client may cache a resolved route
FALLBACK_STATUSES = {404, 408, 409, 425, 429, 500, 502, 503, 504, 529}
PROVIDERS = {"openrouter": "OpenRouter"}


# ── per-alias route configuration ────────────────────────────────────────────────────
def parse_config(raw, ws=None):
    """Validate a route config from the API. Raises ValueError; plans.Upgrade for gated controls."""
    if not isinstance(raw, dict):
        raise ValueError("expected an object")
    out = dict(DEFAULTS)
    mode = raw.get("mode", "auto")
    if mode not in ("auto", "pinned"):
        raise ValueError("mode must be auto or pinned")
    out["mode"] = mode
    if mode == "pinned":
        pinned = str(raw.get("pinned") or "").strip()[:120]
        if "/" not in pinned:
            raise ValueError("pin a model id like vendor/model")
        out["pinned"] = pinned
    gain = raw.get("min_gain", DEFAULTS["min_gain"])
    if not isinstance(gain, (int, float)) or isinstance(gain, bool) or not 0 <= gain <= MAX_MIN_GAIN:
        raise ValueError(f"min_gain must be 0-{MAX_MIN_GAIN} fit points")
    out["min_gain"] = float(gain)
    fb = raw.get("fallbacks", DEFAULTS["fallbacks"])
    if not isinstance(fb, int) or isinstance(fb, bool) or not 0 <= fb <= 5:
        raise ValueError("fallbacks must be 0-5")
    out["fallbacks"] = fb
    if ws is not None:
        if (mode == "pinned" or out["min_gain"] != DEFAULTS["min_gain"]) and not plans.allows(ws, "routing_controls"):
            plans.require(ws, "routing_controls")
        lim = plans.limit(ws, "fallbacks")
        if lim is not None and fb > lim:
            raise plans.Upgrade(f"Your plan allows {lim} fallback model{'s' if lim != 1 else ''} per alias", "fallbacks", "pro")
    return out


def effective_config(ws, raw):
    """A stored config clamped to what the plan allows today (a downgrade must not break routing)."""
    cfg = {**DEFAULTS, **(raw or {})}
    if not plans.allows(ws, "routing_controls"):
        cfg.update(mode="auto", pinned=None, min_gain=DEFAULTS["min_gain"])
    lim = plans.limit(ws, "fallbacks")
    if lim is not None:
        cfg["fallbacks"] = min(cfg["fallbacks"], lim)
    return cfg


# ── route state ──────────────────────────────────────────────────────────────────────
def load(wsid):
    r = db.one("SELECT data, version, updated_at FROM ws_routes WHERE workspace_id=?", wsid)
    return (json.loads(r["data"]), r["version"]) if r else ({}, 0)


def _distinct(p, by):
    """Ranked candidates with one row per model (a model sold paid and ':free' counts once, as its cheaper row)."""
    seen, out = set(), []
    for mid in p["ranked"]:
        base = by[mid]["base"] if mid in by else mid
        if base not in seen:
            seen.add(base)
            out.append(mid)
    return out


def _choose(cfg, cands, fit_of, old, by):
    if cfg["mode"] == "pinned":
        if cfg["pinned"] in by:
            return cfg["pinned"], "pinned"
        if not cands:
            return None, "pinned model is not purchasable right now, and no other model qualifies"
        return cands[0], f"pinned model {cfg['pinned']} is not purchasable right now; following the top pick"
    if not cands:
        return None, "no eligible model with enough benchmark data"
    top = cands[0]
    cur = (old or {}).get("model")
    if cur and cur != top and cur in fit_of and fit_of[cur] is not None and cur in cands:
        gain = (fit_of[top] or 0) - fit_of[cur]
        if gain < cfg["min_gain"]:
            return cur, f"holding: the top pick is only +{gain:.1f} fit (switch threshold {cfg['min_gain']:g})"
    if cur and cur != top:
        return top, "switched to the new top pick" if cur in by else "previous model left the market; switched to the top pick"
    return top, "top pick"


def model_brief(m, fit=None):
    low = m["cost"]["lowest"]
    orc = m["cost"]["channels"].get("openrouter") or []
    return {"model": m["id"], "name": m["name"], "fit": fit, "context": m.get("ctx"), "tools": m.get("tools"),
            "reasoning": m.get("reasoning"),
            "cost": {"blended": low["blended"], "input": low["in"], "output": low["out"], "channel": low["channel"], "provider": low["provider"]},
            "gateway": bool(orc)}


def update(ws, alias_picks, data, snap, notify=True):
    """Recompute every alias's route from fresh picks. Returns {alias: (from, to)} for routes that changed."""
    by = {m["id"]: m for m in snap["models"]}
    prev, _ = load(ws["id"])
    cfgs = data.get("routes") or {}
    out, changed, now = {}, {}, time.time()
    for alias, p in alias_picks.items():
        cfg = effective_config(ws, cfgs.get(alias))
        cands = _distinct(p, by)
        old = prev.get(alias) or {}
        model, reason = _choose(cfg, cands, p.get("fit_of") or {}, old, by)
        fallbacks = [c for c in cands if c != model][:cfg["fallbacks"]]
        out[alias] = {
            "model": model, "reason": reason, "mode": cfg["mode"], "pinned": cfg["pinned"], "min_gain": cfg["min_gain"],
            "fit": (p.get("fit_of") or {}).get(model), "top_pick": cands[0] if cands else None,
            "since": old.get("since") if old.get("model") == model and old.get("since") else now,
            "fallbacks": fallbacks, "pool": [c for c in cands if c != model][:POOL],
            "fit_of": {c: (p.get("fit_of") or {}).get(c) for c in ([model] if model else []) + cands[:POOL]},
            "eligible": p.get("n", 0),
        }
        if prev and old.get("model") != model:
            changed[alias] = (old.get("model"), model)
    if out == {a: v for a, v in prev.items() if a in out} and set(prev) == set(out):
        return {}
    db.run("INSERT INTO ws_routes (workspace_id, data, version, updated_at) VALUES (?,?,1,?) "
           "ON CONFLICT(workspace_id) DO UPDATE SET data=excluded.data, version=ws_routes.version+1, updated_at=excluded.updated_at",
           ws["id"], json.dumps(out), now)
    for alias, (frm, to) in changed.items():
        db.run("INSERT INTO pick_history (workspace_id, alias, from_model, to_model, at, kind) VALUES (?,?,?,?,?, 'route')",
               ws["id"], alias, frm, to, now)
    hook = data.get("webhook") or {}
    if changed and notify and plans.allows(ws, "webhooks") and hook.get("url"):
        from .tenants import send_webhook  # late import: tenants imports this module
        threading.Thread(target=send_webhook, args=(hook, ws["id"], changed, snap.get("generated_at")),
                         kwargs={"event": "route.changed"}, daemon=True).start()
    return changed


def resolve(ws, alias, snap, needs=None):
    """The route for one alias as served to agents, with fallbacks narrowed to what this request needs."""
    routes, ver = load(ws["id"])
    key = next((a for a in routes if a.lower() == (alias or "").lower()), None)
    if key is None:
        raise KeyError(f"no alias called {alias!r} in this workspace")
    r = routes[key]
    by = {m["id"]: m for m in snap["models"]}
    chain = serving_chain(r, by, needs or {}, gateway_only=False)
    primary, rest = (chain[0], chain[1:]) if chain else (None, [])
    fit_of = r.get("fit_of") or {}
    return {
        "alias": key, "model": primary, "mode": r["mode"], "reason": r["reason"] if primary == r["model"] else
        (f"route {r['model']} cannot serve this request ({needs_text(needs)}); using the next fallback" if r["model"] else r["reason"]),
        "fit": fit_of.get(primary), "since": r["since"], "top_pick": r.get("top_pick"),
        "details": model_brief(by[primary], fit_of.get(primary)) if primary in by else None,
        "fallbacks": [model_brief(by[m], fit_of.get(m)) for m in rest if m in by],
        "version": ver, "ttl_seconds": TTL, "generated_at": snap["generated_at"],
    }


def needs_text(needs):
    parts = []
    if (needs or {}).get("tools"):
        parts.append("needs tool use")
    if (needs or {}).get("context"):
        parts.append(f"needs ~{needs['context']:,} tokens of context")
    return ", ".join(parts) or "constraints"


def serves(m, needs, gateway_only):
    if needs.get("tools") and not m.get("tools"):
        return False
    if needs.get("context") and (m.get("ctx") or 0) and m["ctx"] < needs["context"]:
        return False
    if gateway_only and not (m["cost"]["channels"].get("openrouter")):
        return False
    return True


def serving_chain(route, by, needs, gateway_only=True):
    """Route model, then its fallbacks, then spare candidates — keeping only models that can serve the request.
    Its length is 1 + the configured fallback count."""
    want = 1 + len(route.get("fallbacks") or [])
    order = ([route["model"]] if route.get("model") else []) + list(route.get("fallbacks") or []) + list(route.get("pool") or [])
    seen, chain = set(), []
    for mid in order:
        if mid in seen or mid not in by:
            continue
        seen.add(mid)
        if serves(by[mid], needs, gateway_only):
            chain.append(mid)
        if len(chain) == want:
            break
    return chain


# ── request analysis ─────────────────────────────────────────────────────────────────
def request_needs(body):
    """What a chat-completions request requires of a model: tool use, and roughly how much context."""
    chars = 0
    for msg in body.get("messages") or []:
        c = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):
            chars += sum(len(part.get("text") or "") for part in c if isinstance(part, dict))
    out_tokens = body.get("max_tokens") or body.get("max_completion_tokens") or 0
    est = chars // 4 + (out_tokens if isinstance(out_tokens, int) else 0)
    return {"tools": bool(body.get("tools") or body.get("functions")), "context": est if est > 2000 else 0}


def cost_of(m, usage):
    """$ for one call from the model's cheapest gateway price and the upstream token counts."""
    if not usage:
        return None
    orc = m["cost"]["channels"].get("openrouter") or []
    if not orc:
        return None
    c = min(orc, key=lambda x: x["in"] * 3 + x["out"])
    return round((usage.get("prompt_tokens") or 0) / 1e6 * c["in"] + (usage.get("completion_tokens") or 0) / 1e6 * c["out"], 6)


# ── provider credentials (bring your own key) ────────────────────────────────────────
def set_provider_key(wsid, provider, key):
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    key = (key or "").strip()
    if not 20 <= len(key) <= 300 or any(ch.isspace() for ch in key):
        raise ValueError("that does not look like an API key")
    db.run("INSERT INTO provider_keys (workspace_id, provider, sealed, hint, created_at) VALUES (?,?,?,?,?) "
           "ON CONFLICT(workspace_id, provider) DO UPDATE SET sealed=excluded.sealed, hint=excluded.hint, created_at=excluded.created_at",
           wsid, provider, secretbox.seal(key), key[-4:], time.time())


def delete_provider_key(wsid, provider):
    db.run("DELETE FROM provider_keys WHERE workspace_id=? AND provider=?", wsid, provider)


def provider_status(wsid):
    rows = {r["provider"]: r for r in db.all_("SELECT provider, hint, created_at FROM provider_keys WHERE workspace_id=?", wsid)}
    env = settings.get().openrouter_key and not settings.get().cloud
    return {p: {"label": label, "configured": p in rows or bool(env), "hint": rows[p]["hint"] if p in rows else None,
                "source": "workspace" if p in rows else ("environment" if env else None)} for p, label in PROVIDERS.items()}


def provider_key(wsid, provider="openrouter"):
    r = db.one("SELECT sealed FROM provider_keys WHERE workspace_id=? AND provider=?", wsid, provider)
    if r:
        key = secretbox.open_(r["sealed"])
        if key is None:
            log.warning("stored %s key for %s cannot be decrypted (secret key rotated?)", provider, wsid)
        return key
    s = settings.get()
    return s.openrouter_key if not s.cloud else None  # self-hosted: the operator's own key from the environment


# ── gateway log ──────────────────────────────────────────────────────────────────────
def log_request(wsid, **f):
    db.run("INSERT INTO route_log (workspace_id, alias, model, status, attempts, latency_ms, prompt_tokens, completion_tokens, "
           "cost_usd, stream, error, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           wsid, f.get("alias"), f.get("model"), f.get("status"), f.get("attempts", 1), f.get("latency_ms"), f.get("prompt_tokens"),
           f.get("completion_tokens"), f.get("cost_usd"), int(bool(f.get("stream"))), (f.get("error") or "")[:300] or None, time.time())


def usage_summary(wsid, since=None):
    since = since if since is not None else time.time() - 86400
    rows = db.all_("SELECT alias, COUNT(*) AS requests, SUM(CASE WHEN status>=200 AND status<300 THEN 0 ELSE 1 END) AS errors, "
                   "SUM(CASE WHEN attempts>1 THEN 1 ELSE 0 END) AS fell_back, SUM(COALESCE(prompt_tokens,0)) AS prompt_tokens, "
                   "SUM(COALESCE(completion_tokens,0)) AS completion_tokens, SUM(COALESCE(cost_usd,0)) AS cost_usd, "
                   "AVG(latency_ms) AS latency_ms FROM route_log WHERE workspace_id=? AND at>=? GROUP BY alias ORDER BY requests DESC", wsid, since)
    recent = db.all_("SELECT alias, model, status, attempts, latency_ms, prompt_tokens, completion_tokens, cost_usd, stream, error, at "
                     "FROM route_log WHERE workspace_id=? ORDER BY id DESC LIMIT 25", wsid)
    return {"by_alias": rows, "recent": recent}
