"""Pure scoring maths: normalisation, category composites, value, Pareto, alias picks.

Method (see README):
  * each metric is rescaled to 0-100 between its low and high quantile over currently
    purchasable models, so Elo, % and index scales can be averaged;
  * a category score is the weighted mean of the metrics a model has, provided those
    metrics carry at least `min_coverage` of the category's total weight;
  * $/Intelligence = blended $/M tokens / score * 100  (cost of 100 points at this efficiency).
"""

import math


def quantile(sorted_vals, q):
    """Linear-interpolated quantile of an already-sorted list."""
    if not sorted_vals:
        return None
    pos = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def scale_for(values, low_q, high_q, min_n):
    """(lo, hi) anchors for a metric, or None when too few models carry it."""
    vals = sorted(v for v in values if v is not None)
    if len(vals) < min_n:
        return None
    lo, hi = quantile(vals, low_q), quantile(vals, high_q)
    return (lo, hi) if hi > lo else None


def normalize(v, scale):
    lo, hi = scale
    return max(0.0, min(100.0, 100.0 * (v - lo) / (hi - lo)))


def blended(price_in, price_out, blend):
    wi, wo = blend["input"], blend["output"]
    return (price_in * wi + price_out * wo) / (wi + wo)


def composite(norm_scores, weights, min_coverage, confident_coverage):
    """norm_scores: {metric: 0-100}; weights: {metric: w}. -> dict or None. Weights <= 0 are ignored."""
    weights = {k: w for k, w in weights.items() if w > 0}
    total = math.fsum(weights.values())
    have = {k: w for k, w in weights.items() if norm_scores.get(k) is not None}
    if not have or total <= 0:
        return None
    cov = math.fsum(have.values()) / total
    if cov < min_coverage:
        return None
    s = math.fsum(norm_scores[k] * w for k, w in have.items()) / math.fsum(have.values())
    return {"s": round(s, 1), "cov": round(cov, 2), "n": len(have), "confident": cov >= confident_coverage}


def usd_per_100(cost, score, floor):
    """Cost of 100 intelligence points. 0 for free models; None without a score."""
    if cost is None or score is None:
        return None
    if cost == 0:
        return 0.0
    return cost / max(score, floor) * 100.0


def pareto(points):
    """points: [(id, cost, score)] -> ids not dominated (nothing cheaper-or-equal AND better)."""
    best = -math.inf
    out = []
    for pid, cost, score in sorted(points, key=lambda p: (p[1], -p[2])):
        if score > best:
            out.append(pid)
            best = score
    return out


def fit_score(cat_scores, weights, min_share=0.6):
    """Weighted alias fit from category scores ({cat: score|None}).

    Returns (fit, share) where share is the fraction of the alias's weight that had data,
    or (None, share) when that is below `min_share`.
    """
    weights = {c: w for c, w in weights.items() if w > 0}
    total = math.fsum(weights.values())
    have = {c: w for c, w in weights.items() if cat_scores.get(c) is not None}
    share = math.fsum(have.values()) / total if total else 0.0
    if not have or share < min_share:
        return None, round(share, 2)
    return round(math.fsum(cat_scores[c] * w for c, w in have.items()) / math.fsum(have.values()), 1), round(share, 2)


def eligible(row, alias, cost):
    """Does a model row satisfy an alias's hard constraints at blended `cost`?"""
    if cost is None:
        return False
    cap = alias.get("max_cost")
    if cap is not None and cost > cap + 1e-12:
        return False
    req = alias.get("require") or []
    if "free" in req and not row["free"]:
        return False
    if "tools" in req and not row["tools"]:
        return False
    if "reasoning" in req and not row["reasoning"]:
        return False
    if alias.get("min_context") and (row.get("ctx") or 0) < alias["min_context"]:
        return False
    return True


def alias_picks(cands, tolerance):
    """cands: [{id, cost, fit}] already filtered by eligibility.

    Returns top-fit pick, best-value pick (cheapest within `tolerance` of the top fit),
    the cost/fit efficient frontier, and the full ranking.
    """
    cands = [c for c in cands if c["fit"] is not None]
    if not cands:
        return {"top": None, "best_value": None, "frontier": [], "ranked": [], "n": 0}
    ranked = sorted(cands, key=lambda c: (-c["fit"], c["cost"]))
    top = ranked[0]
    near = [c for c in cands if c["fit"] >= top["fit"] * (1 - tolerance)]
    value = sorted(near, key=lambda c: (c["cost"], -c["fit"]))[0]
    frontier = pareto([(c["id"], c["cost"], c["fit"]) for c in cands])
    return {
        "top": top["id"],
        "best_value": value["id"],
        "frontier": frontier,
        "ranked": [c["id"] for c in ranked],
        "n": len(cands),
    }


def validate_current(current_id, cands, alias_cands_all, picks):
    """How does the model the user runs today compare, within one alias?

    cands: eligible candidates; alias_cands_all: every row with a fit (any cost/constraint)
    """
    if not current_id:
        return None
    by_id = {c["id"]: c for c in alias_cands_all}
    cur = by_id.get(current_id)
    if cur is None:
        return {"id": current_id, "status": "no_data"}
    ids = {c["id"] for c in cands}
    out = {"id": current_id, "fit": cur["fit"], "cost": cur["cost"]}
    if current_id not in ids:
        out["status"] = "ineligible"
    else:
        out["status"] = "ok"
        out["rank"] = picks["ranked"].index(current_id) + 1
        out["of"] = picks["n"]
    # strictly better options: no more expensive and a higher fit, best few first
    better = [c for c in cands if c["id"] != current_id and c["cost"] <= cur["cost"] + 1e-12 and c["fit"] > cur["fit"]]
    better.sort(key=lambda c: (-c["fit"], c["cost"]))
    out["better"] = [c["id"] for c in better[:3]]
    return out
