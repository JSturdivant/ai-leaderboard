"""Validation of alias tuning (fit weights and cost caps) and of alias definitions.

A workspace stores its tuning as sparse overlays on its alias definitions:

  weights: {"Coding": {"cats":    {"coding": 0.6},                       # dimension weights
                       "metrics": {"coding": {"swe_bench_verified": 5}}}} # benchmark weights (0 = off)
  caps:    {"Cheap": 0.2, "Thinking": null}                              # blended $/M (null = no cap)

`ctx` below is anything with "aliases" (the workspace's alias definitions) and "categories".
strict=True (API input) raises ValueError on the first problem; strict=False (stored or hand-edited data)
quietly drops anything invalid, so a bad entry can never break a build.

Also reads a legacy weights.json (the single-user file earlier versions wrote) for a one-time import.
"""

import math
import os
import re
from pathlib import Path

from . import ROOT
from .metrics import METRICS
from .util import read_json

MAX_WEIGHT = 100
MAX_CAP = 1000
MAX_ALIASES = 100
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}$")
REQUIREMENTS = ("free", "tools", "reasoning")
UNSET = object()  # "no cap override" (None means "override: no cap")


def legacy_weights_paths():
    """Where earlier single-user versions kept weights.json (app/, the old local/, the repo root)."""
    env = os.environ.get("LEADERBOARD_WEIGHTS")
    if env:
        return [Path(env)]
    repo = ROOT.parent
    return [ROOT / "weights.json", repo / "local" / "weights.json", repo / "weights.json"]


def read_legacy():
    """The first legacy weights.json found, as {"aliases", "caps"} (unvalidated), or None."""
    for p in legacy_weights_paths():
        data = read_json(p)
        if isinstance(data, dict) and (data.get("aliases") or data.get("caps")):
            return {"aliases": data.get("aliases") or {}, "caps": data.get("caps") or {}, "path": str(p)}
    return None


# ── validation ───────────────────────────────────────────────────────────────────────
def _num(x, hi):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0 <= x <= hi


def parse(raw, cfg, strict=False):  # cfg: {"aliases": ..., "categories": ...}
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


# ── alias definitions ────────────────────────────────────────────────────────────────
def parse_alias_def(name, raw, categories, strict=True):
    """Validate one alias definition (as created from the UI or API). Returns the clean dict."""
    def bad(msg):
        raise ValueError(f"{name}: {msg}")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError("alias names are 1-32 letters, digits, spaces, dots, dashes or underscores")
    if not isinstance(raw, dict):
        bad("expected an object")
    weights = {c: w for c, w in (raw.get("weights") or {}).items() if c in categories and _num(w, MAX_WEIGHT)}
    if not any(w > 0 for w in weights.values()):
        bad("give at least one dimension a weight above 0")
    out = {"note": str(raw.get("note") or "")[:200], "weights": weights}
    cap = raw.get("max_cost")
    if cap is not None and not _num(cap, MAX_CAP):
        bad(f"bad cost cap {cap!r}")
    out["max_cost"] = cap
    req = [r for r in (raw.get("require") or []) if r in REQUIREMENTS]
    if req:
        out["require"] = req
    if "free" in req:
        out["max_cost"] = 0
    ctx = raw.get("min_context")
    if ctx is not None:
        if not _num(ctx, 10_000_000):
            bad(f"bad min_context {ctx!r}")
        out["min_context"] = int(ctx)
    share = raw.get("min_share")
    if share is not None:
        if not _num(share, 1):
            bad(f"bad min_share {share!r}")
        out["min_share"] = share
    mw = raw.get("metric_weights")
    if mw:
        out["metric_weights"] = {c: {k: w for k, w in (ms or {}).items() if k in METRICS and _num(w, MAX_WEIGHT)}
                                 for c, ms in mw.items() if c in categories}
    if raw.get("preset"):
        out["preset"] = str(raw["preset"])[:32]
    return out


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
