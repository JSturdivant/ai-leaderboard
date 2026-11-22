"""Cache-aware conversation costing and the stay/switch recommendation."""

import copy
import unittest

from modelfit import convcost, ranker
from tests.test_ranking_api import snapshot


def chan(i, o, cr=None, cw=None, tier=None, provider="P"):
    return {"in": i, "out": o, "cr": cr, "cw": cw, "long_tier": tier, "provider": provider}


class SimulateTests(unittest.TestCase):
    def test_no_cache_pays_full_input_every_turn(self):
        per, cached = convcost.simulate(chan(1.0, 2.0), prefix=10_000, new=100, out=500, turns=2)
        self.assertEqual(cached, 0)
        self.assertAlmostEqual(per[0], (10_100 * 1.0 + 500 * 2.0) / 1e6)
        self.assertAlmostEqual(per[1], ((10_100 + 500 + 100) * 1.0 + 500 * 2.0) / 1e6)  # history grows

    def test_warm_cache_reads_the_prefix_cheaply(self):
        warm, cached = convcost.simulate(chan(1.0, 2.0, cr=0.1), 10_000, 100, 500, 1, warm_tokens=10_000)
        cold, _ = convcost.simulate(chan(1.0, 2.0, cr=0.1), 10_000, 100, 500, 1, warm_tokens=0)
        self.assertEqual(cached, 10_000)
        self.assertAlmostEqual(warm[0], (10_000 * 0.1 + 100 * 1.0 + 500 * 2.0) / 1e6)
        self.assertLess(warm[0], cold[0])

    def test_cold_cache_warms_up_from_the_second_turn(self):
        per, _ = convcost.simulate(chan(1.0, 0.0, cr=0.1), 10_000, 100, 0, 2, tokens_per_turn=100)
        self.assertAlmostEqual(per[1], (10_100 * 0.1 + 100 * 1.0) / 1e6)

    def test_write_premium_and_small_prompts(self):
        prem, _ = convcost.simulate(chan(1.0, 0.0, cr=0.1, cw=1.25), 10_000, 0, 0, 1)
        self.assertAlmostEqual(prem[0], 10_000 * 1.25 / 1e6)
        cheap_write, _ = convcost.simulate(chan(1.0, 0.0, cr=0.1, cw=0.5), 10_000, 0, 0, 1)  # storage-style write: no premium
        self.assertAlmostEqual(cheap_write[0], 10_000 * 1.0 / 1e6)
        _, cached = convcost.simulate(chan(1.0, 0.0, cr=0.1), 500, 10, 0, 1, warm_tokens=500)
        self.assertEqual(cached, 0)  # below the minimum cacheable size

    def test_long_context_tier(self):
        tier = {"min_prompt": 100_000, "in": 2.0, "out": 4.0, "cache_read": 0.2, "cache_write": None}
        per, _ = convcost.simulate(chan(1.0, 2.0, cr=0.1, tier=tier), 150_000, 0, 1000, 1)
        self.assertAlmostEqual(per[0], (150_000 * 2.0 + 1000 * 4.0) / 1e6)

    def test_best_channel_and_breakeven(self):
        chans = {"a": [chan(1.0, 1.0, provider="A")], "b": [chan(1.2, 1.0, cr=0.05, provider="B")]}
        one = convcost.best_channel(chans, 50_000, 100, 100, 1)
        many = convcost.best_channel(chans, 50_000, 100, 100, 6)
        self.assertEqual(one["provider"], "A")        # single shot: cheapest input wins
        self.assertEqual(many["provider"], "B")       # long conversation: cache reads win
        pinned = convcost.best_channel(chans, 50_000, 100, 100, 1, warm={"tokens": 50_000, "provider": "B"})
        self.assertEqual((pinned["provider"], pinned["cached_first"]), ("B", 50_000))
        self.assertEqual(convcost.breakeven([1, 1, 1, 1], [2.5, 0.2, 0.2, 0.2]), 3)
        self.assertEqual(convcost.breakeven([1, 1], [0.5, 0.5]), 1)
        self.assertIsNone(convcost.breakeven([1, 1], [5, 1]))


def cache_snapshot():
    """Two near-identical models: 'alpha' and 'gamma'; both cache at 10% of input."""
    snap = copy.deepcopy(snapshot())
    for m in snap["models"]:
        for cands in m["cost"]["channels"].values():
            for c in cands:
                c.update(cr=round(c["in"] * 0.1, 6), cw=None, long_tier=None)
    return snap


CONV = [{"role": "system", "content": "context " * 40_000},
        {"role": "user", "content": "first question " * 30}, {"role": "assistant", "content": "answer " * 300},
        {"role": "user", "content": "next question please"}]


class SwitchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = cache_snapshot()

    def rank(self, **body):
        return ranker.rank(self.snap, ranker.parse({"messages": CONV, "weights": {"savings": 1}, **body}))

    def test_conversation_plan_splits_history_from_the_new_turn(self):
        r = self.rank(current_model="acme/gamma-2")
        conv = r["assumptions"]["conversation"]
        self.assertGreater(conv["prefix_tokens"], 40_000)
        self.assertLess(conv["new_tokens"], 20)
        self.assertEqual(conv["horizon_turns"], 5)            # conversational default
        self.assertTrue(conv["current_model_cache_warm"])

    def test_warm_cache_makes_the_current_model_cheaper_this_turn(self):
        r = self.rank(current_model="acme/gamma-2", limit=50)
        cur = next(x for x in r["results"] if x["is_current"])
        self.assertTrue(cur["cache"]["warm"])
        self.assertGreater(cur["cache"]["cached_tokens"], 40_000)
        self.assertGreater(cur["cache"]["saved_this_turn_usd"], 0)
        cold = self.rank(current_model="acme/gamma-2", cache={"seconds_since_last_request": 3600}, limit=50)
        cur_cold = next(x for x in cold["results"] if x["is_current"])
        self.assertFalse(cur_cold["cache"]["warm"])
        self.assertGreater(cur_cold["cost"]["estimated_request_usd"], cur["cost"]["estimated_request_usd"])

    def test_stays_while_the_cache_is_warm_and_switches_once_it_is_cold(self):
        """alpha: cheaper per token than gamma but no prompt caching. Warm cache on gamma -> stay; cold -> switch."""
        snap = copy.deepcopy(self.snap)
        for m in snap["models"]:
            if m["id"] == "acme/alpha-1":
                m["cost"]["channels"] = {"openrouter": [chan(0.15, 0.6, provider="Cheap")]}
        body = {"messages": CONV, "weights": {"savings": 1}, "current_model": "acme/gamma-2", "filters": {"free": False,
                "models": ["acme/alpha-1", "acme/gamma-2"]}}
        warm = ranker.rank(snap, ranker.parse({**body, "cache": {"expected_turns": 3}}))["switching"]
        self.assertEqual((warm["recommendation"], warm["best_alternative"]), ("stay", "acme/alpha-1"))
        self.assertGreater(warm["switch_now_extra_usd"], 0)     # giving up the warm cache costs money now
        self.assertIsNone(warm["breakeven_turn"])               # and the cache-less model never catches up
        self.assertIn("cache is warm", warm["reason"])
        cold = ranker.rank(snap, ranker.parse({**body, "cache": {"expected_turns": 1, "seconds_since_last_request": 3600}}))["switching"]
        self.assertEqual(cold["recommendation"], "switch")
        self.assertIn("cache is cold", cold["reason"])

    def test_switches_when_the_current_model_fails_a_filter(self):
        r = self.rank(current_model="acme/alpha-1", filters={"open_weights": True})
        sw = r["switching"]
        self.assertEqual(sw["recommendation"], "switch")
        self.assertEqual(sw["excluded_reason"], "open_weights_filter")

    def test_quality_gap_beats_the_cache(self):
        r = ranker.rank(self.snap, ranker.parse({"messages": CONV, "current_model": "acme/gamma-2", "weights": {"performance": 1},
                                                 "performance": {"coding": 1}}))
        sw = r["switching"]
        self.assertEqual((sw["recommendation"], sw["best_alternative"]), ("switch", "acme/alpha-1"))
        self.assertGreaterEqual(sw["score_gain"], 2)

    def test_threshold_and_unknown_models(self):
        r = ranker.rank(self.snap, ranker.parse({"messages": CONV, "current_model": "acme/gamma-2", "weights": {"performance": 1},
                                                 "performance": {"coding": 1}, "min_switch_gain": 100}))
        self.assertEqual(r["switching"]["recommendation"], "stay")
        r = self.rank(current_model="nobody/unknown-model")
        self.assertFalse(r["switching"]["known"])
        self.assertIsNone(self.rank()["switching"])

    def test_validation(self):
        for body, field in (({"current_model": "nope"}, "current_model"), ({"cache": {"bogus": 1}}, "cache.bogus"),
                            ({"cache": {"expected_turns": 0}}, "cache.expected_turns"), ({"min_switch_gain": -1}, "min_switch_gain")):
            with self.assertRaises(ranker.RankError) as cm:
                ranker.parse(body)
            self.assertEqual(cm.exception.field, field)


if __name__ == "__main__":
    unittest.main()
