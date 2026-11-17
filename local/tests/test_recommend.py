"""Recommendations stored in weights.json: what they contain and how the build keeps them current."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leaderboard import build as B
from leaderboard import recommend, weights
from tests.test_build import NOW, RAW, cfg

STATUS = {"openrouter": {"ok": True, "fetched_at": 1763400000.0, "error": None, "records": 3},
          "epoch": {"ok": True, "fetched_at": 1763300000.0, "error": None, "records": 3}}


class RecommendationContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.object(B, "load_raw", side_effect=lambda n: RAW.get(n)), mock.patch.object(B, "load_status", return_value=STATUS), \
                mock.patch.object(B, "load_user_weights", return_value={"aliases": {"Cheap": {"cats": {"coding": 1.0}}}, "caps": {"Cheap": 0.5}}):
            cls.data = B.build(cfg(), now=NOW)
        cls.recs = recommend.build(cls.data)
        cls.by = {m["id"]: m for m in cls.data["models"]}

    def test_every_alias_is_covered_and_picks_are_the_shortlist(self):
        self.assertEqual(set(self.recs["aliases"]), set(self.data["alias_picks"]))
        for alias, r in self.recs["aliases"].items():
            self.assertEqual([p["model"] for p in r["picks"]], self.data["alias_picks"][alias]["shortlist"])
            self.assertEqual([p["rank"] for p in r["picks"]], list(range(1, len(r["picks"]) + 1)))

    def test_each_pick_carries_what_an_agent_needs_to_use_it(self):
        pick = self.recs["aliases"]["Cheap"]["picks"][0]
        m = self.by[pick["model"]]
        self.assertEqual((pick["name"], pick["fit"], pick["released"]), (m["name"], m["fit"]["Cheap"], m["released"]))
        low = m["cost"]["lowest"]
        self.assertEqual(pick["cost"], {"blended": low["blended"], "input": low["in"], "output": low["out"],
                                        "channel": low["channel"], "provider": low["provider"]})

    def test_picks_record_the_cap_tuning_and_basis_they_were_made_under(self):
        cheap, free = self.recs["aliases"]["Cheap"], self.recs["aliases"]["Free"]
        self.assertEqual((cheap["cap"], cheap["custom_weights"]), (0.5, True))      # saved cap beats config.json's 1.0
        self.assertEqual((free["cap"], free["custom_weights"]), (0, False))
        for p in cheap["picks"]:
            self.assertLessEqual(p["cost"]["blended"], 0.5)
        self.assertEqual(self.recs["cost_basis"], {"input": 3, "output": 1})

    def test_freshness_of_the_inputs_is_stated(self):
        self.assertEqual(set(self.recs["data_as_of"]), {"openrouter", "epoch"})
        self.assertTrue(self.recs["data_as_of"]["openrouter"].startswith("2025-11-17T"))
        self.assertEqual(self.recs["generated_at"], self.data["generated_at"])

    def test_it_is_plain_json_with_no_surprises(self):
        again = json.loads(json.dumps(self.recs, allow_nan=False))
        self.assertEqual(again, self.recs)
        self.assertIn("picks[0]", self.recs["note"])


class BuildAndWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.latest, self.wfile = d / "latest.json", d / "weights.json"
        os.environ["LEADERBOARD_WEIGHTS"] = str(self.wfile)
        for patcher in (mock.patch.object(B, "LATEST", self.latest), mock.patch.object(B, "load_raw", side_effect=lambda n: RAW.get(n)),
                        mock.patch.object(B, "load_status", return_value=STATUS)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        os.environ.pop("LEADERBOARD_WEIGHTS", None)
        self.tmp.cleanup()

    def test_a_build_stores_recommendations_before_latest_json_so_latest_is_the_newer_file(self):
        B.build_and_write(cfg())
        self.assertTrue(self.wfile.exists() and self.latest.exists())
        raw = json.loads(self.wfile.read_text())
        self.assertEqual(raw["aliases"], {})  # created even before any weights were saved
        self.assertIn("Cheap", raw["recommendations"]["aliases"])
        self.assertGreaterEqual(self.latest.stat().st_mtime_ns, self.wfile.stat().st_mtime_ns)

    def test_saved_weights_and_caps_drive_the_stored_picks_and_survive_the_write(self):
        weights.save(cfg(), {"Cheap": {"cats": {"coding": 1.0, "reasoning": 0.0}}}, {"Cheap": 0.3})
        B.build_and_write(cfg())
        raw = json.loads(self.wfile.read_text())
        self.assertEqual(raw["aliases"], {"Cheap": {"cats": {"coding": 1.0, "reasoning": 0.0}}})
        self.assertEqual(raw["caps"], {"Cheap": 0.3})
        cheap = raw["recommendations"]["aliases"]["Cheap"]
        self.assertEqual((cheap["cap"], cheap["custom_weights"]), (0.3, True))
        for p in cheap["picks"]:
            self.assertLessEqual(p["cost"]["blended"], 0.3)

    def test_changing_a_weight_changes_the_stored_picks(self):
        B.build_and_write(cfg())
        before = json.loads(self.wfile.read_text())["recommendations"]["aliases"]["Cheap"]["picks"]
        # gamma-2 scores 20 on coding and 0 on reasoning; put all the weight on reasoning and its fit must drop
        weights.save(cfg(), {"Cheap": {"cats": {"coding": 0.0, "reasoning": 1.0}}})
        B.build_and_write(cfg())
        after = json.loads(self.wfile.read_text())["recommendations"]["aliases"]["Cheap"]["picks"]
        self.assertNotEqual([(p["model"], p["fit"]) for p in before], [(p["model"], p["fit"]) for p in after])

    def test_a_quiet_rebuild_leaves_weights_json_alone(self):
        B.build_and_write(cfg())
        os.utime(self.wfile, (1000, 1000))
        B.build_and_write(cfg())  # same inputs, newer generated_at
        self.assertEqual(self.wfile.stat().st_mtime, 1000)

    def test_a_failure_to_store_does_not_stop_the_dashboard_data(self):
        with mock.patch.object(weights, "write_recommendations", side_effect=OSError("read-only file system")):
            B.build_and_write(cfg())
        self.assertTrue(self.latest.exists())


if __name__ == "__main__":
    unittest.main()
