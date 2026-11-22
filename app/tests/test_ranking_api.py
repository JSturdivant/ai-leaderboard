"""The model ranking API: triage (NLP), ranker scoring/filters/validation, model metadata, and the HTTP endpoints."""

import copy
import json
import time
import unittest

from modelfit import modelmeta, ranker, triage
from tests import helpers
from tests.test_server import Client, ServerCase


class TriageTests(unittest.TestCase):
    CASES = [
        ("Fix this: Traceback (most recent call last): File app.py line 3 KeyError: 'user'", "debugging", "coding"),
        ("Write a Python function that merges two sorted lists", "code_generation", "coding"),
        ("Prove that there are infinitely many primes", "math", "reasoning"),
        ("Summarize this meeting transcript in five bullets", "summarization", "chat"),
        ("Translate this paragraph into Japanese", "translation", "chat"),
        ("Write a poem about autumn leaves", "creative", "chat"),
        ("Classify each support ticket as billing or technical and return JSON", "extraction", "chat"),
        ("Use the browser tool to find flights and book the cheapest one", "agentic", "agentic"),
        ("write a sql query: select customers grouped by region with total revenue", "data_analysis", None),
        ("请帮我总结这篇文章的要点", "summarization", "chat"),
    ]

    def test_typical_requests_are_classified(self):
        for text, task_type, category in self.CASES:
            r = triage.classify(prompt=text)
            self.assertEqual(r["task_types"][0]["type"], task_type, text)
            if category:
                self.assertEqual(r["category"], category, text)
            self.assertAlmostEqual(sum(r["dimensions"].values()), 1.0, places=2)

    def test_task_description_and_conversation_are_used(self):
        self.assertEqual(triage.classify(task="customer support chatbot for a bank")["task_types"][0]["type"], "conversation")
        msgs = [{"role": "system", "content": "You are helpful."}, {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "Hello!"}, {"role": "user", "content": "Now prove that sqrt 2 is irrational"}]
        self.assertEqual(triage.classify(messages=msgs)["category"], "reasoning")

    def test_needs_are_detected(self):
        r = triage.classify(messages=[{"role": "user", "content": [{"type": "text", "text": "what is in this picture?"},
                                                                    {"type": "image_url", "image_url": {"url": "data:"}}]}])
        self.assertTrue(r["needs"]["vision"])
        r = triage.classify(prompt="look this up", tools=[{"type": "function", "function": {"name": "search"}}])
        self.assertTrue(r["needs"]["tools"])
        self.assertEqual(r["category"], "agentic")
        self.assertTrue(triage.classify(prompt="x " * 140000)["needs"]["long_context"])

    def test_complexity_language_and_tokens(self):
        easy = triage.classify(prompt="hi, what's the capital of France?")
        hard = triage.classify(prompt="Design a rigorous, production architecture for a multi-region system. Prove the consistency "
                                      "guarantees step by step, consider edge cases and trade-offs, and optimize for scale. " * 4)
        self.assertEqual(easy["complexity"], "low")
        self.assertEqual(hard["complexity"], "high")
        self.assertEqual(triage.classify(prompt="¿Cuál es la capital de Francia y por qué es tan famosa?")["language"]["code"], "es")
        self.assertEqual(triage.classify(prompt="Bonjour, pouvez-vous m'aider avec ce document pour le client ?")["language"]["code"], "fr")
        self.assertGreater(triage.classify(prompt="word " * 400)["estimated_input_tokens"], 400)

    def test_empty_input_is_low_confidence(self):
        r = triage.classify()
        self.assertEqual(r["confidence"], 0.0)
        self.assertTrue(r["empty"])


class MetaTests(unittest.TestCase):
    def row(self, mid, name, out=1.0, reasoning=False):
        return {"id": mid, "name": name, "reasoning": reasoning, "cost": {"lowest": {"out": out}}}

    def test_open_weights(self):
        self.assertTrue(modelmeta.open_weights(self.row("x/foo", "Foo"), "org/Foo"))
        self.assertTrue(modelmeta.open_weights(self.row("meta-llama/llama-3.1-8b-instruct", "Llama 3.1 8B")))
        self.assertFalse(modelmeta.open_weights(self.row("openai/gpt-6", "GPT-6")))
        self.assertFalse(modelmeta.open_weights(self.row("qwen/qwen-max", "Qwen Max")))

    def test_params_and_latency_estimates(self):
        self.assertEqual(modelmeta.active_params_b("qwen3-235b-a22b"), 22)
        self.assertEqual(modelmeta.active_params_b("llama-3.1-8b"), 8)
        small = modelmeta.latency(self.row("a/tiny-mini", "Tiny Mini", out=0.1))
        big = modelmeta.latency(self.row("a/big-pro", "Big Pro", out=30, reasoning=True))
        self.assertEqual(small["basis"], "estimated")
        self.assertGreater(small["index"], big["index"])
        measured = modelmeta.latency(self.row("a/b", "B"), [{"latency": 400, "throughput": 120}])
        self.assertEqual((measured["basis"], measured["ttft_s"], measured["tokens_per_s"]), ("measured", 0.4, 120))

    def test_reliability(self):
        self.assertEqual(modelmeta.reliability([]), (None, 0))
        score, n = modelmeta.reliability([{"status": 0, "in": 1, "uptime": 100, "provider": p} for p in "abcde"])
        self.assertEqual((score, n), (100.0, 5))
        low, _ = modelmeta.reliability([{"status": 0, "in": 1, "uptime": 96, "provider": "a"}])
        self.assertLess(low, 30)


def snapshot():
    """The fixture snapshot with a few metadata facts set so filters have something to bite on."""
    snap = copy.deepcopy(helpers.snapshot())
    facts = {"acme/alpha-1": ("US", False, 30.0), "acme/gamma-2": ("FR", True, 70.0), "acme/delta-3": ("CN", True, 50.0),
             "acme/beta-free:free": (None, True, 80.0)}
    for m in snap["models"]:
        c, ow, lat = facts.get(m["id"], (None, False, 50.0))
        m.update(country=c, open_weights=ow, latency={"basis": "estimated", "ttft_s": 1.0, "tokens_per_s": 50, "index": lat},
                 reliability=90.0)
        for d in ("agentic", "chat"):  # fixture only has coding + reasoning categories
            m["scores"].pop(d, None)
    return snap


class RankerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = snapshot()

    def rank(self, body):
        return ranker.rank(self.snap, ranker.parse(body))

    def test_basic_shape_and_ordering(self):
        r = self.rank({"prompt": "write a function to sort a list", "weights": {"performance": 1}})
        self.assertEqual(r["object"], "ranking")
        scores = [x["score"] for x in r["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(r["results"][0]["model"], "acme/alpha-1")  # best coding score in the fixture
        self.assertEqual([x["rank"] for x in r["results"]], list(range(1, len(r["results"]) + 1)))
        top = r["results"][0]
        self.assertEqual(top["score"], top["factors"]["performance"])
        self.assertTrue(top["explanation"])

    def test_savings_weight_prefers_cheaper_models(self):
        quality = self.rank({"weights": {"performance": 1}, "filters": {"free": False}})["results"][0]["model"]
        cheap = self.rank({"weights": {"savings": 1}, "filters": {"free": False}})["results"][0]
        self.assertEqual(quality, "acme/alpha-1")
        self.assertEqual(cheap["factors"]["savings"], 100.0)
        self.assertNotEqual(cheap["model"], quality)

    def test_score_is_the_weighted_mean_of_factors(self):
        r = self.rank({"weights": {"performance": 3, "latency": 1}})
        for x in r["results"]:
            want = 0.75 * x["factors"]["performance"] + 0.25 * x["factors"]["latency"]
            self.assertAlmostEqual(x["score"], round(want, 2), places=1)
        self.assertEqual(r["weights"], {"performance": 0.75, "latency": 0.25})

    def test_dimension_weights_become_the_performance_mix(self):
        r = self.rank({"weights": {"reasoning": 2, "savings": 2}})
        self.assertEqual(r["weights"], {"savings": 0.5, "performance": 0.5})
        self.assertEqual(r["performance_mix"], {"reasoning": 1.0})
        self.assertEqual(r["basis"]["performance_mix"], "request")

    def test_filters(self):
        def ids(body):
            return {x["model"] for x in self.rank({"weights": {"performance": 1}, **body})["results"]}
        self.assertEqual(ids({"filters": {"countries": ["US"]}}), {"acme/alpha-1"})
        self.assertNotIn("acme/alpha-1", ids({"filters": {"exclude_countries": ["us"]}}))
        self.assertTrue(all(i != "acme/alpha-1" for i in ids({"filters": {"open_weights": True}})))
        self.assertEqual(ids({"filters": {"open_weights": False}}), {"acme/alpha-1"})
        self.assertEqual(ids({"filters": {"models": ["acme/gamma-2"]}}), {"acme/gamma-2"})
        r = self.rank({"filters": {"countries": ["JP"]}})
        self.assertEqual(r["results"], [])
        self.assertIn("country_filter", r["excluded"])

    def test_cost_limits(self):
        r = self.rank({"weights": {"performance": 1}, "limits": {"max_blended_cost": 0.5}})
        for x in r["results"]:
            self.assertLessEqual(x["cost"]["blended_per_m"], 0.5)
        self.assertIn("over_cost_limit", r["excluded"])
        r = self.rank({"weights": {"performance": 1}, "limits": {"max_cost_per_request": 0}})
        self.assertTrue(all(x["cost"]["estimated_request_usd"] == 0 for x in r["results"]))

    def test_request_cost_uses_token_estimates(self):
        a = self.rank({"weights": {"performance": 1}, "input_tokens": 1000, "expected_output_tokens": 1000, "filters": {"models": ["acme/alpha-1"]}})
        b = self.rank({"weights": {"performance": 1}, "input_tokens": 1000, "expected_output_tokens": 3000, "filters": {"models": ["acme/alpha-1"]}})
        self.assertGreater(b["results"][0]["cost"]["estimated_request_usd"], a["results"][0]["cost"]["estimated_request_usd"])
        self.assertEqual(a["assumptions"]["output_tokens"], 1000)

    def test_tools_in_the_request_require_tool_use(self):
        r = self.rank({"prompt": "go", "tools": [{"type": "function", "function": {"name": "x"}}]})
        self.assertTrue(r["assumptions"]["requires_tools"])
        self.assertTrue(all(x["tools"] for x in r["results"]))

    def test_auto_weights_follow_triage_and_apply_a_quality_floor(self):
        r = self.rank({"prompt": "write a python function to sort a list"})  # the fixture only scores coding/reasoning
        self.assertEqual(r["basis"]["weights"], "auto")
        self.assertIsNotNone(r["assumptions"]["auto_quality_floor"])
        self.assertTrue(all(x["factors"]["performance"] >= r["assumptions"]["auto_quality_floor"] for x in r["results"]))
        explicit = self.rank({"prompt": "write a python function", "weights": {"savings": 1, "performance": 1}})
        self.assertIsNone(explicit["assumptions"]["auto_quality_floor"])

    def test_validation_errors_name_the_field(self):
        for body, field in (({"weights": {"speedy": 1}}, "weights.speedy"), ({"weights": {"savings": -1}}, "weights.savings"),
                            ({"weights": {"savings": 0}}, "weights"), ({"filters": {"nope": 1}}, "filters.nope"),
                            ({"filters": {"open_weights": "yes"}}, "filters.open_weights"), ({"limit": 0}, "limit"),
                            ({"messages": [{"content": "x"}]}, "messages[0]"), ({"limits": {"max_cost_per_request": -1}}, "limits.max_cost_per_request"),
                            ({"bogus": 1}, "bogus"), ([], "body")):
            with self.assertRaises(ranker.RankError, msg=body) as cm:
                ranker.parse(body)
            self.assertEqual(cm.exception.field, field, body)

    def test_friendly_aliases(self):
        self.assertEqual(ranker.parse({"weights": {"speed": 1, "Cost": 2, "quality": 3}})["weights"], {"latency": 1, "savings": 2, "performance": 3})

    def test_options(self):
        o = ranker.options(self.snap)
        self.assertIn("performance", o["factors"])
        self.assertIn("US", o["countries"])
        self.assertIn("translation", o["task_types"])


class RankApiTests(ServerCase):
    MODE = "cloud"
    ENV = {"MODELFIT_BETA_DAILY": "5"}

    def call(self, path, body=None, key="mf_beta_public", method="POST"):
        hdrs = {"X-Requested-With": "", "Origin": "https://somewhere.example"}  # like a script or SDK: no CSRF header
        if key:
            hdrs["Authorization"] = f"Bearer {key}"
        return Client(self.port).call(method, path, body, headers=hdrs)

    def setUp(self):
        super().setUp()
        from modelfit import db, ratelimit
        ratelimit.BETA._b.clear()
        db.run("DELETE FROM usage WHERE workspace_id LIKE 'beta:%'")

    def test_beta_key_ranks_and_reports_its_limits(self):
        st, out, r = self.call("/v1/rank", {"prompt": "write a sorting function", "limit": 2})
        self.assertEqual((st, out["object"]), (200, "ranking"))
        self.assertLessEqual(len(out["results"]), 2)
        self.assertEqual((r.getheader("X-Modelfit-Beta"), r.getheader("X-RateLimit-Limit"), r.getheader("X-RateLimit-Remaining")), ("true", "5", "4"))

    def test_beta_daily_limit(self):
        codes = [self.call("/v1/triage", {"prompt": "hi"})[0] for _ in range(6)]
        self.assertEqual(codes, [200] * 5 + [429])

    def test_keys_and_validation(self):
        self.assertEqual(self.call("/v1/rank", {}, key=None)[0], 401)
        self.assertEqual(self.call("/v1/rank", {}, key="mf_live_wrong")[0], 401)
        st, out, _ = self.call("/v1/rank", {"weights": {"speedy": 1}})
        self.assertEqual((st, out["field"]), (400, "weights.speedy"))

    def test_workspace_key_counts_against_the_plan(self):
        owner = Client(self.port)
        owner.call("POST", "/api/auth/signup", {"email": f"r{time.time_ns()}@example.com", "password": "correcthorse"},
                   headers={"X-Requested-With": "modelfit"})
        key = owner.call("POST", "/api/keys", {"name": "dev"}, headers={"X-Requested-With": "modelfit"})[1]["key"]
        st, out, r = self.call("/v1/rank", {"task": "summarise support tickets"}, key=key)
        self.assertEqual((st, r.getheader("X-RateLimit-Limit")), (200, "200"))
        self.assertIsNone(r.getheader("X-Modelfit-Beta"))

    def test_triage_and_options_endpoints(self):
        st, out, _ = self.call("/v1/triage", {"prompt": "Translate into French: good morning"})
        self.assertEqual((st, out["object"], out["task_types"][0]["type"]), (200, "triage", "translation"))
        st, out, _ = self.call("/v1/rank/options", method="GET")
        self.assertEqual(st, 200)
        self.assertIn("savings", out["factors"])

    def test_config_exposes_the_beta_key_and_the_page_exists(self):
        self.assertEqual(Client(self.port).call("GET", "/api/config")[1]["beta_key"], "mf_beta_public")
        st, body, _ = Client(self.port).call("GET", "/developers")
        self.assertEqual(st, 200)
        self.assertIn(b"Model Ranking", body)


class RankApiBetaOffTests(ServerCase):
    MODE = "cloud"
    ENV = {"MODELFIT_BETA": "0"}

    def test_beta_key_can_be_disabled(self):
        st, _, _ = Client(self.port).call("POST", "/v1/rank", {}, headers={"Authorization": "Bearer mf_beta_public", "X-Requested-With": ""})
        self.assertEqual(st, 401)
        self.assertIsNone(Client(self.port).call("GET", "/api/config")[1]["beta_key"])


class RankApiSelfhostTests(ServerCase):
    def test_no_key_needed_locally(self):
        st, out, r = Client(self.port).call("POST", "/v1/rank", {"prompt": "hi"}, headers={"X-Requested-With": ""})
        self.assertEqual((st, r.getheader("X-RateLimit-Limit")), (200, "unlimited"))


if __name__ == "__main__":
    unittest.main()
