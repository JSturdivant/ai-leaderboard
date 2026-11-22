"""A workspace's alias recommendations, as served by GET /v1/recommendations and sent to webhooks.

Derived from a market snapshot plus the workspace's alias definitions, so they always reflect the latest
benchmark and price data, the workspace's weights and its cost caps, at the snapshot's cost basis.
"""

import datetime as dt

NOTE = ("picks[0] is the recommendation for each alias; 'model' is the OpenRouter-style id, and "
        "cost.channel/provider say where it is cheapest right now.")


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def build(snapshot, alias_picks, custom=()):
    """snapshot: data/latest.json; alias_picks: {alias: picks} from picks.score_alias/score_workspace;
    custom: aliases whose weights differ from their definition's defaults."""
    by = {m["id"]: m for m in snapshot["models"]}
    aliases = {}
    for alias, p in alias_picks.items():
        out = []
        for rank, mid in enumerate(p["shortlist"], 1):
            m, low = by[mid], by[mid]["cost"]["lowest"]
            out.append({
                "rank": rank,
                "model": mid,
                "name": m["name"],
                "fit": p["fit_of"][mid],
                "released": m["released"],
                "cost": {"blended": low["blended"], "input": low["in"], "output": low["out"],
                         "channel": low["channel"], "provider": low["provider"]},
            })
        aliases[alias] = {
            "cap": p["cap"],
            "require": p["require"],
            "min_context": p["min_context"],
            "custom_weights": alias in custom,
            "candidates": p["n"],
            "unscored": p.get("unscored", 0),
            "picks": out,
        }
        if p.get("current"):
            aliases[alias]["current"] = p["current"]
    return {
        "generated_at": snapshot["generated_at"],
        "note": NOTE,
        "cost_basis": snapshot["config"]["blend"],
        "data_as_of": {name: _iso(s["fetched_at"]) for name, s in snapshot["sources"].items() if s.get("fetched_at")},
        "aliases": aliases,
    }


def top_picks(recs):
    """{alias: #1 model id} (aliases with no pick are left out)."""
    return {a: e["picks"][0]["model"] for a, e in ((recs or {}).get("aliases") or {}).items() if e.get("picks")}


def changes(prev, new):
    """{alias: (from, to)} for every alias whose #1 pick changed, appeared or vanished."""
    before, after = top_picks(prev), top_picks(new)
    out = {a: (before.get(a), m) for a, m in after.items() if before.get(a) != m}
    out.update({a: (m, None) for a, m in before.items() if a not in after})
    return out
