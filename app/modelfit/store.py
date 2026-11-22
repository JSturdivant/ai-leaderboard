"""Raw-source cache, refresh status, and the refresh orchestration shared by CLI and server."""

import threading
import time

from . import CONFIG_PATH, DATA_DIR, RAW_DIR
from .sources import SOURCES
from .util import log, read_json, write_json_atomic

STATUS_PATH = DATA_DIR / "status.json"
_lock = threading.Lock()  # one refresh at a time (CLI --loop, server thread, manual button)


def load_config():
    cfg = read_json(CONFIG_PATH)
    if cfg is None:
        raise SystemExit(f"Missing or invalid {CONFIG_PATH}")
    return cfg


def raw_path(name):
    return RAW_DIR / f"{name}.json"


def load_raw(name):
    return read_json(raw_path(name))


def load_status():
    return read_json(STATUS_PATH, {})


def interval_seconds(cfg, name):
    return float(cfg["refresh"]["intervals_minutes"].get(name, 60)) * 60


def stale_sources(cfg, status=None, now=None):
    status = status if status is not None else load_status()
    now = now or time.time()
    out = []
    for name in SOURCES:
        last = (status.get(name) or {}).get("fetched_at")
        if last is None or now - last >= interval_seconds(cfg, name):
            # after a failure, retry at most every 5 minutes instead of hammering the source
            tried = (status.get(name) or {}).get("attempted_at") or 0
            if now - tried >= 300:
                out.append(name)
    return out


def forceable_sources(cfg, status=None, now=None):
    """Sources a manual 'refresh now' may fetch: skips ones fetched more recently than refresh.min_force_minutes."""
    status = status if status is not None else load_status()
    now = now or time.time()
    mins = cfg["refresh"].get("min_force_minutes", {})
    out = []
    for name in SOURCES:
        last = (status.get(name) or {}).get("fetched_at")
        if last is None or now - last >= float(mins.get(name, 2)) * 60:
            out.append(name)
    return out


def refresh_sources(cfg, names=None, progress=None):
    """Fetch the given sources (default: all). A failure keeps the previous raw copy."""
    names = list(SOURCES if names is None else names)
    results = {}
    with _lock:
        status = load_status()
        for name in names:
            mod = SOURCES[name]
            started = time.time()
            entry = status.setdefault(name, {})
            entry["attempted_at"] = started
            if progress:
                progress(f"fetching {name}")
            try:
                raw = mod.fetch(cfg, load_raw(name))
                write_json_atomic(raw_path(name), raw)
                entry.update(ok=True, fetched_at=raw.get("fetched_at", started), error=None, records=mod.count(raw))
                results[name] = "ok"
            except Exception as exc:  # noqa: BLE001 - any source failure must be survivable
                log.error("source %s failed: %s", name, exc)
                entry.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
                results[name] = "error"
            entry["seconds"] = round(time.time() - started, 1)
            write_json_atomic(STATUS_PATH, status, indent=1)
    return results
