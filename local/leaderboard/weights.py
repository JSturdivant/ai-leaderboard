"""weights.json: your fit weights and cost caps (saved from the UI) plus the app's latest recommendations.

One file (local/weights.json; override with $LEADERBOARD_WEIGHTS), kept outside data/ so clearing the cache
never loses it:

  {"saved_at": "...",
   "aliases": {"Coding": {"cats":    {"coding": 0.6},                      # dimension weights
                          "metrics": {"coding": {"swe_bench_verified": 5}}}},  # benchmark weights (0 = off)
   "caps":    {"Cheap": 0.2, "Thinking": null},                            # cost cap, blended $/M (null = no cap)
   "recommendations": {...}}                                                # written by the app, see recommend.py

`aliases` and `caps` are sparse (only what differs from config.json) and are the only parts to edit by hand.
The build overlays them on config.json; `recommendations` is rewritten after every build.
"""

import datetime as dt
import math
import os
import shutil
import threading
from pathlib import Path

from . import ROOT
from .metrics import METRICS
from .util import log, read_json, write_json_atomic

MAX_WEIGHT = 100
MAX_CAP = 1000
UNSET = object()  # "no cap override" (None means "override: no cap")
_lock = threading.Lock()  # a weights save and a rebuild must never interleave their read-modify-write


def weights_path():
    return Path(os.environ.get("LEADERBOARD_WEIGHTS") or ROOT / "weights.json")


# ── moving a file saved at the old (pre-local/) location ─────────────────────────────
def legacy_candidate():
    """weights.json at the repository root, where it lived before the app moved into local/."""
    if os.environ.get("LEADERBOARD_WEIGHTS") or ROOT.name != "local":
        return None
    p = ROOT.parent / "weights.json"
    return p if (ROOT.parent / ".git").exists() and p.exists() else None


def migrate_legacy(legacy, dest):
    """One-time: copy the old file into place and set the original aside so it cannot resurrect later."""
    if not legacy or not Path(legacy).exists() or Path(dest).exists():
        return False
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(legacy, dest)
    Path(legacy).rename(Path(legacy).with_name(Path(legacy).name + ".moved-to-local"))
    log.info("moved %s into %s", legacy, dest)
    return True


# ── validation ───────────────────────────────────────────────────────────────────────
def _num(x, hi):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0 <= x <= hi


def parse(raw, cfg, strict=False):
    """Validate a {alias: {cats, metrics}} mapping against the config.

    strict=True (API input) raises ValueError on the first problem. strict=False (a file the user may
    have edited by hand) quietly drops anything invalid, so a typo can never break the build.
    """
    def bad(msg):
        if strict:
            raise ValueError(msg)

    out = {}
    if not isinstance(raw, dict):
        bad("aliases must be an object")
        return out
    for alias, entry in raw.items():
        if alias not in cfg["aliases"]:
            bad(f"unknown alias {alias!r}")
            continue
        if not isinstance(entry, dict):
            bad(f"{alias}: expected an object")
            continue
        kept = {}
        cats = {}
        for c, w in (entry.get("cats") or {}).items():
            if c in cfg["categories"] and _num(w, MAX_WEIGHT):
                cats[c] = w
            else:
                bad(f"{alias}: bad dimension weight {c!r}={w!r}")
        metrics = {}
        for c, ms in (entry.get("metrics") or {}).items():
            if c not in cfg["categories"] or not isinstance(ms, dict):
                bad(f"{alias}: bad dimension {c!r}")
                continue
            good = {}
            for k, w in ms.items():
                if k in METRICS and _num(w, MAX_WEIGHT):
                    good[k] = w
                else:
                    bad(f"{alias}: bad benchmark weight {c}.{k}={w!r}")
            if good:
                metrics[c] = good
        if cats:
            kept["cats"] = cats
        if metrics:
            kept["metrics"] = metrics
        if kept:
            out[alias] = kept
    return out


def parse_caps(raw, cfg, strict=False):
    """Validate {alias: cap} where cap is a blended $/M number or null (no cap). Same strictness rules."""
    def bad(msg):
        if strict:
            raise ValueError(msg)

    out = {}
    if not isinstance(raw, dict):
        bad("caps must be an object")
        return out
    for alias, cap in raw.items():
        if alias not in cfg["aliases"]:
            bad(f"unknown alias {alias!r}")
        elif cap is None or _num(cap, MAX_CAP):
            out[alias] = cap
        else:
            bad(f"{alias}: bad cost cap {cap!r}")
    return out


# ── the file ─────────────────────────────────────────────────────────────────────────
def _raw():
    migrate_legacy(legacy_candidate(), weights_path())
    data = read_json(weights_path(), {})
    return data if isinstance(data, dict) else {}


def _write(raw):
    write_json_atomic(weights_path(), raw, indent=2)


def version():
    """A cheap fingerprint of the file (modification time + size), or None when it does not exist.

    It changes whenever anything rewrites the file: a save from the page, the recommendations after a rebuild,
    the CLI, or a hand edit. The page polls it to tell you about every change.
    """
    try:
        st = weights_path().stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns}-{st.st_size}"


def load(cfg):
    """The saved settings: {"aliases": {alias: {cats, metrics}}, "caps": {alias: cap|None}}."""
    with _lock:
        raw = _raw()
    return {"aliases": parse(raw.get("aliases"), cfg), "caps": parse_caps(raw.get("caps"), cfg)}


def snapshot(cfg):
    """Everything the API returns: settings, when they were saved, and the latest recommendations."""
    with _lock:
        raw = _raw()
        ver = version()
    return {"aliases": parse(raw.get("aliases"), cfg), "caps": parse_caps(raw.get("caps"), cfg),
            "saved_at": raw.get("saved_at"), "recommendations": raw.get("recommendations"),
            "file": weights_path().name, "version": ver}


def save(cfg, raw_aliases, raw_caps=None):
    """Validate strictly, then write atomically. `caps` left out (None) keeps the saved caps.

    The recommendations section is carried over untouched; the rebuild that follows refreshes it.
    """
    aliases = parse(raw_aliases, cfg, strict=True)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with _lock:
        cur = _raw()
        caps = parse_caps(raw_caps, cfg, strict=True) if raw_caps is not None else parse_caps(cur.get("caps"), cfg)
        out = {**cur, "saved_at": stamp, "aliases": aliases, "caps": caps}  # unknown keys survive; order kept
        _write(out)
        ver = version()
    return {"saved_at": stamp, "aliases": aliases, "caps": caps, "version": ver}


def _comparable(recs):
    return {k: v for k, v in (recs or {}).items() if k != "generated_at"}


def write_recommendations(recs):
    """Store the latest recommendations in the file, leaving the settings as they are.

    Written only when something other than the timestamp changed, so a quiet rebuild leaves the file (and
    its modification time) alone. Returns whether the file was rewritten.
    """
    with _lock:
        cur = _raw()
        if _comparable(cur.get("recommendations")) == _comparable(recs):
            return False
        out = {"saved_at": None, "aliases": {}, "caps": {}, **cur, "recommendations": recs}
        _write(out)
    return True


def load_recommendations():
    """The last-written recommendations dict (or None), read under the lock."""
    with _lock:
        return _raw().get("recommendations")


def saved_at():
    with _lock:
        return _raw().get("saved_at")


# ── applying settings to the config ──────────────────────────────────────────────────
def merge_alias(alias_cfg, overlay, cap=UNSET):
    """The alias as the build should see it: config.json values with the saved weights and cap applied."""
    if not overlay and cap is UNSET:
        return alias_cfg
    out = dict(alias_cfg)
    if overlay:
        out["weights"] = {**alias_cfg["weights"], **(overlay.get("cats") or {})}
        metric_weights = {c: dict(v) for c, v in (alias_cfg.get("metric_weights") or {}).items()}
        for c, ms in (overlay.get("metrics") or {}).items():
            metric_weights.setdefault(c, {}).update(ms)
        if metric_weights:
            out["metric_weights"] = metric_weights
    if cap is not UNSET:
        out["max_cost"] = cap
    return out
