"""HTTP + file helpers (stdlib only)."""

import gzip
import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("leaderboard")

# Generic client name on purpose: nothing about the machine or its owner is sent.
USER_AGENT = "ai-leaderboard-local/1.0"


def fetch(url, *, headers=None, timeout=60, retries=3, backoff=1.5):
    """GET `url` and return the body bytes. Retries 429/5xx/network errors."""
    hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})}
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
            wait = float(e.headers.get("Retry-After") or backoff ** (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if attempt == retries:
                raise
            wait = backoff ** (attempt + 1)
        log.debug("retry %s in %.1fs (%s)", url, wait, last)
        time.sleep(min(wait, 30))
    raise last  # pragma: no cover


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw).decode("utf-8"))


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json_atomic(path, obj, *, indent=None):
    """Write JSON so a reader never sees a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=indent, separators=None if indent else (",", ":"), allow_nan=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def num(x):
    """Coerce to float; None for blank/invalid/NaN."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def per_million(x):
    """OpenRouter-style per-token price string -> $/1M tokens (None if missing)."""
    v = num(x)
    return None if v is None else round(v * 1e6, 6)
