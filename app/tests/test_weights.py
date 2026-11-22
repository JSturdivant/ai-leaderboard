import json
import os
import tempfile
import unittest
from pathlib import Path

from modelfit import weights as W

CFG = {
    "categories": {"coding": {}, "chat": {}},
    "aliases": {
        "Coding": {"weights": {"coding": 0.75, "chat": 0.25}, "metric_weights": {"coding": {"scicode": 2}}},
        "Free": {"weights": {"chat": 1.0}},
    },
}


class ParseTests(unittest.TestCase):
    def test_valid_overlay_passes_through(self):
        raw = {"Coding": {"cats": {"coding": 0.6}, "metrics": {"coding": {"swe_bench_verified": 5, "arena_webdev": 0}}}}
        self.assertEqual(W.parse(raw, CFG, strict=True), raw)

    def test_strict_rejects_every_kind_of_bad_input(self):
        for raw in (
            {"Nope": {"cats": {"coding": 1}}},                       # unknown alias
            {"Coding": {"cats": {"flying": 1}}},                      # unknown dimension
            {"Coding": {"metrics": {"coding": {"made_up": 1}}}},      # unknown benchmark
            {"Coding": {"cats": {"coding": -1}}},                     # negative
            {"Coding": {"cats": {"coding": float("nan")}}},           # NaN
            {"Coding": {"cats": {"coding": 1e9}}},                    # absurd
            {"Coding": {"cats": {"coding": True}}},                   # bool is not a weight
            {"Coding": {"cats": {"coding": "3"}}},                    # string
            {"Coding": "oops"},                                       # wrong shape
            [],                                                       # wrong top level
        ):
            with self.assertRaises(ValueError, msg=repr(raw)):
                W.parse(raw, CFG, strict=True)

    def test_lenient_keeps_the_good_and_drops_the_bad(self):
        raw = {"Coding": {"cats": {"coding": 0.5, "flying": 1}, "metrics": {"coding": {"scicode": 3, "made_up": 1}}}, "Nope": {"cats": {"chat": 1}}}
        self.assertEqual(W.parse(raw, CFG), {"Coding": {"cats": {"coding": 0.5}, "metrics": {"coding": {"scicode": 3}}}})

    def test_empty_entries_vanish(self):
        self.assertEqual(W.parse({"Coding": {"cats": {}, "metrics": {}}}, CFG), {})


class MergeTests(unittest.TestCase):
    def test_no_overlay_returns_the_config_alias(self):
        self.assertIs(W.merge_alias(CFG["aliases"]["Coding"], None), CFG["aliases"]["Coding"])

    def test_dimension_weights_overlay_and_can_add_a_dimension(self):
        m = W.merge_alias(CFG["aliases"]["Free"], {"cats": {"coding": 0.2, "chat": 0.5}})
        self.assertEqual(m["weights"], {"chat": 0.5, "coding": 0.2})

    def test_benchmark_weights_merge_over_the_alias_config(self):
        m = W.merge_alias(CFG["aliases"]["Coding"], {"metrics": {"coding": {"swe_bench_verified": 5}}})
        self.assertEqual(m["metric_weights"], {"coding": {"scicode": 2, "swe_bench_verified": 5}})

    def test_merge_does_not_mutate_the_config(self):
        before = json.dumps(CFG, sort_keys=True)
        W.merge_alias(CFG["aliases"]["Coding"], {"cats": {"coding": 0.1}, "metrics": {"coding": {"scicode": 9}}})
        self.assertEqual(json.dumps(CFG, sort_keys=True), before)


class CapTests(unittest.TestCase):
    def test_numbers_and_null_are_valid(self):
        self.assertEqual(W.parse_caps({"Coding": 0.25, "Free": None, "Coding2": 1}, {"aliases": {"Coding": {}, "Free": {}}}, strict=False), {"Coding": 0.25, "Free": None})

    def test_strict_rejects_bad_caps(self):
        for raw in ({"Nope": 1}, {"Coding": -1}, {"Coding": "1"}, {"Coding": True}, {"Coding": float("inf")}, {"Coding": 1e9}, [1]):
            with self.assertRaises(ValueError, msg=repr(raw)):
                W.parse_caps(raw, CFG, strict=True)

    def test_merge_applies_a_cap_even_when_there_are_no_weights(self):
        a = CFG["aliases"]["Coding"]
        self.assertIs(W.merge_alias(a, None), a)
        self.assertEqual(W.merge_alias(dict(a, max_cost=1.0), None, 0.2)["max_cost"], 0.2)
        self.assertIsNone(W.merge_alias(dict(a, max_cost=1.0), None, None)["max_cost"])  # None = an explicit "no cap"
        self.assertEqual(W.merge_alias(dict(a, max_cost=1.0), {"cats": {"chat": 1}}, 0.2)["weights"]["chat"], 1)


class LegacyFileTests(unittest.TestCase):
    def test_reads_an_old_single_user_weights_file_for_import(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "weights.json"
            p.write_text(json.dumps({"aliases": {"Coding": {"cats": {"coding": 1}}}, "caps": {"Daily": 0.2}, "recommendations": {}}))
            os.environ["LEADERBOARD_WEIGHTS"] = str(p)
            try:
                got = W.read_legacy()
            finally:
                os.environ.pop("LEADERBOARD_WEIGHTS")
        self.assertEqual((got["aliases"], got["caps"]), ({"Coding": {"cats": {"coding": 1}}}, {"Daily": 0.2}))

    def test_missing_or_empty_file_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["LEADERBOARD_WEIGHTS"] = str(Path(d) / "nope.json")
            try:
                self.assertIsNone(W.read_legacy())
            finally:
                os.environ.pop("LEADERBOARD_WEIGHTS")


class AliasDefTests(unittest.TestCase):
    CATS = {"coding": {}, "chat": {}}

    def test_valid_definition_is_cleaned(self):
        d = W.parse_alias_def("Summarizer", {"weights": {"chat": 1, "bogus": 2}, "max_cost": 0.3, "require": ["tools", "x"],
                                             "min_context": 32000, "note": "n"}, self.CATS)
        self.assertEqual(d, {"note": "n", "weights": {"chat": 1}, "max_cost": 0.3, "require": ["tools"], "min_context": 32000})

    def test_free_requirement_forces_a_zero_cap(self):
        self.assertEqual(W.parse_alias_def("F", {"weights": {"chat": 1}, "require": ["free"], "max_cost": 5}, self.CATS)["max_cost"], 0)

    def test_bad_definitions_are_rejected(self):
        for name, raw in (("", {"weights": {"chat": 1}}), ("bad/name", {"weights": {"chat": 1}}), ("X" * 40, {"weights": {"chat": 1}}),
                          ("Ok", {"weights": {"chat": 0}}), ("Ok", {"weights": {"chat": 1}, "max_cost": -1}), ("Ok", [])):
            with self.assertRaises(ValueError, msg=(name, raw)):
                W.parse_alias_def(name, raw, self.CATS)


if __name__ == "__main__":
    unittest.main()
