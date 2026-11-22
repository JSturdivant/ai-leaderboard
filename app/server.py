#!/usr/bin/env python3
"""Modelfit web server: marketing pages, the app, its JSON API and the public /v1 API.

  python3 server.py                  self-hosted: http://127.0.0.1:8787, no sign-in, data refreshed in the background
  python3 server.py --open           ...and open it in your browser
  MODELFIT_MODE=cloud python3 server.py   the multi-tenant SaaS (accounts, plans, billing); see .env.example
  python3 server.py --no-schedule    serve whatever is in data/latest.json without refreshing

Standard library only. In production run it behind a TLS-terminating proxy (see docs/ARCHITECTURE.md).
"""

import argparse
import json
import logging
import mimetypes
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from modelfit import DATA_DIR, LATEST, ROOT
from modelfit import auth, billing, build, db, plans, ranker, ratelimit, router, settings, tenants, triage
from modelfit.scheduler import Scheduler
from modelfit.store import load_config, load_status

WEB = ROOT / "web"
MAX_BODY = 64 * 1024
GATEWAY_BODY = 8 * 1024 * 1024   # chat requests carry whole conversations
GATEWAY_TIMEOUT = 180
CSRF_HEADER = "modelfit"
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")
log = logging.getLogger("server")

PAGES = {"/": "index.html", "/pricing": "pricing.html", "/login": "auth.html", "/signup": "auth.html",
         "/app": "app.html", "/docs": "docs.html", "/developers": "developers.html"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


class Ctx:
    """Who is calling: user (None for guests), workspace, role, and how they authenticated."""

    def __init__(self, user=None, ws=None, role=None, via=None, token=None):
        self.user, self.ws, self.role, self.via, self.token = user, ws, role, via, token

    @property
    def admin(self):
        return self.role in ("owner", "admin")


ROUTES = []


def route(method, pattern, auth_=None):
    """auth_: None (public), "user" (signed-in browser session), "key" (API key or session)."""
    rx = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, auth_, fn))
        return fn
    return deco


class Handler(SimpleHTTPRequestHandler):
    scheduler = None
    allowed_hosts = set()
    server_version = "Modelfit"
    sys_version = ""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    # ── plumbing ──────────────────────────────────────────────────────────────────────
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        if settings.get().secure_cookies:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        path = urlparse(self.path).path
        static = path.startswith(("/assets/", "/vendor/", "/fonts/")) or path.endswith((".css", ".js", ".svg", ".woff2", ".png", ".ico"))
        self.send_header("Cache-Control", "public, max-age=300" if static else "no-store")
        super().end_headers()

    def _json(self, obj, status=HTTPStatus.OK, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, body, ctype, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to):
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _host_ok(self):
        # blocks DNS rebinding: only our own host names may talk to us
        return (self.headers.get("Host") or "").lower() in self.allowed_hosts

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc.lower() in self.allowed_hosts

    def client_ip(self):
        # behind a proxy, trust its X-Forwarded-For only in cloud mode (where we deploy behind one)
        if settings.get().cloud and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].split(",")[0].strip()
        return self.client_address[0]

    def body(self, max_bytes=MAX_BODY):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length > max_bytes:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"body must be at most {max_bytes // 1024} KB")
        if length <= 0:
            raise ApiError(HTTPStatus.BAD_REQUEST, "expected a JSON body")
        raw = self.rfile.read(length)
        self._raw_body = raw
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "malformed JSON") from None

    def raw_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            raise ApiError(HTTPStatus.BAD_REQUEST, "bad body")
        return self.rfile.read(length)

    def cookie(self, name):
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie") or "")
        except Exception:  # noqa: BLE001 - a malformed cookie header is just "no cookie"
            return None
        return c[name].value if name in c else None

    # ── who is calling ────────────────────────────────────────────────────────────────
    def context(self):
        s = settings.get()
        if not s.cloud:
            return Ctx({"id": "local", "email": "", "name": "You"}, tenants.selfhost_workspace(), "owner", "selfhost")
        token = self.cookie(auth.COOKIE)
        found = auth.session(token)
        if found:
            user, ws, role = found
            return Ctx(user, ws, role, "session", token)
        return Ctx()

    def key_context(self):
        hdr = self.headers.get("Authorization") or ""
        if hdr.lower().startswith("bearer "):
            ws = auth.key_workspace(hdr[7:].strip())
            if not ws:
                raise ApiError(HTTPStatus.UNAUTHORIZED, "invalid or revoked API key")
            return Ctx(None, ws, "api", "key")
        ctx = self.context()
        if ctx.ws is None:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "send your API key as 'Authorization: Bearer mf_live_...'")
        return ctx

    def rank_context(self):
        """The ranking API accepts a workspace key, the public beta key, a signed-in session, or (self-hosted) nothing."""
        s = settings.get()
        hdr = self.headers.get("Authorization") or ""
        if hdr.lower().startswith("bearer "):
            token = hdr[7:].strip()
            if s.beta_enabled and s.beta_key and token == s.beta_key:
                return Ctx(None, None, "beta", "beta")
            ws = auth.key_workspace(token)
            if not ws:
                raise ApiError(HTTPStatus.UNAUTHORIZED, "invalid or revoked API key" + (f"; the public beta key is {s.beta_key}" if s.beta_enabled else ""))
            return Ctx(None, ws, "api", "key")
        ctx = self.context()
        if ctx.ws is None:
            hint = f" (free beta: 'Authorization: Bearer {s.beta_key}')" if s.beta_enabled else ""
            raise ApiError(HTTPStatus.UNAUTHORIZED, "send an API key as 'Authorization: Bearer <key>'" + hint)
        return ctx

    # ── dispatch ──────────────────────────────────────────────────────────────────────
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        try:
            self._route(method)
        finally:
            db.release()

    def _route(self, method):
        url = urlparse(self.path)
        path = url.path
        self.query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            if not self._host_ok() and path != "/healthz":  # health checks come from the platform, any Host
                raise ApiError(HTTPStatus.FORBIDDEN, "forbidden")
            for m, rx, auth_, fn in ROUTES:
                hit = rx.match(path)
                if not hit or m != method:
                    continue
                writes = method != "GET"
                is_webhook = path == "/api/billing/webhook"
                # API calls (agents, SDKs) cannot send our CSRF header; a JSON body is preflight-protected instead
                api_json = auth_ in ("key", "rank") and (self.headers.get("Content-Type") or "").split(";")[0].strip() == "application/json"
                if writes and not is_webhook and not api_json and (self.headers.get("X-Requested-With") != CSRF_HEADER or not self._origin_ok()):
                    raise ApiError(HTTPStatus.FORBIDDEN, "forbidden")
                if auth_ == "key":
                    ctx = self.key_context()
                elif auth_ == "rank":
                    ctx = self.rank_context()
                elif is_webhook:
                    ctx = Ctx()
                else:
                    ctx = self.context()
                    if auth_ == "user" and ctx.user is None:
                        raise ApiError(HTTPStatus.UNAUTHORIZED, "sign in first", signin="/login")
                out = fn(self, ctx, **{k: unquote(v) for k, v in hit.groupdict().items()})
                if out is None:  # the handler wrote the response itself
                    return
                status, payload, *rest = out if isinstance(out, tuple) else (HTTPStatus.OK, out)
                return self._json(payload, status, rest[0] if rest else None)
            if method != "GET":
                raise ApiError(HTTPStatus.NOT_FOUND, "not found")
            if path.startswith(("/api/", "/v1/")):
                raise ApiError(HTTPStatus.NOT_FOUND, "not found")
            return self._page(path)
        except ApiError as e:
            self._json({"error": str(e), **e.extra}, e.status)
        except plans.Upgrade as e:
            self._json({"error": str(e), "upgrade": e.plan, "feature": e.feature}, HTTPStatus.PAYMENT_REQUIRED)
        except auth.AuthError as e:
            self._json({"error": str(e)}, e.status)
        except billing.BillingError as e:
            self._json({"error": str(e)}, HTTPStatus.BAD_GATEWAY)
        except (ValueError, KeyError) as e:
            self._json({"error": str(e).strip("'\"")}, HTTPStatus.BAD_REQUEST if isinstance(e, ValueError) else HTTPStatus.NOT_FOUND)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001
            log.exception("unhandled error on %s %s", method, path)
            try:
                self._json({"error": "internal error"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            except OSError:
                pass

    def _page(self, path):
        s = settings.get()
        if path == "/" and not s.cloud:
            return self._redirect("/app")
        if path in ("/login", "/signup") and s.cloud and self.context().user:
            return self._redirect("/app")
        if path in ("/login", "/signup", "/pricing") and not s.cloud:
            return self._redirect("/app")
        page = PAGES.get(path.rstrip("/") or "/")
        if page:
            return self._bytes((WEB / page).read_bytes(), "text/html; charset=utf-8")
        if path.endswith(".html") or "/." in path:
            return self._bytes((WEB / "404.html").read_bytes(), "text/html; charset=utf-8", HTTPStatus.NOT_FOUND)
        target = (WEB / path.lstrip("/")).resolve()
        if not str(target).startswith(str(WEB.resolve())) or not target.is_file():
            return self._bytes((WEB / "404.html").read_bytes(), "text/html; charset=utf-8", HTTPStatus.NOT_FOUND)
        return super().do_GET()


# ── helpers ─────────────────────────────────────────────────────────────────────────────
def ws_summary(ws):
    eff = plans.effective_plan(ws)
    return {"id": ws["id"], "name": ws["name"], "plan": ws["plan"], "effective_plan": eff, "plan_status": ws["plan_status"],
            "plan_interval": ws.get("plan_interval"), "current_period_end": ws.get("current_period_end"), "seats": ws.get("seats"),
            "entitlements": plans.get(eff), "billing_account": bool(ws.get("stripe_customer_id"))}


def usage(ws):
    data, _, _ = tenants.get_settings(ws["id"])
    return {"aliases": len(data["alias_defs"]), "api_requests_today": db.usage_today(ws["id"], "api"),
            "api_keys": len(auth.list_keys(ws["id"])),
            "members": db.one("SELECT COUNT(*) AS n FROM members WHERE workspace_id=?", ws["id"])["n"]}


def guest_workspace():
    """What a signed-out visitor sees in the app: the starter aliases, read-only."""
    cfg = load_config()
    names = (cfg.get("workspace_defaults") or {}).get("starter_aliases") or list(cfg["aliases"])
    defs = {n: {**cfg["aliases"][n], "preset": n} for n in names if n in cfg["aliases"]}
    return {"alias_defs": defs, "weights": {}, "caps": {}, "current_models": {}, "version": 0, "webhook": {"url": ""}}


def need_admin(ctx):
    if not ctx.admin:
        raise ApiError(HTTPStatus.FORBIDDEN, "only workspace owners and admins can do that")


def write_limit(ctx):
    if not ratelimit.WRITES.allow(ctx.ws["id"]):
        raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, "slow down")


def workspace_payload(ctx):
    data, ver, _ = tenants.get_settings(ctx.ws["id"])
    out = tenants.public_settings(ctx.ws, data, ver)
    out["recommendations"] = tenants.recommendations(ctx.ws["id"])
    return out


# ── public ──────────────────────────────────────────────────────────────────────────────
@route("GET", "/healthz")
def healthz(h, ctx):
    db.one("SELECT 1 AS ok")
    return {"ok": True, "snapshot": LATEST.exists()}


@route("GET", "/api/config")
def api_config(h, ctx):
    s = settings.get()
    return {"mode": s.mode, "dev_billing": s.dev_billing, "billing_live": s.billing_live, "presets": list(load_config()["aliases"]),
            "beta_key": s.beta_key if s.beta_enabled else None, "beta_daily": s.beta_daily}


@route("GET", "/api/plans")
def api_plans(h, ctx):
    return {"plans": plans.public(), "features": plans.FEATURE_LABELS}


@route("GET", "/data/latest.json")
@route("GET", "/api/snapshot")
def api_snapshot(h, ctx):
    if not LATEST.exists():
        raise ApiError(HTTPStatus.NOT_FOUND, "no data yet")
    h._bytes(LATEST.read_bytes(), "application/json")


@route("GET", "/api/status")
def api_status(h, ctx):
    st = h.scheduler.state if h.scheduler else {}
    return {"generated_ts": LATEST.stat().st_mtime if LATEST.exists() else None, "now": time.time(),
            "running": st.get("running", False), "step": st.get("step"), "last_error": st.get("last_error"),
            "scheduler": h.scheduler is not None, "can_refresh": h.scheduler is not None and not settings.get().cloud,
            "sources": load_status()}


@route("POST", "/api/refresh")
def api_refresh(h, ctx):
    if settings.get().cloud:
        raise ApiError(HTTPStatus.FORBIDDEN, "data refreshes on a schedule in the cloud")
    if h.scheduler is None:
        raise ApiError(HTTPStatus.CONFLICT, "scheduler disabled (--no-schedule)")
    h.scheduler.trigger(force=True)
    return HTTPStatus.ACCEPTED, {"queued": True}


@route("GET", "/api/public/highlights")
def api_highlights(h, ctx):
    """Landing-page numbers: models tracked, sources, and today's #1 for a few presets."""
    snap = tenants.snapshot()
    if not snap:
        return {"ready": False}
    by = {m["id"]: m for m in snap["models"]}
    picks = []
    for alias in ("Daily", "Coding", "Thinking", "Cheap"):
        p = snap["alias_picks"].get(alias)
        if p and p["shortlist"]:
            m = by[p["shortlist"][0]]
            picks.append({"alias": alias, "model": m["id"], "name": m["name"].split(": ", 1)[-1], "fit": m["fit"][alias],
                          "cost": m["cost"]["lowest"]["blended"], "provider": m["cost"]["lowest"]["provider"]})
    return {"ready": True, "models": len(snap["models"]), "benchmarks": sum(1 for m in snap["metrics"].values() if m.get("scale")),
            "sources": len(snap["sources"]), "generated_at": snap["generated_at"], "picks": picks}


# ── accounts ────────────────────────────────────────────────────────────────────────────
def _auth_limit(h):
    if not ratelimit.AUTH.allow(h.client_ip()):
        raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, "Too many attempts. Wait a minute and try again.")


@route("POST", "/api/auth/signup")
def api_signup(h, ctx):
    if not settings.get().cloud:
        raise ApiError(HTTPStatus.NOT_FOUND, "accounts are a cloud feature")
    _auth_limit(h)
    b = h.body()
    uid, wsid = auth.signup(b.get("email"), b.get("password"), b.get("name"))
    token = auth.create_session(uid, wsid)
    return HTTPStatus.CREATED, {"ok": True, "next": "/app?welcome=1"}, {"Set-Cookie": auth.cookie_header(token)}


@route("POST", "/api/auth/login")
def api_login(h, ctx):
    if not settings.get().cloud:
        raise ApiError(HTTPStatus.NOT_FOUND, "accounts are a cloud feature")
    _auth_limit(h)
    b = h.body()
    u = auth.login(b.get("email"), b.get("password"))
    wsid = auth.default_workspace(u["id"])
    if not wsid:
        raise ApiError(HTTPStatus.FORBIDDEN, "this account has no workspace")
    token = auth.create_session(u["id"], wsid)
    db.event("login", wsid, u["id"])
    return HTTPStatus.OK, {"ok": True, "next": "/app"}, {"Set-Cookie": auth.cookie_header(token)}


@route("POST", "/api/auth/logout")
def api_logout(h, ctx):
    auth.end_session(ctx.token)
    return HTTPStatus.OK, {"ok": True}, {"Set-Cookie": auth.cookie_header("", max_age=0)}


@route("GET", "/api/me")
def api_me(h, ctx):
    s = settings.get()
    if ctx.ws is None:
        return {"authenticated": False, "mode": s.mode, "dev_billing": s.dev_billing}
    return {"authenticated": True, "mode": s.mode, "dev_billing": s.dev_billing, "user": ctx.user, "role": ctx.role,
            "workspace": ws_summary(ctx.ws), "usage": usage(ctx.ws)}


# ── workspace ───────────────────────────────────────────────────────────────────────────
@route("GET", "/api/workspace")
def api_workspace(h, ctx):
    if ctx.ws is None:
        return {**guest_workspace(), "guest": True, "recommendations": None}
    return workspace_payload(ctx)


@route("GET", "/api/workspace/version")
def api_workspace_version(h, ctx):
    return {"version": tenants.version(ctx.ws["id"]) if ctx.ws else 0}


@route("PUT", "/api/workspace/tuning", "user")
def api_tuning(h, ctx):
    write_limit(ctx)
    b = h.body()
    if not isinstance(b, dict) or "weights" not in b:
        raise ApiError(HTTPStatus.BAD_REQUEST, 'expected {"weights": {...}, "caps": {...}}')
    _, ver = tenants.save_tuning(ctx.ws, b["weights"], b.get("caps"))
    return workspace_payload(ctx)


@route("POST", "/api/workspace/aliases", "user")
def api_add_alias(h, ctx):
    write_limit(ctx)
    b = h.body()
    tenants.add_alias(ctx.ws, b.get("name"), b.get("definition"), b.get("preset"))
    return HTTPStatus.CREATED, workspace_payload(ctx)


@route("DELETE", "/api/workspace/aliases/<name>", "user")
def api_remove_alias(h, ctx, name):
    write_limit(ctx)
    tenants.remove_alias(ctx.ws, name)
    return workspace_payload(ctx)


@route("PUT", "/api/workspace/current", "user")
def api_current(h, ctx):
    write_limit(ctx)
    b = h.body()
    tenants.set_current(ctx.ws, b.get("alias"), b.get("model"))
    return workspace_payload(ctx)


@route("PUT", "/api/workspace/webhook", "user")
def api_webhook(h, ctx):
    need_admin(ctx)
    b = h.body()
    hook, _ = tenants.set_webhook(ctx.ws, b.get("url"), bool(b.get("rotate")))
    return {"url": hook["url"], "secret": hook["secret"]}


@route("POST", "/api/workspace/webhook/test", "user")
def api_webhook_test(h, ctx):
    need_admin(ctx)
    plans.require(ctx.ws, "webhooks")
    data, _, _ = tenants.get_settings(ctx.ws["id"])
    hook = data.get("webhook") or {}
    if not hook.get("url"):
        raise ApiError(HTTPStatus.BAD_REQUEST, "set a webhook URL first")
    ok, err = tenants.send_webhook(hook, ctx.ws["id"], {"Example": ("old/model", "new/model")}, None, event="test")
    return {"ok": ok, "error": err}


@route("GET", "/api/workspace/history", "user")
def api_history(h, ctx):
    return {"history": tenants.history(ctx.ws), "days": plans.limit(ctx.ws, "history_days")}


@route("PUT", "/api/workspace/name", "user")
def api_ws_name(h, ctx):
    need_admin(ctx)
    name = str(h.body().get("name") or "").strip()[:60]
    if not name:
        raise ApiError(HTTPStatus.BAD_REQUEST, "name required")
    db.run("UPDATE workspaces SET name=? WHERE id=?", name, ctx.ws["id"])
    return {"ok": True, "name": name}


# ── API keys ────────────────────────────────────────────────────────────────────────────
@route("GET", "/api/keys", "user")
def api_keys(h, ctx):
    return {"keys": auth.list_keys(ctx.ws["id"]), "limit": plans.limit(ctx.ws, "api_keys")}


@route("POST", "/api/keys", "user")
def api_key_create(h, ctx):
    need_admin(ctx)
    plans.check_limit(ctx.ws, "api_keys", len(auth.list_keys(ctx.ws["id"])))
    k = auth.create_key(ctx.ws["id"], h.body().get("name"))
    db.event("key.created", ctx.ws["id"], ctx.user["id"], key=k["id"])
    return HTTPStatus.CREATED, k


@route("DELETE", "/api/keys/<kid>", "user")
def api_key_revoke(h, ctx, kid):
    need_admin(ctx)
    if not auth.revoke_key(ctx.ws["id"], kid):
        raise ApiError(HTTPStatus.NOT_FOUND, "no such key")
    db.event("key.revoked", ctx.ws["id"], ctx.user["id"], key=kid)
    return {"ok": True}


# ── team ────────────────────────────────────────────────────────────────────────────────
@route("GET", "/api/members", "user")
def api_members(h, ctx):
    rows = db.all_("SELECT u.id, u.email, u.name, m.role, m.created_at FROM members m JOIN users u ON u.id=m.user_id "
                   "WHERE m.workspace_id=? ORDER BY m.created_at", ctx.ws["id"])
    return {"members": rows, "limit": plans.limit(ctx.ws, "members")}


@route("POST", "/api/members", "user")
def api_member_add(h, ctx):
    """Add an existing Modelfit user to a Team workspace (email invitations: see the roadmap)."""
    need_admin(ctx)
    plans.require(ctx.ws, "team")
    b = h.body()
    n = db.one("SELECT COUNT(*) AS n FROM members WHERE workspace_id=?", ctx.ws["id"])["n"]
    plans.check_limit(ctx.ws, "members", n)
    u = db.one("SELECT id FROM users WHERE email=?", auth.normalise_email(b.get("email")))
    if not u:
        raise ApiError(HTTPStatus.NOT_FOUND, "No account with that email yet. Ask them to sign up first.")
    role = b.get("role") if b.get("role") in ("admin", "member") else "member"
    db.run("INSERT OR IGNORE INTO members (workspace_id, user_id, role, created_at) VALUES (?,?,?,?)", ctx.ws["id"], u["id"], role, time.time())
    db.event("member.added", ctx.ws["id"], ctx.user["id"], member=u["id"])
    return HTTPStatus.CREATED, {"ok": True}


@route("DELETE", "/api/members/<uid>", "user")
def api_member_remove(h, ctx, uid):
    need_admin(ctx)
    m = db.one("SELECT role FROM members WHERE workspace_id=? AND user_id=?", ctx.ws["id"], uid)
    if not m:
        raise ApiError(HTTPStatus.NOT_FOUND, "not a member")
    if m["role"] == "owner":
        raise ApiError(HTTPStatus.BAD_REQUEST, "the owner cannot be removed")
    db.run("DELETE FROM members WHERE workspace_id=? AND user_id=?", ctx.ws["id"], uid)
    return {"ok": True}


# ── billing ─────────────────────────────────────────────────────────────────────────────
@route("POST", "/api/billing/checkout", "user")
def api_checkout(h, ctx):
    need_admin(ctx)
    if not settings.get().cloud:
        raise ApiError(HTTPStatus.NOT_FOUND, "billing is a cloud feature")
    b = h.body()
    return billing.checkout(ctx.ws, ctx.user, b.get("plan"), b.get("interval") or "month")


@route("POST", "/api/billing/portal", "user")
def api_portal(h, ctx):
    need_admin(ctx)
    if not settings.get().cloud:
        raise ApiError(HTTPStatus.NOT_FOUND, "billing is a cloud feature")
    return billing.portal(ctx.ws)


@route("POST", "/api/billing/webhook")
def api_stripe_webhook(h, ctx):
    payload = h.raw_body()
    if not billing.verify(payload, h.headers.get("Stripe-Signature"), settings.get().stripe_webhook_secret):
        raise ApiError(HTTPStatus.BAD_REQUEST, "bad signature")
    result = billing.handle_event(json.loads(payload))
    return {"received": True, "result": result}


# ── public API (/v1) ────────────────────────────────────────────────────────────────────
def metered(h, ctx):
    """Daily quota per plan + burst smoothing; sets the X-RateLimit headers."""
    lim = plans.limit(ctx.ws, "api_requests_per_day")
    if not ratelimit.API.allow(ctx.ws["id"]):
        raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, "too many requests per minute")
    used = db.bump_usage(ctx.ws["id"], "api")
    if lim is not None and used > lim:
        raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, f"daily limit of {lim} requests reached", upgrade="pro")
    return {"X-RateLimit-Limit": str(lim) if lim is not None else "unlimited",
            "X-RateLimit-Remaining": str(max(0, lim - used)) if lim is not None else "unlimited"}


@route("GET", "/v1/recommendations", "key")
def v1_recs(h, ctx):
    hdrs = metered(h, ctx)
    recs = tenants.recommendations(ctx.ws["id"]) or tenants.recompute(ctx.ws)
    if not recs:
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "market data is still loading")
    return HTTPStatus.OK, recs, hdrs


@route("GET", "/v1/recommendations/<alias>", "key")
def v1_rec(h, ctx, alias):
    hdrs = metered(h, ctx)
    recs = tenants.recommendations(ctx.ws["id"]) or tenants.recompute(ctx.ws) or {}
    entry = (recs.get("aliases") or {}).get(alias)
    if entry is None:
        raise ApiError(HTTPStatus.NOT_FOUND, f"no alias called {alias!r} in this workspace")
    return HTTPStatus.OK, {"alias": alias, "generated_at": recs["generated_at"], **entry}, hdrs


@route("GET", "/v1/market", "key")
def v1_market(h, ctx):
    """The market snapshot: every purchasable model with its lowest price and use-case scores."""
    hdrs = metered(h, ctx)
    snap = tenants.snapshot()
    if not snap:
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "market data is still loading")
    q = (h.query.get("q") or "").lower()
    try:
        limit = max(1, min(500, int(h.query.get("limit") or 100)))
    except ValueError:
        limit = 100
    rows = [m for m in snap["models"] if not q or q in (m["id"] + " " + m["name"]).lower()]
    out = [{"id": m["id"], "name": m["name"], "released": m["released"], "context": m["ctx"], "tools": m["tools"],
            "reasoning": m["reasoning"], "cost": m["cost"]["lowest"], "scores": {c: s["s"] for c, s in m["scores"].items()}}
           for m in rows[:limit]]
    return HTTPStatus.OK, {"generated_at": snap["generated_at"], "count": len(rows), "models": out}, hdrs


# ── routing (/v1/routes, and the OpenAI-compatible gateway) ─────────────────────────────
def _snap_or_503():
    snap = tenants.snapshot()
    if not snap:
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "market data is still loading")
    return snap


def _ensure_routes(ws, snap):
    routes, _ = router.load(ws["id"])
    if not routes:
        tenants.recompute(ws, snap)
    return snap


def _needs_from_query(q):
    needs = {}
    if q.get("tools") in ("1", "true", "yes"):
        needs["tools"] = True
    try:
        if q.get("context"):
            needs["context"] = max(0, int(q["context"]))
    except ValueError:
        raise ApiError(HTTPStatus.BAD_REQUEST, "context must be a token count") from None
    return needs


@route("GET", "/v1/models", "key")
def v1_models(h, ctx):
    """OpenAI-compatible model list: your aliases, each resolving to its live route (so SDKs can list them)."""
    hdrs = metered(h, ctx)
    snap = _ensure_routes(ctx.ws, _snap_or_503())
    routes, _ = router.load(ctx.ws["id"])
    data = [{"id": alias, "object": "model", "created": int(r.get("since") or 0), "owned_by": "modelfit",
             "modelfit": {"routes_to": r.get("model"), "mode": r.get("mode"), "fallbacks": r.get("fallbacks")}}
            for alias, r in routes.items()]
    return HTTPStatus.OK, {"object": "list", "data": data}, hdrs


@route("GET", "/v1/routes", "key")
def v1_routes(h, ctx):
    hdrs = metered(h, ctx)
    snap = _ensure_routes(ctx.ws, _snap_or_503())
    routes, ver = router.load(ctx.ws["id"])
    needs = _needs_from_query(h.query)
    return HTTPStatus.OK, {"version": ver, "generated_at": snap["generated_at"], "ttl_seconds": router.TTL,
                           "routes": {a: router.resolve(ctx.ws, a, snap, needs) for a in routes}}, hdrs


@route("GET", "/v1/routes/<alias>", "key")
def v1_route(h, ctx, alias):
    hdrs = metered(h, ctx)
    snap = _ensure_routes(ctx.ws, _snap_or_503())
    out = router.resolve(ctx.ws, alias, snap, _needs_from_query(h.query))
    etag = f'"{ctx.ws["id"]}-{out["version"]}-{out["model"]}"'
    hdrs.update({"ETag": etag, "Cache-Control": f"private, max-age={min(router.TTL, 60)}"})
    return HTTPStatus.OK, out, hdrs


def oai_error(h, status, message, code=None, headers=None, **extra):
    """OpenAI-style error body, so SDKs surface our message."""
    h._json({"error": {"message": message, "type": "invalid_request_error" if status < 500 else "upstream_error",
                       "code": code, **extra}}, status, headers)


@route("POST", "/v1/chat/completions", "key")
def v1_chat(h, ctx):
    """OpenAI-compatible gateway: `model` is an alias; Modelfit picks the model and fails over down the route."""
    t0 = time.time()
    wsid = ctx.ws["id"]
    if not ratelimit.GATEWAY.allow(wsid):
        return oai_error(h, 429, "too many requests per minute", "rate_limited")
    lim = plans.limit(ctx.ws, "gateway_requests_per_day")
    used = db.bump_usage(wsid, "gateway")
    if lim is not None and used > lim:
        return oai_error(h, 429, f"daily gateway limit of {lim} requests reached", "quota_exceeded", upgrade="pro")
    try:
        body = h.body(GATEWAY_BODY)
    except ApiError as e:
        return oai_error(h, e.status, str(e))
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return oai_error(h, 400, "expected an OpenAI chat-completions body with messages")
    snap = tenants.snapshot()
    if not snap:
        return oai_error(h, 503, "market data is still loading")
    by = {m["id"]: m for m in snap["models"]}
    requested = str(body.get("model") or "")
    name = requested.split("/", 1)[1] if requested.lower().startswith("modelfit/") else requested
    _ensure_routes(ctx.ws, snap)
    routes, _ = router.load(wsid)
    alias = next((a for a in routes if a.lower() == name.lower()), None)
    needs = router.request_needs(body)
    if alias:
        chain = router.serving_chain(routes[alias], by, needs, gateway_only=True)
        if not chain:
            return oai_error(h, 422, f"no model routed for {alias} can serve this request ({router.needs_text(needs)}); "
                                     "widen the alias's cap or requirements", "no_route")
    elif "/" in requested and requested in by:
        chain = [requested]  # a concrete model id passes straight through (no fallback)
    else:
        return oai_error(h, 404, f"unknown model {requested!r}: use one of your aliases ({', '.join(routes) or 'none yet'})", "model_not_found")
    upstream_key = router.provider_key(wsid)
    if not upstream_key:
        return oai_error(h, 400, "add your OpenRouter API key under Routing in the Modelfit app to use the gateway", "no_provider_key")
    stream = bool(body.get("stream"))
    s = settings.get()
    attempts, last = 0, (502, "no attempt made")
    for mid in chain:
        attempts += 1
        payload = {**body, "model": mid}
        payload.setdefault("provider", {"sort": "price"})
        req = urllib.request.Request(s.upstream_url + "/chat/completions", data=json.dumps(payload).encode(), headers={
            "Authorization": f"Bearer {upstream_key}", "Content-Type": "application/json", "User-Agent": "Modelfit-Gateway/1.0",
            "HTTP-Referer": s.base_url or "https://modelfit.ai", "X-Title": "Modelfit"})
        try:
            resp = urllib.request.urlopen(req, timeout=GATEWAY_TIMEOUT)
        except urllib.error.HTTPError as e:
            detail = e.read()[:2000]
            if e.code in router.FALLBACK_STATUSES:
                last = (e.code, detail.decode("utf-8", "replace"))
                continue
            router.log_request(wsid, alias=alias, model=mid, status=e.code, attempts=attempts, stream=stream,
                               latency_ms=int((time.time() - t0) * 1000), error=detail.decode("utf-8", "replace"))
            h._bytes(detail or b"{}", e.headers.get("Content-Type") or "application/json", e.code)
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = (502, str(e))
            continue
        route_headers = {"X-Modelfit-Alias": alias or "", "X-Modelfit-Model": mid, "X-Modelfit-Attempts": str(attempts)}
        if stream:
            usage = _relay_stream(h, resp, route_headers)
        else:
            raw = resp.read()
            try:
                out = json.loads(raw)
                usage = out.get("usage")
                out["modelfit"] = {"alias": alias, "model": mid, "attempts": attempts}
                h._json(out, HTTPStatus.OK, route_headers)
            except ValueError:
                usage = None
                h._bytes(raw, resp.headers.get("Content-Type") or "application/json")
        router.log_request(wsid, alias=alias, model=mid, status=200, attempts=attempts, stream=stream,
                           latency_ms=int((time.time() - t0) * 1000),
                           prompt_tokens=(usage or {}).get("prompt_tokens"), completion_tokens=(usage or {}).get("completion_tokens"),
                           cost_usd=router.cost_of(by[mid], usage) if mid in by else None)
        return None
    router.log_request(wsid, alias=alias, model=chain[-1], status=last[0], attempts=attempts, stream=stream,
                       latency_ms=int((time.time() - t0) * 1000), error=str(last[1]))
    return oai_error(h, 502, f"every model on the route failed ({attempts} tried); last error {last[0]}: {str(last[1])[:300]}", "route_exhausted")


def _relay_stream(h, resp, headers):
    """Pass server-sent events through as they arrive; pick up the usage block if the upstream sends one."""
    h.send_response(HTTPStatus.OK)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("X-Accel-Buffering", "no")
    for k, v in headers.items():
        h.send_header(k, v)
    h.end_headers()
    usage = None
    while True:
        line = resp.readline()
        if not line:
            break
        if line.startswith(b"data: ") and b'"usage"' in line:
            try:
                usage = json.loads(line[6:]).get("usage") or usage
            except ValueError:
                pass
        try:
            h.wfile.write(line)
            if line in (b"\n", b"\r\n"):
                h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            break
    h.close_connection = True
    return usage


# ── routing settings (the app) ──────────────────────────────────────────────────────────
@route("GET", "/api/routing")
def api_routing(h, ctx):
    if ctx.ws is None:
        raise ApiError(HTTPStatus.UNAUTHORIZED, "sign in first", signin="/login")
    snap = tenants.snapshot()
    if snap:
        _ensure_routes(ctx.ws, snap)
    routes, ver = router.load(ctx.ws["id"])
    by = {m["id"]: m for m in (snap or {}).get("models", [])}
    fit = lambda r, m: (r.get("fit_of") or {}).get(m)  # noqa: E731
    view = {a: {**r, "details": router.model_brief(by[r["model"]], r.get("fit")) if r.get("model") in by else None,
                "fallback_details": [router.model_brief(by[m], fit(r, m)) for m in r.get("fallbacks") or [] if m in by]}
            for a, r in routes.items()}
    data, _, _ = tenants.get_settings(ctx.ws["id"])
    return {"routes": view, "version": ver, "config": tenants.clean(data)["routes"], "providers": router.provider_status(ctx.ws["id"]),
            "usage": router.usage_summary(ctx.ws["id"]), "gateway_today": db.usage_today(ctx.ws["id"], "gateway"),
            "limits": {"gateway_requests_per_day": plans.limit(ctx.ws, "gateway_requests_per_day"), "fallbacks": plans.limit(ctx.ws, "fallbacks")},
            "defaults": router.DEFAULTS}


@route("PUT", "/api/routing/routes/<alias>", "user")
def api_route_config(h, ctx, alias):
    write_limit(ctx)
    tenants.set_route(ctx.ws, alias, h.body())
    return api_routing(h, ctx)


@route("PUT", "/api/routing/providers/<provider>", "user")
def api_provider_key(h, ctx, provider):
    need_admin(ctx)
    router.set_provider_key(ctx.ws["id"], provider, h.body().get("key"))
    db.event("provider_key.set", ctx.ws["id"], ctx.user["id"], provider=provider)
    return {"providers": router.provider_status(ctx.ws["id"])}


@route("DELETE", "/api/routing/providers/<provider>", "user")
def api_provider_key_delete(h, ctx, provider):
    need_admin(ctx)
    router.delete_provider_key(ctx.ws["id"], provider)
    return {"providers": router.provider_status(ctx.ws["id"])}


# ── model ranking API (/v1/rank, /v1/triage) ────────────────────────────────────────────
def rank_metered(h, ctx):
    """Beta key: per-IP burst + daily limits. Workspace key or session: the plan's API quota. Self-hosted: none."""
    s = settings.get()
    if ctx.via == "beta":
        ip = h.client_ip()
        if not ratelimit.BETA.allow(ip):
            raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, "beta key: too many requests per minute from this address")
        used = db.bump_usage(f"beta:{ip}", "rank")
        if used > s.beta_daily:
            raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, f"beta key: daily limit of {s.beta_daily} requests per address reached; "
                           "create a free account for your own key", signup="/signup")
        return {"X-RateLimit-Limit": str(s.beta_daily), "X-RateLimit-Remaining": str(max(0, s.beta_daily - used)), "X-Modelfit-Beta": "true"}
    if ctx.via == "selfhost":
        return {"X-RateLimit-Limit": "unlimited"}
    return metered(h, ctx)


def _rank_body(h):
    body = h.body(GATEWAY_BODY)
    try:
        return ranker.parse(body)
    except ranker.RankError as e:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(e), field=e.field) from None


@route("POST", "/v1/rank", "rank")
def v1_rank(h, ctx):
    """Rank every purchasable model for one request (prompt / conversation / task + weights, limits, filters)."""
    hdrs = rank_metered(h, ctx)
    req = _rank_body(h)
    snap = tenants.snapshot()
    if not snap:
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "market data is still loading; try again in a minute")
    out = ranker.rank(snap, req)
    db.event("rank", ctx.ws["id"] if ctx.ws else None, None, via=ctx.via, category=out["triage"]["category"],
             candidates=out["candidates"])
    return HTTPStatus.OK, out, hdrs


@route("POST", "/v1/triage", "rank")
def v1_triage(h, ctx):
    """Just the classification: what a prompt / conversation / task needs from a model."""
    hdrs = rank_metered(h, ctx)
    req = _rank_body(h)
    out = triage.classify(req["prompt"], req["messages"], req["task"], req["tools"], req["response_format"])
    out.pop("empty", None)
    return HTTPStatus.OK, {"object": "triage", **out}, hdrs


@route("GET", "/v1/rank/options", "rank")
def v1_rank_options(h, ctx):
    snap = tenants.snapshot()
    if not snap:
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "market data is still loading")
    return ranker.options(snap)


# ── entry point ─────────────────────────────────────────────────────────────────────────
def local_hosts(host, port):
    hosts = {"localhost", "127.0.0.1", "[::1]", host}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        hosts.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return {f"{h}:{port}" for h in hosts}


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=cfg["server"]["host"])
    ap.add_argument("--port", type=int, default=cfg["server"]["port"])
    ap.add_argument("--open", action="store_true", help="open the page in a browser")
    ap.add_argument("--no-schedule", action="store_true", help="do not refresh data in the background")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    s = settings.get()
    DATA_DIR.mkdir(exist_ok=True)
    db.conn()  # create / migrate the database before taking traffic
    if not s.cloud:
        tenants.selfhost_workspace()
    build.ON_SNAPSHOT.append(tenants.on_snapshot)

    hosts = set(s.allowed_hosts)
    if not s.cloud or not s.base_url:  # local development of either mode
        hosts |= local_hosts(args.host, args.port)
    Handler.allowed_hosts = {h.lower() for h in hosts}
    if not args.no_schedule:
        Handler.scheduler = Scheduler()
        Handler.scheduler.start()
    elif tenants.snapshot():
        threading.Thread(target=tenants.on_snapshot, args=(tenants.snapshot(),), daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    shown = "localhost" if args.host in ("0.0.0.0", "127.0.0.1") else args.host
    url = f"http://{shown}:{args.port}/"
    log.info("Modelfit (%s mode%s) on %s", s.mode, ", dev billing" if s.dev_billing else "", url)
    if not LATEST.exists() and Handler.scheduler:
        log.info("First run: collecting market data (about 20 seconds); the page fills in when it is ready.")
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("bye")


if __name__ == "__main__":
    main()
