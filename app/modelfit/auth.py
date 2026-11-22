"""Accounts, sessions and API keys.

* Passwords: scrypt (N=2^14, r=8, p=1) with a per-user salt, compared in constant time.
* Sessions: 256-bit random tokens in an HttpOnly, SameSite=Lax cookie; only an HMAC of the token is stored.
* API keys: "mf_live_" + 32 random characters, shown once; stored as an HMAC with a short display prefix.
* CSRF: every state-changing browser request must carry X-Requested-With (a custom header a cross-site form
  cannot send) and, when present, an Origin on the allowed list. Enforced in server.py.
"""

import hashlib
import hmac
import re
import secrets
import time

from . import db, settings, tenants

SESSION_DAYS = 30
COOKIE = "mf_session"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
_DUMMY_HASH = None


class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ── passwords ────────────────────────────────────────────────────────────────────────
def hash_password(pw, salt=None, n=2 ** 14, r=8, p=1):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def check_password(pw, stored):
    try:
        _, n, r, p, salt, want = stored.split("$")
        got = hash_password(pw, bytes.fromhex(salt), int(n), int(r), int(p)).split("$")[-1]
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def _token_hash(token):
    return hmac.new(settings.get().secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


# ── accounts ─────────────────────────────────────────────────────────────────────────
def normalise_email(email):
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address")
    return email


def signup(email, password, name=""):
    """Create the user, their workspace (Free plan, starter aliases) and the owner membership."""
    email = normalise_email(email)
    if not isinstance(password, str) or len(password) < 8:
        raise AuthError("Use at least 8 characters for your password")
    if len(password) > 200:
        raise AuthError("That password is too long")
    name = (name or "").strip()[:80]
    pw_hash = hash_password(password)
    now = time.time()
    uid, wsid = db.new_id("usr"), db.new_id("ws")
    with db.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise AuthError("An account with that email already exists. Sign in instead.", 409)
        c.execute("INSERT INTO users (id, email, name, pw_hash, created_at) VALUES (?,?,?,?,?)", (uid, email, name, pw_hash, now))
        ws_name = f"{name.split()[0]}'s workspace" if name else "My workspace"
        c.execute("INSERT INTO workspaces (id, name, plan, created_at) VALUES (?,?,?,?)", (wsid, ws_name, "free", now))
        c.execute("INSERT INTO members (workspace_id, user_id, role, created_at) VALUES (?,?,?,?)", (wsid, uid, "owner", now))
    tenants.init_workspace(wsid, starter=True)
    db.event("signup", wsid, uid)
    return uid, wsid


def login(email, password):
    try:
        email = normalise_email(email)
    except AuthError:
        email = ""
    u = db.one("SELECT * FROM users WHERE email=?", email) if email else None
    if not u:
        global _DUMMY_HASH
        _DUMMY_HASH = _DUMMY_HASH or hash_password("not-a-real-password")
        check_password(password or "", _DUMMY_HASH)  # same work either way: no account-existence timing leak
        raise AuthError("Email or password is incorrect", 401)
    if not check_password(password or "", u["pw_hash"]):
        raise AuthError("Email or password is incorrect", 401)
    db.run("UPDATE users SET last_login_at=? WHERE id=?", time.time(), u["id"])
    return u


def default_workspace(user_id):
    r = db.one("SELECT workspace_id FROM members WHERE user_id=? ORDER BY created_at LIMIT 1", user_id)
    return r["workspace_id"] if r else None


# ── sessions ─────────────────────────────────────────────────────────────────────────
def create_session(user_id, workspace_id):
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.run("INSERT INTO sessions (token_hash, user_id, workspace_id, created_at, expires_at) VALUES (?,?,?,?,?)",
           _token_hash(token), user_id, workspace_id, now, now + SESSION_DAYS * 86400)
    return token


def session(token):
    """(user, workspace, role) for a live session token, else None."""
    if not token or len(token) > 100:
        return None
    s = db.one("SELECT * FROM sessions WHERE token_hash=?", _token_hash(token))
    if not s or s["expires_at"] < time.time():
        return None
    m = db.one("SELECT role FROM members WHERE workspace_id=? AND user_id=?", s["workspace_id"], s["user_id"])
    if not m:  # removed from the workspace since signing in
        return None
    user = db.one("SELECT id, email, name, created_at FROM users WHERE id=?", s["user_id"])
    ws = db.one("SELECT * FROM workspaces WHERE id=?", s["workspace_id"])
    return (user, ws, m["role"]) if user and ws else None


def end_session(token):
    if token:
        db.run("DELETE FROM sessions WHERE token_hash=?", _token_hash(token))


def cookie_header(token, max_age=SESSION_DAYS * 86400):
    parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
    if settings.get().secure_cookies:
        parts.append("Secure")
    return "; ".join(parts)


# ── API keys ─────────────────────────────────────────────────────────────────────────
def create_key(workspace_id, name):
    name = (name or "").strip()[:60] or "API key"
    raw = "mf_live_" + secrets.token_urlsafe(24)
    kid = db.new_id("key")
    db.run("INSERT INTO api_keys (id, workspace_id, name, prefix, key_hash, created_at) VALUES (?,?,?,?,?,?)",
           kid, workspace_id, name, raw[:12], _token_hash(raw), time.time())
    return {"id": kid, "name": name, "prefix": raw[:12], "key": raw}


def list_keys(workspace_id):
    return db.all_("SELECT id, name, prefix, created_at, last_used_at FROM api_keys "
                   "WHERE workspace_id=? AND revoked_at IS NULL ORDER BY created_at", workspace_id)


def revoke_key(workspace_id, key_id):
    cur = db.run("UPDATE api_keys SET revoked_at=? WHERE id=? AND workspace_id=? AND revoked_at IS NULL",
                 time.time(), key_id, workspace_id)
    return cur.rowcount > 0


def key_workspace(raw):
    """The workspace an API key belongs to (and marks it used), or None."""
    if not raw or not raw.startswith("mf_") or len(raw) > 100:
        return None
    k = db.one("SELECT id, workspace_id FROM api_keys WHERE key_hash=? AND revoked_at IS NULL", _token_hash(raw))
    if not k:
        return None
    db.run("UPDATE api_keys SET last_used_at=? WHERE id=?", time.time(), k["id"])
    return db.one("SELECT * FROM workspaces WHERE id=?", k["workspace_id"])
