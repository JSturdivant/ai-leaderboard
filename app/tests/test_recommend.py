"""Recommendations: what they contain, and how workspaces keep them current."""

import json
import unittest
from unittest import mock

from modelfit import db, picks, plans, recommend, tenants
from tests import helpers


class RecommendationContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = helpers.snapshot()
        cls.recs = recommend.build(cls.snap, cls.snap["alias_picks"])
        cls.by = {m["id"]: m for m in cls.snap["models"]}

    def test_every_alias_is_covered_and_picks_are_the_shortlist(self):
        self.assertEqual(set(self.recs["aliases"]), set(self.snap["alias_picks"]))
        for alias, r in self.recs["aliases"].items():
            self.assertEqual([p["model"] for p in r["picks"]], self.snap["alias_picks"][alias]["shortlist"])
            self.assertEqual([p["rank"] for p in r["picks"]], list(range(1, len(r["picks"]) + 1)))

    def test_each_pick_carries_what_an_agent_needs_to_use_it(self):
        pick = self.recs["aliases"]["Cheap"]["picks"][0]
        m = self.by[pick["model"]]
        self.assertEqual((pick["name"], pick["fit"], pick["released"]), (m["name"], m["fit"]["Cheap"], m["released"]))
        low = m["cost"]["lowest"]
        self.assertEqual(pick["cost"], {"blended": low["blended"], "input": low["in"], "output": low["out"],
                                        "channel": low["channel"], "provider": low["provider"]})

    def test_freshness_and_basis_are_stated(self):
        self.assertEqual(set(self.recs["data_as_of"]), {"openrouter", "epoch"})
        self.assertEqual(self.recs["generated_at"], self.snap["generated_at"])
        self.assertEqual(self.recs["cost_basis"], {"input": 3, "output": 1})

    def test_it_is_plain_json(self):
        self.assertEqual(json.loads(json.dumps(self.recs, allow_nan=False)), self.recs)

    def test_changes_detects_moves_appearances_and_removals(self):
        a = {"aliases": {"X": {"picks": [{"model": "m1"}]}, "Y": {"picks": [{"model": "m2"}]}}}
        b = {"aliases": {"X": {"picks": [{"model": "m3"}]}, "Z": {"picks": [{"model": "m4"}]}}}
        self.assertEqual(recommend.changes(a, b), {"X": ("m1", "m3"), "Z": (None, "m4"), "Y": ("m2", None)})
        self.assertEqual(recommend.changes(a, a), {})


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.env = helpers.Env("cloud")
        db.run("INSERT INTO workspaces (id, name, plan, created_at) VALUES ('ws_a', 'A', 'pro', 0)")
        tenants.init_workspace("ws_a")
        self.ws = db.one("SELECT * FROM workspaces WHERE id='ws_a'")

    def tearDown(self):
        self.env.stop()

    def test_new_workspace_gets_the_starter_aliases_and_recommendations(self):
        data, ver, _ = tenants.get_settings("ws_a")
        self.assertEqual(list(data["alias_defs"]), ["Cheap"])
        recs = tenants.recompute(self.ws)
        self.assertEqual(list(recs["aliases"]), ["Cheap"])
        self.assertEqual(tenants.recommendations("ws_a"), recs)

    def test_tuning_drives_the_stored_picks(self):
        before = tenants.recompute(self.ws)["aliases"]["Cheap"]["picks"]
        tenants.save_tuning(self.ws, {"Cheap": {"cats": {"coding": 0.0, "reasoning": 1.0}}}, {"Cheap": 0.3})
        after = tenants.recommendations("ws_a")["aliases"]["Cheap"]
        self.assertTrue(after["custom_weights"])
        self.assertEqual(after["cap"], 0.3)
        for p in after["picks"]:
            self.assertLessEqual(p["cost"]["blended"], 0.3)
        self.assertNotEqual([(p["model"], p["fit"]) for p in before], [(p["model"], p["fit"]) for p in after["picks"]])

    def test_workspace_scoring_matches_the_build_for_an_untouched_preset(self):
        recs = tenants.recompute(self.ws)
        self.assertEqual([p["model"] for p in recs["aliases"]["Cheap"]["picks"]], self.env.snap["alias_picks"]["Cheap"]["shortlist"])

    def test_free_plan_cannot_save_custom_weights_but_can_save_caps(self):
        db.run("UPDATE workspaces SET plan='free' WHERE id='ws_a'")
        ws = db.one("SELECT * FROM workspaces WHERE id='ws_a'")
        with self.assertRaises(plans.Upgrade):
            tenants.save_tuning(ws, {"Cheap": {"cats": {"coding": 1}}})
        tenants.save_tuning(ws, {}, {"Cheap": 0.2})
        self.assertEqual(tenants.get_settings("ws_a")[0]["caps"], {"Cheap": 0.2})

    def test_add_remove_aliases_and_the_plan_limit(self):
        tenants.add_alias(self.ws, None, preset="Free")
        tenants.add_alias(self.ws, "Summarizer", {"weights": {"coding": 1}, "max_cost": 0.5})
        self.assertEqual(list(tenants.get_settings("ws_a")[0]["alias_defs"]), ["Cheap", "Free", "Summarizer"])
        with self.assertRaises(ValueError):
            tenants.add_alias(self.ws, "Summarizer", {"weights": {"coding": 1}})
        tenants.remove_alias(self.ws, "Summarizer")
        self.assertNotIn("Summarizer", tenants.recommendations("ws_a")["aliases"])
        db.run("UPDATE workspaces SET plan='free' WHERE id='ws_a'")
        ws = db.one("SELECT * FROM workspaces WHERE id='ws_a'")
        tenants.add_alias(ws, "Two", {"weights": {"coding": 1}})
        tenants.add_alias(ws, "Three", {"weights": {"coding": 1}})  # 4 = the Free limit
        with self.assertRaises(plans.Upgrade):
            tenants.add_alias(ws, "Five", {"weights": {"coding": 1}})

    def test_pick_changes_are_logged_and_sent_to_the_webhook(self):
        tenants.recompute(self.ws)
        data, _, _ = tenants.get_settings("ws_a")
        data["webhook"] = {"url": "https://hooks.example.com/x", "secret": "whsec_test"}
        tenants._put("ws_a", data)
        with mock.patch.object(tenants, "send_webhook") as send, mock.patch.object(tenants.threading, "Thread") as T:
            T.side_effect = lambda target, args, daemon, kwargs=None: mock.Mock(start=lambda: target(*args, **(kwargs or {})))
            tenants.save_tuning(self.ws, {}, {"Cheap": 0})  # a $0 cap forces a different #1 (and route)
        hist = tenants.history(self.ws)
        self.assertEqual(sorted((h["alias"], h["kind"]) for h in hist), [("Cheap", "pick"), ("Cheap", "route")])
        self.assertTrue(all(h["from_model"] == "acme/alpha-1" for h in hist))
        self.assertEqual(send.call_count, 2)
        events = sorted(c.kwargs.get("event", "picks.changed") for c in send.call_args_list)
        self.assertEqual(events, ["picks.changed", "route.changed"])
        for c in send.call_args_list:
            self.assertEqual((c.args[1], c.args[2]["Cheap"][0]), ("ws_a", "acme/alpha-1"))

    def test_a_quiet_recompute_changes_nothing(self):
        tenants.recompute(self.ws)
        t0 = db.one("SELECT updated_at FROM ws_recommendations WHERE workspace_id='ws_a'")["updated_at"]
        tenants.recompute(self.ws)
        self.assertEqual(db.one("SELECT updated_at FROM ws_recommendations WHERE workspace_id='ws_a'")["updated_at"], t0)

    def test_on_snapshot_rescores_every_workspace(self):
        db.run("INSERT INTO workspaces (id, name, plan, created_at) VALUES ('ws_b', 'B', 'free', 0)")
        tenants.init_workspace("ws_b")
        tenants.on_snapshot(self.env.snap)
        self.assertIsNotNone(tenants.recommendations("ws_a"))
        self.assertIsNotNone(tenants.recommendations("ws_b"))

    def test_webhook_signature_is_verifiable(self):
        sig = tenants.sign("whsec_x", "1700000000", b'{"a":1}')
        import hashlib, hmac
        self.assertEqual(sig, "v1=" + hmac.new(b"whsec_x", b'1700000000.{"a":1}', hashlib.sha256).hexdigest())


if __name__ == "__main__":
    unittest.main()
