import json
import os
import tempfile
import unittest
from pathlib import Path

from leaderboard import weights as W

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


class FileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "weights.json"
        os.environ["LEADERBOARD_WEIGHTS"] = str(self.path)

    def tearDown(self):
        os.environ.pop("LEADERBOARD_WEIGHTS", None)
        self.dir.cleanup()

    def test_path_can_be_overridden(self):
        self.assertEqual(W.weights_path(), self.path)

    def test_round_trip_is_human_readable_and_sparse(self):
        saved = W.save(CFG, {"Coding": {"cats": {"coding": 0.6}}})
        self.assertEqual(W.load(CFG), {"Coding": {"cats": {"coding": 0.6}}})
        text = self.path.read_text()
        self.assertIn('\n  "aliases"', text)  # indented JSON a person can read and edit
        self.assertEqual(json.loads(text)["saved_at"], saved["saved_at"])
        self.assertEqual(W.saved_at(), saved["saved_at"])

    def test_saving_nothing_keeps_an_empty_file(self):
        W.save(CFG, {})
        self.assertEqual(W.load(CFG), {})
        self.assertTrue(self.path.exists())

    def test_invalid_save_leaves_the_file_untouched(self):
        W.save(CFG, {"Coding": {"cats": {"coding": 0.6}}})
        before = self.path.read_text()
        with self.assertRaises(ValueError):
            W.save(CFG, {"Coding": {"cats": {"coding": -5}}})
        self.assertEqual(self.path.read_text(), before)

    def test_missing_or_corrupt_file_means_no_overrides(self):
        self.assertEqual(W.load(CFG), {})
        self.path.write_text("{ this is not json")
        self.assertEqual(W.load(CFG), {})
        self.path.write_text('{"aliases": {"Coding": {"cats": {"coding": "lots"}}}}')
        self.assertEqual(W.load(CFG), {})  # a hand-edit typo can't break the build


if __name__ == "__main__":
    unittest.main()
