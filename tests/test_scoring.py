import unittest

from leaderboard import scoring as sc


class ScaleTests(unittest.TestCase):
    def test_quantile_interpolates(self):
        self.assertEqual(sc.quantile([0, 10], 0.5), 5)
        self.assertEqual(sc.quantile([1, 2, 3, 4, 5], 0), 1)
        self.assertEqual(sc.quantile([1, 2, 3, 4, 5], 1), 5)

    def test_scale_needs_population_and_range(self):
        self.assertIsNone(sc.scale_for([1, 2, 3], 0.05, 0.99, min_n=6))
        self.assertIsNone(sc.scale_for([5] * 10, 0.05, 0.99, min_n=6))
        lo, hi = sc.scale_for(range(101), 0.05, 0.95, min_n=6)
        self.assertEqual((lo, hi), (5, 95))

    def test_normalize_clamps(self):
        self.assertEqual(sc.normalize(50, (0, 100)), 50)
        self.assertEqual(sc.normalize(-5, (0, 100)), 0)
        self.assertEqual(sc.normalize(500, (0, 100)), 100)


class CompositeTests(unittest.TestCase):
    W = {"a": 3, "b": 1}

    def test_weighted_mean_over_available(self):
        r = sc.composite({"a": 80, "b": 40}, self.W, 0.35, 0.6)
        self.assertEqual(r["s"], 70.0)
        self.assertTrue(r["confident"])

    def test_missing_metric_renormalises_but_lowers_coverage(self):
        r = sc.composite({"b": 40}, self.W, 0.2, 0.6)
        self.assertEqual((r["s"], r["cov"], r["confident"]), (40.0, 0.25, False))

    def test_below_min_coverage_is_none(self):
        self.assertIsNone(sc.composite({"b": 40}, self.W, 0.35, 0.6))
        self.assertIsNone(sc.composite({}, self.W, 0.35, 0.6))


class ValueTests(unittest.TestCase):
    def test_usd_per_100(self):
        self.assertAlmostEqual(sc.usd_per_100(0.5, 50, 5), 1.0)
        self.assertEqual(sc.usd_per_100(0, 70, 5), 0.0)
        self.assertIsNone(sc.usd_per_100(1.0, None, 5))

    def test_floor_prevents_blowup_for_tiny_scores(self):
        self.assertAlmostEqual(sc.usd_per_100(1.0, 0.1, 5), 20.0)

    def test_blended(self):
        self.assertEqual(sc.blended(1, 5, {"input": 3, "output": 1}), 2.0)
        self.assertEqual(sc.blended(1, 5, {"input": 0, "output": 1}), 5.0)

    def test_pareto(self):
        pts = [("cheap-bad", 1, 10), ("mid", 2, 50), ("dominated", 3, 40), ("top", 5, 90), ("free", 0, 5)]
        self.assertEqual(sc.pareto(pts), ["free", "cheap-bad", "mid", "top"])

    def test_pareto_same_cost_keeps_best(self):
        self.assertEqual(sc.pareto([("a", 1, 10), ("b", 1, 20)]), ["b"])


class AliasTests(unittest.TestCase):
    def test_fit_score_share(self):
        w = {"chat": 0.5, "coding": 0.5}
        self.assertEqual(sc.fit_score({"chat": 80, "coding": 60}, w), (70.0, 1.0))
        self.assertEqual(sc.fit_score({"chat": 80}, w, min_share=0.4), (80.0, 0.5))
        self.assertEqual(sc.fit_score({"chat": 80}, w, min_share=0.6), (None, 0.5))

    def test_eligible_constraints(self):
        row = {"free": False, "tools": True, "reasoning": False, "ctx": 200000}
        self.assertTrue(sc.eligible(row, {"max_cost": 1.0, "require": ["tools"]}, 0.5))
        self.assertFalse(sc.eligible(row, {"max_cost": 0.4}, 0.5))
        self.assertTrue(sc.eligible(row, {"max_cost": None}, 99))
        self.assertFalse(sc.eligible(row, {"require": ["reasoning"]}, 0.1))
        self.assertFalse(sc.eligible(row, {"require": ["free"]}, 0.1))
        self.assertFalse(sc.eligible(row, {"min_context": 500000}, 0.1))
        self.assertFalse(sc.eligible(row, {}, None))

    def test_picks_top_fit_and_best_value(self):
        c = [{"id": "big", "cost": 10, "fit": 95}, {"id": "near", "cost": 1, "fit": 90}, {"id": "cheap", "cost": 0.1, "fit": 60}]
        p = sc.alias_picks(c, tolerance=0.1)
        self.assertEqual((p["top"], p["best_value"]), ("big", "near"))  # near is within 10% of 95
        self.assertEqual(p["frontier"], ["cheap", "near", "big"])
        self.assertEqual(sc.alias_picks(c, tolerance=0.01)["best_value"], "big")

    def test_empty(self):
        self.assertIsNone(sc.alias_picks([], 0.1)["top"])

    def test_validate_current(self):
        allc = [{"id": "a", "cost": 1, "fit": 50}, {"id": "b", "cost": 1, "fit": 70}, {"id": "c", "cost": 5, "fit": 90}]
        ok = allc[:2]
        picks = sc.alias_picks(ok, 0.1)
        v = sc.validate_current("a", ok, allc, picks)
        self.assertEqual((v["status"], v["rank"], v["of"], v["better"]), ("ok", 2, 2, ["b"]))
        self.assertEqual(sc.validate_current("c", ok, allc, picks)["status"], "ineligible")
        self.assertEqual(sc.validate_current("zzz", ok, allc, picks)["status"], "no_data")
        self.assertIsNone(sc.validate_current(None, ok, allc, picks))


if __name__ == "__main__":
    unittest.main()
