"""SQLite storage for the SaaS layer (stdlib sqlite3, WAL mode, one connection per thread).

SQLite comfortably serves a single app instance with thousands of workspaces. The schema is plain SQL with
TEXT ids and JSON columns so it ports to Postgres unchanged when the app runs as several instances
(see docs/ARCHITECTURE.md). Migrations are append-only: add a new entry to MIGRATIONS, never edit one.
"""

import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import settings

MIGRATIONS = [
    # 1: accounts, workspaces, sessions, keys, settings, recommendations, billing, usage, audit
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL DEFAULT '',
        pw_hash TEXT NOT NULL, created_at REAL NOT NULL, last_login_at REAL
    );
    CREATE TABLE workspaces (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, plan TEXT NOT NULL DEFAULT 'free',
        plan_status TEXT NOT NULL DEFAULT 'active', plan_interval TEXT, current_period_end REAL,
        seats INTEGER NOT NULL DEFAULT 1, stripe_customer_id TEXT, stripe_subscription_id TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE members (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role TEXT NOT NULL DEFAULT 'member', created_at REAL NOT NULL,
        PRIMARY KEY (workspace_id, user_id)
    );
    CREATE INDEX members_user ON members(user_id);
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        created_at REAL NOT NULL, expires_at REAL NOT NULL
    );
    CREATE TABLE api_keys (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        name TEXT NOT NULL, prefix TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
        created_at REAL NOT NULL, last_used_at REAL, revoked_at REAL
    );
    CREATE INDEX api_keys_ws ON api_keys(workspace_id);
    CREATE TABLE ws_settings (
        workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
        data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL
    );
    CREATE TABLE ws_recommendations (
        workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
        data TEXT NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE pick_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        alias TEXT NOT NULL, from_model TEXT, to_model TEXT, at REAL NOT NULL
    );
    CREATE INDEX pick_history_ws ON pick_history(workspace_id, at);
    CREATE TABLE usage (
        workspace_id TEXT NOT NULL, day TEXT NOT NULL, metric TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (workspace_id, day, metric)
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT, user_id TEXT, type TEXT NOT NULL,
        data TEXT, at REAL NOT NULL
    );
    CREATE INDEX events_ws ON events(workspace_id, at);
    CREATE TABLE billing_events (id TEXT PRIMARY KEY, type TEXT NOT NULL, received_at REAL NOT NULL);
    """,
]

_local = threading.local()
_init_lock = threading.Lock()
_initialised = set()


def new_id(prefix):
    return f"{prefix}_{secrets.token_hex(8)}"


def _connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def migrate(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    have = row["version"] if row else 0
    for i, sql in enumerate(MIGRATIONS[have:], start=have + 1):
        conn.executescript("BEGIN;" + sql + ";COMMIT;")
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version VALUES (?)", (i,))


def conn():
    path = settings.get().db_path
    c = getattr(_local, "conns", {}).get(str(path))
    if c is None:
        c = _connect(path)
        with _init_lock:
            if str(path) not in _initialised:
                migrate(c)
                _initialised.add(str(path))
        _local.__dict__.setdefault("conns", {})[str(path)] = c
    return c


def release():
    """Close this thread's connections (the server calls it after every request: one thread per request)."""
    for c in getattr(_local, "conns", {}).values():
        try:
            c.close()
        except sqlite3.Error:
            pass
    _local.__dict__["conns"] = {}


@contextmanager
def tx():
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def one(sql, *args):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def all_(sql, *args):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def run(sql, *args):
    return conn().execute(sql, args)


def event(type_, workspace_id=None, user_id=None, **data):
    """Audit trail and product analytics in one table (signups, upgrades, key changes, ...)."""
    run("INSERT INTO events (workspace_id, user_id, type, data, at) VALUES (?,?,?,?,?)",
        workspace_id, user_id, type_, json.dumps(data) if data else None, time.time())


def bump_usage(workspace_id, metric, n=1, day=None):
    day = day or time.strftime("%Y-%m-%d", time.gmtime())
    run("INSERT INTO usage (workspace_id, day, metric, count) VALUES (?,?,?,?) "
        "ON CONFLICT(workspace_id, day, metric) DO UPDATE SET count = count + excluded.count",
        workspace_id, day, metric, n)
    r = one("SELECT count FROM usage WHERE workspace_id=? AND day=? AND metric=?", workspace_id, day, metric)
    return r["count"] if r else n


def usage_today(workspace_id, metric):
    r = one("SELECT count FROM usage WHERE workspace_id=? AND day=? AND metric=?",
            workspace_id, time.strftime("%Y-%m-%d", time.gmtime()), metric)
    return r["count"] if r else 0
