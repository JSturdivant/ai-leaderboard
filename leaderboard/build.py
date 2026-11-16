"""Join the raw source caches into data/latest.json (the companion data file)."""

import datetime as dt
import re
import statistics
import time

from . import LATEST
from . import scoring as sc
from .metrics import METRICS, SOURCE_LABELS
from .names import NameIndex, slug, strip_date
from .sources import SOURCES
from .store import load_raw, load_status
from .util import log, write_json_atomic

CHANNELS = ("primary", "openrouter", "nous")  # tie-break order for "lowest"
SCHEMA = 1


# ── helpers ───────────────────────────────────────────────────────────────────────────
def _variant(mid):
    return mid.split(":", 1)[1] if ":" in mid else None


def _price_ok(i, o):
    return i is not None and o is not None and i >= 0 and o >= 0


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _first_party(vendor, provider, overrides):
    if provider in overrides.get(vendor, []):
        return True
    v, p = _norm(vendor), _norm(provider)
    short = min(len(v), len(p))
    return short >= 3 and (v.startswith(p) or p.startswith(v))


def _minimal(cands):
    """Keep only endpoints not beaten on both input and output price."""
    out = []
    for c in sorted(cands, key=lambda c: (c["in"], c["out"])):
        if not out or c["out"] < out[-1]["out"] - 1e-12:
            out.append(c)
    return out


def _endpoint_cands(eps, cost_cfg, vendor, first_party_only=False):
    tiers = {None} | set(cost_cfg.get("include_service_tiers") or [])
    overrides = cost_cfg.get("primary_openrouter_providers") or {}
    bad_quant = set(cost_cfg.get("exclude_quantizations") or [])
    out = []
    for e in eps:
        if e.get("tier") not in tiers or e.get("status") not in (0, None):
            continue
        if e.get("uptime") is not None and e["uptime"] < cost_cfg.get("min_uptime_pct", 0):
            continue
        if not _price_ok(e.get("in"), e.get("out")):
            continue
        first = _first_party(vendor, e.get("provider") or "", overrides)
        if first_party_only and not first:
            continue
        # aggressive quantisation is excluded for third parties; the lab's own serving is the reference
        if e.get("quant") in bad_quant and not first:
            continue
        note = e.get("quant") if e.get("quant") not in (None, "unknown") else None
        out.append({"in": e["in"], "out": e["out"], "provider": e.get("provider") or "?", "note": note})
    return _minimal(out)


def _date_of(ts):
    if not ts:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()


def _text_model(m, exclude):
    mid = m["id"]
    if mid.startswith("~") or "embed" in mid or _variant(mid) in exclude:
        return False
    return m.get("output_modalities") in ([], ["text"])


def _aggregate(metric, recs):
    """Collapse several records (effort levels, harnesses) for one model into one value."""
    if metric == "terminal_bench":
        best = {}
        for r in recs:
            a = r.get("agent") or "?"
            if a not in best or r["value"] > best[a]["value"]:
                best[a] = r
        vals = [r["value"] for r in best.values()]
        top = max(best.values(), key=lambda r: r["value"])
        return {"v": statistics.median(vals), "label": f"median of {len(vals)} harness(es); best {top['label']}", "n": len(recs)}
    top = max(recs, key=lambda r: r["value"])
    return {"v": top["value"], "label": top["label"], "n": len(recs)}


# ── build ─────────────────────────────────────────────────────────────────────────────
def build(cfg, now=None):
    now = now or time.time()
    today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
    cost_cfg, sc_cfg = cfg["cost"], cfg["scoring"]
    blend = cost_cfg["blend"]
    exclude = set(cost_cfg.get("exclude_variants") or [])

    raws = {name: load_raw(name) for name in SOURCES}
    or_raw, nous_raw, ll_raw = raws["openrouter"], raws["nous"], raws["litellm"]
    or_models = {m["id"]: m for m in (or_raw or {}).get("models", [])}
    or_eps = (or_raw or {}).get("endpoints", {})
    nous_models = {m["id"]: m for m in (nous_raw or {}).get("models", [])}

    # LiteLLM first-party prices, indexed by slug
    ll_index = {}
    vendors_of = cost_cfg.get("primary_litellm_providers") or {}
    for e in (ll_raw or {}).get("entries", []):
        if e.get("deprecated") and e["deprecated"] < today.isoformat():
            continue
        ll_index.setdefault(slug(e["key"]), []).append(e)

    # ── 1. candidate rows: every purchasable text model on OpenRouter or Nous ─────────
    rows = {}
    for src in (or_models, nous_models):
        for mid, m in src.items():
            if mid in rows or not _text_model(m, exclude):
                continue
            exp = (or_models.get(mid) or {}).get("expiration")
            if exp and exp < today.isoformat():
                continue
            om, nm = or_models.get(mid), nous_models.get(mid)
            first = om or nm
            vendor = mid.split("/")[0]
            rows[mid] = {
                "id": mid,
                "base": mid.split(":")[0],
                "name": first["name"],
                "vendor": vendor,
                "released": _date_of(first.get("created")),
                "ctx": first.get("context_length"),
                "tools": bool((om or {}).get("tools") or (nm or {}).get("tools")),
                "reasoning": bool((om or {}).get("reasoning") or (nm or {}).get("reasoning")),
                "canonical": (om or {}).get("canonical_slug"),
            }

    # ── 2. prices from the three channels, then the lowest ───────────────────────────
    for mid, row in list(rows.items()):
        om, nm = or_models.get(mid), nous_models.get(mid)
        chan = {c: [] for c in CHANNELS}
        if om:
            eps = or_eps.get(mid)
            if eps is None:  # endpoint list not fetched: fall back to the catalogue price
                if _price_ok(om.get("in"), om.get("out")):
                    chan["openrouter"] = [{"in": om["in"], "out": om["out"], "provider": "catalogue price", "note": None}]
            else:
                chan["openrouter"] = _endpoint_cands(eps, cost_cfg, row["vendor"])
                chan["primary"] += _endpoint_cands(eps, cost_cfg, row["vendor"], first_party_only=True)
        if nm and _price_ok(nm.get("in"), nm.get("out")):
            note = None
            if nm.get("orig_in") is not None and nm["orig_in"] > nm["in"] + 1e-12:
                note = f"promo (was ${nm['orig_in']:g}/${nm['orig_out']:g})"
            chan["nous"] = [{"in": nm["in"], "out": nm["out"], "provider": "Nous Portal", "note": note}]
        if _variant(mid) != "free":
            cands = [row["base"], row["canonical"] or ""]
            cands_s = [slug(c) for c in cands if c]
            hits = next((ll_index[c] for c in cands_s if c in ll_index), None) or next(
                (ll_index[strip_date(c)] for c in cands_s if strip_date(c) in ll_index), None
            )
            if hits:  # only the model's own lab counts as a primary source (not e.g. a host re-listing it)
                chan["primary"] += _minimal(
                    [{"in": h["in"], "out": h["out"], "provider": f"{h['provider']} (list price)", "note": None}
                     for h in hits
                     if _price_ok(h["in"], h["out"]) and row["vendor"] in vendors_of.get(h["provider"], [])]
                )
        chan["primary"] = _minimal(chan["primary"]) if chan["primary"] else []
        flat = [(sc.blended(c["in"], c["out"], blend), CHANNELS.index(ch), ch, c) for ch in CHANNELS for c in chan[ch]]
        if not flat:
            del rows[mid]  # nothing to buy it from right now
            continue
        b, _, ch, c = min(flat, key=lambda t: (t[0], t[1]))
        row["cost"] = {
            "lowest": {"channel": ch, "provider": c["provider"], "in": c["in"], "out": c["out"], "blended": round(b, 6), "note": c["note"]},
            "channels": {k: v[:4] for k, v in chan.items() if v},
        }
        row["free"] = b == 0

    # ── 3. attach benchmark metrics ───────────────────────────────────────────────────
    values = {mid: {} for mid in rows}  # mid -> metric -> {v,label,how,n}
    # 3a. metrics embedded in OpenRouter's catalogue (matched by exact id)
    for mid, row in rows.items():
        # a ':free' variant shares its base model's benchmarks; free-only models carry their own
        b = (or_models.get(row["base"]) or or_models.get(mid) or {}).get("benchmarks") or {}
        aa = b.get("artificial_analysis") or {}
        for key, src in (("aa_intelligence", "intelligence_index"), ("aa_coding", "coding_index"), ("aa_agentic", "agentic_index")):
            if aa.get(src) is not None:
                values[mid][key] = {"v": float(aa[src]), "label": "Artificial Analysis via OpenRouter", "how": "exact", "n": 1}
        for e in b.get("design_arena") or []:
            if e.get("arena") == "models" and e.get("category") == "codecategories" and e.get("elo") is not None:
                values[mid]["design_arena_code"] = {"v": float(e["elo"]), "label": "Design Arena codecategories", "how": "exact", "n": 1}
    # 3b. name-matched metrics (Arena, Epoch)
    indexes = {}
    for src_name in ("arena", "epoch"):
        for rec in (raws[src_name] or {}).get("records", []):
            idx = indexes.setdefault(rec["metric"], NameIndex(cfg.get("name_overrides")))
            idx.add(rec["name"], rec)
    matched_labels = set()
    for mid, row in rows.items():
        cands = [slug(row["base"])] + ([slug(row["canonical"])] if row["canonical"] else [])
        for metric, idx in indexes.items():
            recs, how = idx.lookup(cands)
            if recs:
                agg = _aggregate(metric, recs)
                agg["how"] = how
                values[mid][metric] = agg
                matched_labels.update((metric, r["label"]) for r in recs)

    # ── 4. normalise every metric against the purchasable population ─────────────────
    used = {m for c in cfg["categories"].values() for m in c["metrics"]}
    scales = {}
    for metric in used:
        pop = {}
        for mid, row in rows.items():
            if metric in values[mid]:
                pop[row["base"]] = values[mid][metric]["v"]
        sc_ = sc.scale_for(pop.values(), sc_cfg["low_quantile"], sc_cfg["high_quantile"], sc_cfg["min_metric_population"])
        if sc_:
            scales[metric] = sc_

    # ── 5. category scores, $/intelligence, efficiency frontier ──────────────────────
    cats = cfg["categories"]
    for mid, row in rows.items():
        for metric, d in values[mid].items():
            if metric in scales:
                d["s"] = round(sc.normalize(d["v"], scales[metric]), 1)
        row["metrics"] = values[mid]
        norm = {m: d.get("s") for m, d in values[mid].items()}
        row["scores"], row["usd_per_100"] = {}, {}
        for cat, c in cats.items():
            w = {m: wt for m, wt in c["metrics"].items() if m in scales}
            res = sc.composite(norm, w, sc_cfg["min_coverage"], sc_cfg["confident_coverage"]) if w else None
            if res:
                row["scores"][cat] = res
                row["usd_per_100"][cat] = round(
                    sc.usd_per_100(row["cost"]["lowest"]["blended"], res["s"], sc_cfg["score_floor_for_ratio"]), 4
                )
    for cat in cats:
        pts = [(r["id"], r["cost"]["lowest"]["blended"], r["scores"][cat]["s"]) for r in rows.values() if cat in r["scores"]]
        front = set(sc.pareto(pts))
        for r in rows.values():
            r.setdefault("frontier", {})[cat] = r["id"] in front

    # ── 6. alias fit + picks ──────────────────────────────────────────────────────────
    aliases = cfg["aliases"]
    alias_out = {}
    for name, a in aliases.items():
        for r in rows.values():
            fit, share = sc.fit_score({c: (r["scores"].get(c) or {}).get("s") for c in cats}, a["weights"], a.get("min_share", 0.6))
            r.setdefault("fit", {})[name] = fit
            r.setdefault("fit_cov", {})[name] = share
        allc = [{"id": r["id"], "cost": r["cost"]["lowest"]["blended"], "fit": r["fit"][name]} for r in rows.values() if r["fit"][name] is not None]
        ok = [c for c in allc if sc.eligible(rows[c["id"]], a, c["cost"])]
        picks = sc.alias_picks(ok, cfg.get("alias_value_tolerance", 0.1))
        picks["unscored"] = sum(1 for r in rows.values() if r["fit"][name] is None and sc.eligible(r, a, r["cost"]["lowest"]["blended"]))
        picks["current"] = sc.validate_current((cfg.get("current_models") or {}).get(name), ok, allc, picks)
        picks["ranked"] = picks["ranked"][:15]
        alias_out[name] = picks

    # ── 7. metadata, provenance, diagnostics ──────────────────────────────────────────
    status = load_status()
    sources = {}
    for name, mod in SOURCES.items():
        st = status.get(name) or {}
        sources[name] = {
            "label": mod.LABEL, "url": mod.URL, "ok": st.get("ok"), "fetched_at": st.get("fetched_at"),
            "error": st.get("error"), "records": st.get("records"),
            "interval_minutes": cfg["refresh"]["intervals_minutes"].get(name),
        }
    published = (raws["arena"] or {}).get("published", {})
    metric_meta = {}
    for metric in sorted(used):
        meta = dict(METRICS[metric])
        meta["source_label"] = SOURCE_LABELS.get(meta["source"], meta["source"])
        meta["scale"] = {"lo": scales[metric][0], "hi": scales[metric][1]} if metric in scales else None
        meta["models"] = sum(1 for r in rows.values() if metric in r["metrics"])
        meta["as_of"] = published.get(metric) or _date_of((raws.get(meta["source"]) or {}).get("fetched_at"))
        metric_meta[metric] = meta
    families = {strip_date(slug(r["base"])) for r in rows.values()}
    unmatched = []
    for idx_metric, idx in indexes.items():
        if idx_metric not in scales:
            continue
        seen = set()
        for recs in idx.exact.values():
            for r in recs:
                k = (idx_metric, r["label"])
                if k in matched_labels or k in seen or strip_date(slug(r["label"].split("(")[0].split("_")[0])) in families:
                    continue
                seen.add(k)
                unmatched.append({"metric": idx_metric, "name": r["label"], "value": round(r["value"], 2),
                                  "norm": round(sc.normalize(r["value"], scales[idx_metric]), 1)})
    unmatched.sort(key=lambda u: -u["norm"])

    for r in rows.values():
        r["age_days"] = (today - dt.date.fromisoformat(r["released"])).days if r["released"] else None
        r.pop("canonical", None)

    return {
        "schema": SCHEMA,
        "generated_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds"),
        "generated_ts": now,
        "config": {
            "blend": blend,
            "categories": {k: {"label": c["label"], "short": c.get("short", k.title()), "metrics": c["metrics"]} for k, c in cats.items()},
            "aliases": aliases,
            "alias_value_tolerance": cfg.get("alias_value_tolerance", 0.1),
            "current_models": cfg.get("current_models") or {},
            "age": cfg["age"],
            "score_floor_for_ratio": sc_cfg["score_floor_for_ratio"],
        },
        "sources": sources,
        "metrics": metric_meta,
        "models": sorted(rows.values(), key=lambda r: r["id"]),
        "alias_picks": alias_out,
        "diagnostics": {"unmatched": unmatched[:40], "models_without_scores": sum(1 for r in rows.values() if not r["scores"])},
    }


def build_and_write(cfg):
    data = build(cfg)
    write_json_atomic(LATEST, data)
    log.info("wrote %s (%d models)", LATEST, len(data["models"]))
    return data
