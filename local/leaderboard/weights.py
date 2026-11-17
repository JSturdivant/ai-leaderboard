"""Per-alias fit weights saved from the UI, kept in their own file so they never get lost.

weights.json (repo root; override with $LEADERBOARD_WEIGHTS) is sparse: it holds only what differs
from config.json, in the same shape the browser edits:

  {"aliases": {"Coding": {"cats":    {"coding": 0.6, "agentic": 0.3},
                          "metrics": {"coding": {"swe_bench_verified": 5, "arena_webdev": 0}}}}}

`cats` sets dimension weights, `metrics` sets benchmark weights inside a dimension (0 = off).
The build overlays this on config.json, so data/latest.json and the CLI use the same weights the page does.
"""

import datetime as dt
import math
import os
from pathlib import Path

from . import ROOT
from .metrics import METRICS
from .util import read_json, write_json_atomic

MAX_WEIGHT = 100


def weights_path():
    return Path(os.environ.get("LEADERBOARD_WEIGHTS") or ROOT / "weights.json")


def _valid(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0 <= x <= MAX_WEIGHT


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
            if c in cfg["categories"] and _valid(w):
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
                if k in METRICS and _valid(w):
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


def load(cfg):
    """The saved overlay ({alias: {cats, metrics}}); empty when the file is missing or unreadable."""
    data = read_json(weights_path(), {})
    return parse(data.get("aliases") if isinstance(data, dict) else {}, cfg)


def saved_at():
    data = read_json(weights_path(), {})
    return data.get("saved_at") if isinstance(data, dict) else None


def save(cfg, raw_aliases):
    """Validate strictly, then write atomically. Returns what was saved."""
    aliases = parse(raw_aliases, cfg, strict=True)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    write_json_atomic(weights_path(), {"saved_at": stamp, "aliases": aliases}, indent=2)
    return {"saved_at": stamp, "aliases": aliases}


def merge_alias(alias_cfg, overlay):
    """The alias as the build should see it: config.json values with the saved overlay applied."""
    if not overlay:
        return alias_cfg
    out = dict(alias_cfg)
    out["weights"] = {**alias_cfg["weights"], **(overlay.get("cats") or {})}
    metric_weights = {c: dict(v) for c, v in (alias_cfg.get("metric_weights") or {}).items()}
    for c, ms in (overlay.get("metrics") or {}).items():
        metric_weights.setdefault(c, {}).update(ms)
    if metric_weights:
        out["metric_weights"] = metric_weights
    return out
