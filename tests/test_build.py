"""build() on small synthetic raw data: price selection, name matching, scoring wiring."""

import time
import unittest
from unittest import mock

from leaderboard import build as B

NOW = time.mktime((2026, 11, 16, 12, 0, 0, 0, 0, 0))
TS = NOW - 20 * 86400


def cfg():
    return {
        "refresh": {"intervals_minutes": {"openrouter": 30, "nous": 30, "litellm": 360, "arena": 720, "epoch": 1440}},
        "cost": {
            "blend": {"input": 3, "output": 1}, "exclude_variants": ["batch", "US"], "min_uptime_pct": 80,
            "include_service_tiers": [], "exclude_quantizations": ["int4", "fp4"],
            "primary_litellm_providers": {"acme_direct": ["acme"]},
            "primary_openrouter_providers": {},
        },
        "scoring": {"low_quantile": 0.0, "high_quantile": 1.0, "min_metric_population": 2, "min_coverage": 0.3,
                    "confident_coverage": 0.6, "score_floor_for_ratio": 5},
        "categories": {
            "coding": {"label": "Coding", "short": "Coding", "metrics": {"aa_coding": 3, "swe_bench_verified": 2}},
            "reasoning": {"label": "Reasoning", "short": "Reasoning", "metrics": {"gpqa_diamond": 2}},
        },
        "aliases": {
            "Cheap": {"max_cost": 1.0, "weights": {"coding": 0.5, "reasoning": 0.5}},
            "Free": {"max_cost": 0, "require": ["free"], "min_share": 0.4, "weights": {"coding": 0.5, "reasoning": 0.5}},
        },
        "alias_value_tolerance": 0.1,
        "current_models": {"Cheap": "acme/gamma-2"},
        "age": {"fresh_days": 90, "faded_days": 450, "min_opacity": 0.12},
        "name_overrides": {},
    }


def orm(mid, bench=None, tools=True, **kw):
    m = {"id": mid, "canonical_slug": None, "name": f"Acme: {mid.split('/')[1]}", "created": TS, "context_length": 200000,
         "in": 1.0, "out": 4.0, "cache_read": None, "input_modalities": ["text"], "output_modalities": ["text"],
         "tools": tools, "reasoning": False, "expiration": None, "benchmarks": bench}
    m.update(kw)
    return m


def ep(provider, i, o, quant=None, tier=None, status=0, uptime=99.0):
    return {"provider": provider, "tag": None, "tier": tier, "quant": quant, "status": status, "uptime": uptime,
            "in": i, "out": o, "cache_read": None, "ctx": 200000}


def aa(v):
    return {"artificial_analysis": {"intelligence_index": None, "coding_index": v, "agentic_index": None}}


RAW = {
    "openrouter": {"models": [
        orm("acme/alpha-1", aa(80)), orm("acme/alpha-1:batch", aa(80)), orm("~acme/alpha-latest"),
        orm("acme/gamma-2", aa(40)), orm("acme/delta-3", aa(60)),
        orm("acme/beta-free:free", aa(30), **{"in": 0.0, "out": 0.0}),
        orm("acme/expired-9", aa(50), expiration="2026-01-01"),
    ], "endpoints": {
        "acme/alpha-1": [
            ep("Acme", 1.0, 4.0),                                  # first party, $1.75 blended
            ep("OkHost", 0.5, 2.0, quant="fp8"),                   # best legitimate third party: $0.875
            ep("CheapHost", 0.2, 0.8, quant="fp4"),                # 4-bit third party: excluded
            ep("FlexHost", 0.1, 0.1, tier="flex"),                 # non-default tier: excluded
            ep("DownHost", 0.01, 0.01, status=-2),                 # unhealthy: excluded
            ep("FlakyHost", 0.02, 0.02, uptime=40.0),              # low uptime: excluded
        ],
        "acme/gamma-2": [ep("Acme", 0.2, 0.8)],
        "acme/delta-3": [ep("Acme", 2.0, 8.0, quant="fp4"), ep("Other", 1.0, 3.0, quant="fp8")],  # first-party fp4 is the reference
    }},
    "nous": {"models": [
        {"id": "acme/alpha-1", "name": "Acme: alpha-1", "created": TS, "context_length": 200000, "in": 0.4, "out": 1.6,
         "orig_in": 0.8, "orig_out": 3.2, "output_modalities": ["text"], "tools": True, "reasoning": False, "frontier": True},
        {"id": "acme/nous-only", "name": "Acme: nous-only", "created": TS, "context_length": 64000, "in": 0.3, "out": 0.3,
         "orig_in": None, "orig_out": None, "output_modalities": ["text"], "tools": False, "reasoning": False, "frontier": False},
        {"id": "acme/embed-1", "name": "embed", "created": TS, "in": 0.1, "out": 0, "output_modalities": [], "tools": False, "reasoning": False},
    ]},
    "litellm": {"entries": [
        {"key": "alpha-1", "provider": "acme_direct", "in": 1.0, "out": 4.0, "cache_read": None, "deprecated": None},
        {"key": "tencent/alpha-1", "provider": "reseller", "in": 0.01, "out": 0.01, "cache_read": None, "deprecated": None},
    ]},
    "arena": {"records": [], "published": {}},
    "epoch": {"records": [
        {"metric": "gpqa_diamond", "name": "alpha-1_high", "value": 90.0, "date": None, "agent": None},
        {"metric": "gpqa_diamond", "name": "alpha-1_max", "value": 92.0, "date": None, "agent": None},
        {"metric": "gpqa_diamond", "name": "gamma-2_low", "value": 50.0, "date": None, "agent": None},
        {"metric": "gpqa_diamond", "name": "unknown-model", "value": 99.0, "date": None, "agent": None},
    ]},
}


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.object(B, "load_raw", side_effect=lambda n: RAW.get(n)), mock.patch.object(B, "load_status", return_value={}):
            cls.d = B.build(cfg(), now=NOW)
        cls.by = {m["id"]: m for m in cls.d["models"]}

    def test_excluded_rows(self):
        for gone in ("acme/alpha-1:batch", "~acme/alpha-latest", "acme/expired-9", "acme/embed-1"):
            self.assertNotIn(gone, self.by)
        self.assertIn("acme/nous-only", self.by)  # Nous-only models are still purchasable

    def test_openrouter_channel_filters(self):
        provs = [c["provider"] for c in self.by["acme/alpha-1"]["cost"]["channels"]["openrouter"]]
        self.assertEqual(provs, ["OkHost"])  # fp4, flex tier, down, low-uptime all excluded; Acme dominated by OkHost

    def test_primary_is_the_labs_own_price_only(self):
        prim = self.by["acme/alpha-1"]["cost"]["channels"]["primary"]
        self.assertEqual([c["provider"] for c in prim], ["Acme"])  # the reseller's $0.01 list price must not count

    def test_first_party_quantisation_is_kept(self):
        prim = self.by["acme/delta-3"]["cost"]["channels"]["primary"]
        self.assertEqual((prim[0]["provider"], prim[0]["note"]), ("Acme", "fp4"))
        self.assertEqual([c["provider"] for c in self.by["acme/delta-3"]["cost"]["channels"]["openrouter"]], ["Other"])

    def test_lowest_across_channels(self):
        low = self.by["acme/alpha-1"]["cost"]["lowest"]
        self.assertEqual((low["channel"], round(low["blended"], 3)), ("nous", 0.7))  # nous 0.4/1.6 beats OR 0.875 and primary 1.75
        self.assertIn("promo", self.by["acme/alpha-1"]["cost"]["channels"]["nous"][0]["note"])

    def test_free_variant(self):
        r = self.by["acme/beta-free:free"]
        self.assertTrue(r["free"])
        self.assertEqual(r["cost"]["lowest"]["blended"], 0)
        self.assertFalse(self.by["acme/alpha-1"]["free"])

    def test_metrics_matched_with_best_effort_level(self):
        m = self.by["acme/alpha-1"]["metrics"]
        self.assertEqual(m["aa_coding"]["v"], 80)
        self.assertEqual((m["gpqa_diamond"]["v"], m["gpqa_diamond"]["how"], m["gpqa_diamond"]["n"]), (92.0, "exact", 2))
        self.assertNotIn("gpqa_diamond", self.by["acme/delta-3"]["metrics"])  # no fuzzy matching

    def test_free_row_shares_base_model_benchmarks(self):
        self.assertEqual(self.by["acme/beta-free:free"]["metrics"]["aa_coding"]["v"], 30)

    def test_scores_and_value(self):
        a = self.by["acme/alpha-1"]
        self.assertEqual(a["scores"]["coding"]["s"], 100.0)  # top of the population
        self.assertEqual(a["usd_per_100"]["coding"], round(0.7 / 100 * 100, 4))
        self.assertNotIn("reasoning", self.by["acme/delta-3"]["scores"])

    def test_alias_picks_respect_caps(self):
        cheap = self.d["alias_picks"]["Cheap"]
        self.assertTrue(cheap["top"] in self.by and self.by[cheap["top"]]["cost"]["lowest"]["blended"] <= 1.0)
        self.assertEqual(self.d["alias_picks"]["Free"]["top"], None if self.by["acme/beta-free:free"]["fit"]["Free"] is None else "acme/beta-free:free")
        self.assertEqual(cheap["current"]["id"], "acme/gamma-2")

    def test_config_echo_carries_what_the_fit_dialog_explains(self):
        sc = self.d["config"]["scoring"]
        self.assertEqual((sc["low_quantile"], sc["high_quantile"], sc["min_coverage"]), (0.0, 1.0, 0.3))
        self.assertEqual(self.d["config"]["categories"]["coding"]["short"], "Coding")
        self.assertIn("min_share", self.d["config"]["aliases"]["Free"])

    def test_fit_is_the_weighted_mean_of_category_scores(self):
        a = self.by["acme/alpha-1"]
        w = self.d["config"]["aliases"]["Cheap"]["weights"]
        have = {c: w[c] for c in w if c in a["scores"]}
        expect = sum(a["scores"][c]["s"] * wt for c, wt in have.items()) / sum(have.values())
        self.assertAlmostEqual(a["fit"]["Cheap"], expect, places=1)

    def test_unmatched_diagnostics_lists_unknown_names(self):
        self.assertIn("unknown-model", [u["name"] for u in self.d["diagnostics"]["unmatched"]])


class AliasMetricWeightTests(unittest.TestCase):
    """aliases.<name>.metric_weights re-weights benchmarks for that alias only."""

    @staticmethod
    def run_build(mutate):
        c = cfg()
        mutate(c)
        with mock.patch.object(B, "load_raw", side_effect=lambda n: RAW.get(n)), mock.patch.object(B, "load_status", return_value={}):
            d = B.build(c, now=NOW)
        return {m["id"]: m for m in d["models"]}

    def test_zeroing_a_benchmark_changes_only_that_alias(self):
        def zero_gpqa(c):
            c["aliases"]["Cheap"].update(min_share=0.4, metric_weights={"reasoning": {"gpqa_diamond": 0}})
        base, tuned = self.run_build(lambda c: None), self.run_build(zero_gpqa)
        a0, a1 = base["acme/gamma-2"], tuned["acme/gamma-2"]  # scores differ between coding and reasoning
        # with reasoning dropped, Cheap's fit is just the coding score
        self.assertEqual(a1["fit"]["Cheap"], a1["scores"]["coding"]["s"])
        self.assertEqual((a0["fit"]["Cheap"], a1["fit"]["Cheap"]), (10.0, 20.0))
        # other aliases and the shared category scores are untouched
        self.assertEqual(a0["fit"]["Free"], a1["fit"]["Free"])
        self.assertEqual(a0["scores"], a1["scores"])

    def test_default_fit_unchanged_without_overrides(self):
        a = self.run_build(lambda c: None)["acme/alpha-1"]
        w = {"coding": 0.5, "reasoning": 0.5}
        expect = round(sum(a["scores"][c]["s"] * wt for c, wt in w.items()) / sum(w.values()), 1)
        self.assertAlmostEqual(a["fit"]["Cheap"], expect, places=1)


if __name__ == "__main__":
    unittest.main()
