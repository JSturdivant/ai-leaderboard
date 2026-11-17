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


class MigrationTests(unittest.TestCase):
    def test_old_root_level_file_moves_once_and_is_set_aside(self):
        with tempfile.TemporaryDirectory() as d:
            legacy, dest = Path(d) / "weights.json", Path(d) / "local" / "weights.json"
            legacy.write_text('{"aliases": {"Coding": {"cats": {"coding": 0.6}}}}')
            self.assertTrue(W.migrate_legacy(legacy, dest))
            self.assertEqual(json.loads(dest.read_text())["aliases"]["Coding"]["cats"]["coding"], 0.6)
            self.assertFalse(legacy.exists())
            self.assertTrue((Path(d) / "weights.json.moved-to-local").exists())  # kept as a backup, cannot resurrect
            self.assertFalse(W.migrate_legacy(legacy, dest))  # nothing left to move

    def test_an_existing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            legacy, dest = Path(d) / "old.json", Path(d) / "new.json"
            legacy.write_text('{"aliases": {"Coding": {"cats": {"coding": 0.1}}}}')
            dest.write_text('{"aliases": {}}')
            self.assertFalse(W.migrate_legacy(legacy, dest))
            self.assertEqual(dest.read_text(), '{"aliases": {}}')
            self.assertTrue(legacy.exists())

    def test_no_legacy_file_is_a_no_op(self):
        self.assertFalse(W.migrate_legacy(None, "/nonexistent/x.json"))


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
        self.assertEqual(W.load(CFG), {"aliases": {"Coding": {"cats": {"coding": 0.6}}}, "caps": {}})
        text = self.path.read_text()
        self.assertIn('\n  "aliases"', text)  # indented JSON a person can read and edit
        self.assertEqual(json.loads(text)["saved_at"], saved["saved_at"])
        self.assertEqual(W.saved_at(), saved["saved_at"])

    def test_saving_nothing_keeps_an_empty_file(self):
        W.save(CFG, {})
        self.assertEqual(W.load(CFG), {"aliases": {}, "caps": {}})
        self.assertTrue(self.path.exists())

    def test_invalid_save_leaves_the_file_untouched(self):
        W.save(CFG, {"Coding": {"cats": {"coding": 0.6}}})
        before = self.path.read_text()
        with self.assertRaises(ValueError):
            W.save(CFG, {"Coding": {"cats": {"coding": -5}}})
        self.assertEqual(self.path.read_text(), before)

    RECS = {"generated_at": "2026-11-17T18:00:00+00:00", "aliases": {"Coding": {"picks": [{"rank": 1, "model": "a/b"}]}}}

    def test_caps_round_trip_and_omitting_them_keeps_the_saved_ones(self):
        W.save(CFG, {}, {"Coding": 0.5, "Free": None})
        self.assertEqual(W.load(CFG)["caps"], {"Coding": 0.5, "Free": None})
        W.save(CFG, {"Coding": {"cats": {"coding": 0.6}}})            # caps left out: unchanged
        self.assertEqual(W.load(CFG)["caps"], {"Coding": 0.5, "Free": None})
        W.save(CFG, {}, {})                                            # explicit empty: cleared
        self.assertEqual(W.load(CFG)["caps"], {})

    def test_recommendations_are_stored_beside_the_settings_without_touching_them(self):
        W.save(CFG, {"Coding": {"cats": {"coding": 0.6}}}, {"Coding": 0.5})
        self.assertTrue(W.write_recommendations(self.RECS))
        raw = json.loads(self.path.read_text())
        self.assertEqual((raw["aliases"], raw["caps"], raw["recommendations"]), ({"Coding": {"cats": {"coding": 0.6}}}, {"Coding": 0.5}, self.RECS))
        self.assertEqual(list(raw), ["saved_at", "aliases", "caps", "recommendations"])  # settings first, big section last

    def test_saving_weights_keeps_the_recommendations_until_the_rebuild_replaces_them(self):
        W.write_recommendations(self.RECS)
        W.save(CFG, {"Coding": {"cats": {"coding": 0.7}}})
        self.assertEqual(json.loads(self.path.read_text())["recommendations"], self.RECS)

    def test_recommendations_can_create_the_file(self):
        self.assertTrue(W.write_recommendations(self.RECS))
        raw = json.loads(self.path.read_text())
        self.assertEqual((raw["saved_at"], raw["aliases"], raw["caps"]), (None, {}, {}))

    def test_an_unchanged_rebuild_does_not_touch_the_file(self):
        W.write_recommendations(self.RECS)
        os.utime(self.path, (1000, 1000))
        same_but_newer_stamp = {**self.RECS, "generated_at": "2026-11-17T19:00:00+00:00"}
        self.assertFalse(W.write_recommendations(same_but_newer_stamp))
        self.assertEqual(self.path.stat().st_mtime, 1000)  # file and mtime untouched
        changed = {**self.RECS, "aliases": {}}
        self.assertTrue(W.write_recommendations(changed))

    def test_hand_added_keys_survive_both_kinds_of_write(self):
        self.path.write_text('{"my_notes": "keep me", "aliases": {}}')
        W.save(CFG, {"Coding": {"cats": {"coding": 0.6}}})
        W.write_recommendations(self.RECS)
        self.assertEqual(json.loads(self.path.read_text())["my_notes"], "keep me")

    def test_missing_or_corrupt_file_means_no_overrides(self):
        empty = {"aliases": {}, "caps": {}}
        self.assertEqual(W.load(CFG), empty)
        self.path.write_text("{ this is not json")
        self.assertEqual(W.load(CFG), empty)
        self.path.write_text('{"aliases": {"Coding": {"cats": {"coding": "lots"}}}, "caps": {"Coding": "cheap"}}')
        self.assertEqual(W.load(CFG), empty)  # a hand-edit typo can't break the build


if __name__ == "__main__":
    unittest.main()
