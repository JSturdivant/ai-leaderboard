"""Workspaces: their alias definitions and tuning, their recommendations, and pick-change webhooks.

Stored settings (ws_settings.data, JSON):

  {"alias_defs":     {"Coding": {"note", "max_cost", "require", "min_context", "weights", ...}, ...},  # ordered
   "weights":        {"Coding": {"cats": {...}, "metrics": {...}}},   # sparse overlays (see weights.py)
   "caps":           {"Cheap": 0.2},                                  # sparse overlays, null = no cap
   "current_models": {"Daily": "z-ai/glm-5.3-flash"},                 # what the workspace runs today
   "webhook":        {"url": "...", "secret": "...", "legacy_token": false}}

Every change bumps `version` (the UI polls it so teammates and other tabs see edits) and re-scores the
workspace against the current market snapshot. Every new snapshot re-scores every workspace.
"""

import copy
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
import urllib.request
from urllib.parse import urlparse

from . import LATEST, db, plans, recommend, settings
from . import picks as picks_mod
from . import weights as W
from .store import load_config
from .util import log, read_json

SELFHOST_WS = "ws_local"
HOOK_TIMEOUT = 5
_snap_lock = threading.Lock()
_snap = {"mtime": None, "data": None}


# ── the market snapshot (shared by every workspace) ──────────────────────────────────
def snapshot():
    """data/latest.json, parsed once per rebuild."""
    try:
        mtime = LATEST.stat().st_mtime_ns
    except OSError:
        return None
    with _snap_lock:
        if _snap["mtime"] != mtime:
            data = read_json(LATEST)
            if data is None:
                return _snap["data"]
            _snap.update(mtime=mtime, data=data)
        return _snap["data"]


def remember_snapshot(data):
    with _snap_lock:
        try:
            _snap.update(mtime=LATEST.stat().st_mtime_ns, data=data)
        except OSError:
            _snap.update(mtime=None, data=data)


# ── settings ─────────────────────────────────────────────────────────────────────────
def presets():
    return load_config()["aliases"]


def _blank():
    return {"alias_defs": {}, "weights": {}, "caps": {}, "current_models": {}, "webhook": {}}


def init_workspace(wsid, starter=True, data=None):
    cfg = load_config()
    if data is None:
        data = _blank()
        names = (cfg.get("workspace_defaults") or {}).get("starter_aliases") if starter else None
        for name in (names or list(cfg["aliases"])):
            if name in cfg["aliases"]:
                data["alias_defs"][name] = {**copy.deepcopy(cfg["aliases"][name]), "preset": name}
    db.run("INSERT OR IGNORE INTO ws_settings (workspace_id, data, version, updated_at) VALUES (?,?,1,?)",
           wsid, json.dumps(data), time.time())
    return data


def get_settings(wsid):
    r = db.one("SELECT data, version, updated_at FROM ws_settings WHERE workspace_id=?", wsid)
    if not r:
        return init_workspace(wsid), 1, time.time()
    return {**_blank(), **json.loads(r["data"])}, r["version"], r["updated_at"]


def version(wsid):
    r = db.one("SELECT version FROM ws_settings WHERE workspace_id=?", wsid)
    return r["version"] if r else None


def _put(wsid, data):
    with db.tx() as c:
        c.execute("UPDATE ws_settings SET data=?, version=version+1, updated_at=? WHERE workspace_id=?",
                  (json.dumps(data), time.time(), wsid))
        return c.execute("SELECT version FROM ws_settings WHERE workspace_id=?", (wsid,)).fetchone()["version"]


def _ctx(data):
    return {"aliases": data["alias_defs"], "categories": load_config()["categories"]}


def clean(data):
    """Stored settings with anything that no longer validates dropped (aliases, categories, benchmarks)."""
    ctx = _ctx(data)
    return {**data, "weights": W.parse(data.get("weights"), ctx), "caps": W.parse_caps(data.get("caps"), ctx),
            "current_models": {a: m for a, m in (data.get("current_models") or {}).items() if a in data["alias_defs"] and m}}


def effective_defs(data):
    """Alias definitions with the workspace's weights and caps applied (what scoring uses)."""
    data = clean(data)
    return {n: W.merge_alias(d, data["weights"].get(n), data["caps"].get(n, W.UNSET)) for n, d in data["alias_defs"].items()}


def public_settings(ws, data, ver):
    """What the UI and API see (the webhook secret only as a hint)."""
    data = clean(data)
    hook = data.get("webhook") or {}
    return {"alias_defs": data["alias_defs"], "weights": data["weights"], "caps": data["caps"],
            "current_models": data["current_models"], "version": ver,
            "webhook": {"url": hook.get("url") or "", "secret_hint": (hook.get("secret") or "")[-4:], "has_secret": bool(hook.get("secret"))}}


def save_tuning(ws, raw_weights, raw_caps=None):
    """Validate and store weights (and caps unless None). Custom weights need a plan that includes them."""
    data, _, _ = get_settings(ws["id"])
    ctx = _ctx(data)
    weights = W.parse(raw_weights, ctx, strict=True)
    if weights and not plans.allows(ws, "custom_weights"):
        plans.require(ws, "custom_weights")
    data["weights"] = weights
    if raw_caps is not None:
        data["caps"] = W.parse_caps(raw_caps, ctx, strict=True)
    ver = _put(ws["id"], data)
    recompute(ws)
    return data, ver


def add_alias(ws, name, raw=None, preset=None):
    data, _, _ = get_settings(ws["id"])
    name = (name or preset or "").strip()
    if name in data["alias_defs"]:
        raise ValueError(f"There is already an alias called {name}")
    plans.check_limit(ws, "aliases", len(data["alias_defs"]))
    cfg = load_config()
    if preset:
        if preset not in cfg["aliases"]:
            raise ValueError(f"Unknown preset {preset!r}")
        base = {**copy.deepcopy(cfg["aliases"][preset]), "preset": preset}
        raw = {**base, **(raw or {})}
    if len(data["alias_defs"]) >= W.MAX_ALIASES:
        raise ValueError("Too many aliases")
    data["alias_defs"][name] = W.parse_alias_def(name, raw or {}, cfg["categories"])
    ver = _put(ws["id"], data)
    recompute(ws)
    db.event("alias.added", ws["id"], alias=name, preset=preset)
    return data, ver


def remove_alias(ws, name):
    data, _, _ = get_settings(ws["id"])
    if name not in data["alias_defs"]:
        raise KeyError(name)
    for k in ("alias_defs", "weights", "caps", "current_models"):
        data[k].pop(name, None)
    ver = _put(ws["id"], data)
    recompute(ws)
    return data, ver


def set_current(ws, alias, model_id):
    data, _, _ = get_settings(ws["id"])
    if alias not in data["alias_defs"]:
        raise KeyError(alias)
    model_id = (model_id or "").strip()[:120]
    if model_id:
        data["current_models"][alias] = model_id
    else:
        data["current_models"].pop(alias, None)
    ver = _put(ws["id"], data)
    recompute(ws)
    return data, ver


def set_webhook(ws, url, rotate=False):
    plans.require(ws, "webhooks")
    data, _, _ = get_settings(ws["id"])
    url = (url or "").strip()
    if url:
        check_webhook_url(url)
    hook = dict(data.get("webhook") or {})
    hook["url"] = url
    if rotate or not hook.get("secret"):
        hook["secret"] = "whsec_" + secrets.token_urlsafe(24)
    data["webhook"] = hook
    ver = _put(ws["id"], data)
    db.event("webhook.updated", ws["id"])
    return hook, ver


# ── recommendations ──────────────────────────────────────────────────────────────────
def recommendations(wsid):
    r = db.one("SELECT data, updated_at FROM ws_recommendations WHERE workspace_id=?", wsid)
    return json.loads(r["data"]) if r else None


def recompute(ws, snap=None, notify=True):
    """Re-score one workspace against the snapshot, store the result, log and announce #1 changes."""
    snap = snap or snapshot()
    if not snap:
        return None
    data, _, _ = get_settings(ws["id"])
    data = clean(data)
    defs = effective_defs(data)
    custom = {n for n in data["weights"]}
    recs = recommend.build(snap, picks_mod.score_workspace(snap, defs, data["current_models"]), custom)
    prev = recommendations(ws["id"])
    same = prev is not None and {k: v for k, v in prev.items() if k != "generated_at"} == {k: v for k, v in recs.items() if k != "generated_at"}
    if same:
        return prev
    now = time.time()
    db.run("INSERT INTO ws_recommendations (workspace_id, data, updated_at) VALUES (?,?,?) "
           "ON CONFLICT(workspace_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
           ws["id"], json.dumps(recs), now)
    changed = recommend.changes(prev, recs) if prev is not None else {}
    for alias, (frm, to) in changed.items():
        db.run("INSERT INTO pick_history (workspace_id, alias, from_model, to_model, at) VALUES (?,?,?,?,?)",
               ws["id"], alias, frm, to, now)
    if changed and notify and plans.allows(ws, "webhooks") and (data.get("webhook") or {}).get("url"):
        threading.Thread(target=send_webhook, args=(data["webhook"], ws["id"], changed, recs.get("generated_at")), daemon=True).start()
    return recs


def history(ws, limit=100):
    days = plans.limit(ws, "history_days")
    since = time.time() - days * 86400 if days else 0
    return db.all_("SELECT alias, from_model, to_model, at FROM pick_history WHERE workspace_id=? AND at>=? "
                   "ORDER BY at DESC LIMIT ?", ws["id"], since, limit)


def on_snapshot(snap):
    """Build hook: every workspace re-scores against the new market data."""
    remember_snapshot(snap)
    for ws in db.all_("SELECT * FROM workspaces"):
        try:
            recompute(ws, snap)
        except Exception:  # noqa: BLE001 - one bad workspace must not stop the others
            log.exception("re-scoring workspace %s failed", ws["id"])


# ── webhooks ─────────────────────────────────────────────────────────────────────────
def check_webhook_url(url):
    """Webhooks go to public https endpoints in the cloud (no SSRF into our own network)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Webhook URL must start with https://")
    if not settings.get().cloud:
        return
    if p.scheme != "https":
        raise ValueError("Webhook URL must use https")
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)}
    except OSError:
        raise ValueError("Webhook host does not resolve") from None
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError("Webhook URL must be a public address")


def sign(secret, ts, body):
    return "v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def send_webhook(hook, wsid, changed, generated_at, event="picks.changed"):
    """POST the change, signed: X-Modelfit-Signature = v1=HMAC-SHA256(secret, "<timestamp>.<body>")."""
    try:
        check_webhook_url(hook["url"])
        body = json.dumps({"event": event, "workspace": wsid, "generated_at": generated_at,
                           "changed": {a: {"from": f, "to": t} for a, (f, t) in changed.items()}}).encode()
        ts = str(int(time.time()))
        headers = {"Content-Type": "application/json", "User-Agent": "Modelfit-Webhooks/1.0",
                   "X-Modelfit-Timestamp": ts, "X-Modelfit-Signature": sign(hook.get("secret") or "", ts, body)}
        if hook.get("legacy_token") and not settings.get().cloud:  # receivers built for the old local sync hook
            headers["X-Sync-Token"] = hook.get("secret") or ""
        urllib.request.urlopen(urllib.request.Request(hook["url"], data=body, headers=headers), timeout=HOOK_TIMEOUT).read()
        db.event("webhook.sent", wsid, aliases=sorted(changed))
        return True, None
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook for %s failed: %s", wsid, exc)
        db.event("webhook.failed", wsid, error=str(exc)[:200])
        return False, str(exc)[:200]


# ── self-hosted: one implicit workspace ──────────────────────────────────────────────
def selfhost_workspace():
    """The single workspace of a self-hosted install, created (and seeded from legacy files) on first use."""
    ws = db.one("SELECT * FROM workspaces WHERE id=?", SELFHOST_WS)
    if ws:
        return ws
    cfg = load_config()
    db.run("INSERT OR IGNORE INTO workspaces (id, name, plan, created_at) VALUES (?,?,?,?)",
           SELFHOST_WS, "Local workspace", "selfhost", time.time())
    data = _blank()
    for name, a in cfg["aliases"].items():
        data["alias_defs"][name] = {**copy.deepcopy(a), "preset": name}
    data["current_models"] = {a: m for a, m in (cfg.get("current_models") or {}).items() if m and a in cfg["aliases"]}
    legacy = W.read_legacy()
    if legacy:  # the weights.json an earlier single-user version wrote
        ctx = _ctx(data)
        data["weights"], data["caps"] = W.parse(legacy["aliases"], ctx), W.parse_caps(legacy["caps"], ctx)
        log.info("imported fit weights and caps from %s", legacy["path"])
    s = settings.get()
    old_hook = cfg.get("webhook") or {}
    url, secret = s.webhook_url or old_hook.get("url"), s.webhook_secret or old_hook.get("token")
    if url:
        data["webhook"] = {"url": url, "secret": secret or "whsec_" + secrets.token_urlsafe(24), "legacy_token": bool(secret)}
    init_workspace(SELFHOST_WS, data=data)
    return db.one("SELECT * FROM workspaces WHERE id=?", SELFHOST_WS)
