"""Alias fit and picks, computed from a finished market snapshot.

The market snapshot (data/latest.json) is global: the same prices and benchmark scores for every customer.
What differs per workspace is only the alias definitions (weights, caps, requirements). Scoring an alias is
cheap (no network, no name matching), so it runs on demand per workspace from the snapshot — the build uses
the same function for the preset aliases, which keeps the two paths identical.
"""

from . import scoring as sc


def scaled_metrics(snapshot):
    """Benchmarks that have a usable 0-100 scale in this snapshot."""
    return {k for k, m in snapshot["metrics"].items() if m.get("scale")}


def alias_cat_scores(row, alias_def, cats, scaled, sc_cfg):
    """Category scores as one alias sees them: the shared ones, unless the alias re-weights a category's
    benchmarks via `metric_weights` ({category: {metric: weight}})."""
    over = alias_def.get("metric_weights") or {}
    norm = {m: d.get("s") for m, d in row["metrics"].items()}
    out = {}
    for c, c_cfg in cats.items():
        if c in over:
            w = {m: wt for m, wt in {**c_cfg["metrics"], **over[c]}.items() if m in scaled}
            res = sc.composite(norm, w, sc_cfg["min_coverage"], sc_cfg["confident_coverage"]) if w else None
        else:
            res = row["scores"].get(c)
        out[c] = res["s"] if res else None
    return out


def score_alias(rows, alias_def, cats, scaled, sc_cfg, current_id=None, keep_ranked=15):
    """rows: model rows (snapshot['models'] or the build's rows). Returns (fits, picks).

    fits:  {model id: (fit | None, share)}
    picks: the alias_picks() dict plus `unscored`, `current` and the limits the picks were made under.
    """
    fits = {}
    for r in rows:
        fits[r["id"]] = sc.fit_score(alias_cat_scores(r, alias_def, cats, scaled, sc_cfg),
                                     alias_def["weights"], alias_def.get("min_share", 0.6))
    allc = [{"id": r["id"], "base": r["base"], "cost": r["cost"]["lowest"]["blended"], "fit": fits[r["id"]][0]}
            for r in rows if fits[r["id"]][0] is not None]
    by = {r["id"]: r for r in rows}
    ok = [c for c in allc if sc.eligible(by[c["id"]], alias_def, c["cost"])]
    picks = sc.alias_picks(ok)
    picks["unscored"] = sum(1 for r in rows if fits[r["id"]][0] is None and sc.eligible(r, alias_def, r["cost"]["lowest"]["blended"]))
    picks["current"] = sc.validate_current(current_id, ok, allc, picks)
    picks["ranked"] = picks["ranked"][:keep_ranked]
    picks["fit_of"] = {mid: fits[mid][0] for mid in sorted({*picks["ranked"], *picks["shortlist"]})}
    picks.update(cap=alias_def.get("max_cost"), require=list(alias_def.get("require") or []),
                 min_context=alias_def.get("min_context"))
    return fits, picks


def score_workspace(snapshot, alias_defs, current_models=None):
    """{alias: picks} for a workspace's effective alias definitions, from a snapshot."""
    cfg = snapshot["config"]
    scaled, cats, sc_cfg = scaled_metrics(snapshot), cfg["categories"], cfg["scoring"]
    out = {}
    for name, a in alias_defs.items():
        _, picks = score_alias(snapshot["models"], a, cats, scaled, sc_cfg, (current_models or {}).get(name))
        out[name] = picks
    return out
