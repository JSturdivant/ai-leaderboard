"""The app's current alias recommendations, as stored in weights.json.

Derived from a finished build (data/latest.json's contents), so they always reflect the latest benchmark and
price data, the saved fit weights, and the saved cost caps, at the cost basis in config.json (`cost.blend`).
"""

import datetime as dt

NOTE = ("Written by the app after every rebuild from the latest benchmark and cost data, your saved weights "
        "and caps. Edit 'aliases' and 'caps' in this file, never this section. picks[0] is the recommendation; "
        "'model' is the OpenRouter-style id, and cost.channel/provider say where it is cheapest.")


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def build(data):
    """data: the dict returned by build.build(). Returns the JSON-able recommendations section."""
    by = {m["id"]: m for m in data["models"]}
    aliases = {}
    for alias, p in data["alias_picks"].items():
        picks = []
        for rank, mid in enumerate(p["shortlist"], 1):
            m, low = by[mid], by[mid]["cost"]["lowest"]
            picks.append({
                "rank": rank,
                "model": mid,
                "name": m["name"],
                "fit": m["fit"][alias],
                "released": m["released"],
                "cost": {"blended": low["blended"], "input": low["in"], "output": low["out"],
                         "channel": low["channel"], "provider": low["provider"]},
            })
        aliases[alias] = {
            "cap": p["cap"],
            "require": p["require"],
            "min_context": p["min_context"],
            "custom_weights": p["custom_weights"],
            "candidates": p["n"],
            "unscored": p.get("unscored", 0),
            "picks": picks,
        }
    return {
        "generated_at": data["generated_at"],
        "note": NOTE,
        "cost_basis": data["config"]["blend"],
        "data_as_of": {name: _iso(s["fetched_at"]) for name, s in data["sources"].items() if s.get("fetched_at")},
        "aliases": aliases,
    }
