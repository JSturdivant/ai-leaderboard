"""Model routing: sticky routes, pinning, fallbacks, request filtering, credentials, and the gateway end to end."""

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from modelfit import db, plans, router, secretbox, settings, tenants
from tests import helpers
from tests.test_server import Client, ServerCase


def row(mid, tools=True, ctx=128000, gateway=True):
    chans = {"openrouter": [{"in": 1.0, "out": 4.0, "provider": "X", "note": None}]} if gateway else {"nous": [{"in": 1.0, "out": 4.0}]}
    return {"id": mid, "base": mid.split(":")[0], "name": mid, "tools": tools, "ctx": ctx, "reasoning": False,
            "cost": {"lowest": {"blended": 1.75, "in": 1.0, "out": 4.0, "channel": "openrouter", "provider": "X"}, "channels": chans}}


class ChooseTests(unittest.TestCase):
    BY = {m: row(m) for m in ("a/top", "a/old", "a/third")}
    CFG = dict(router.DEFAULTS)

    def test_first_route_is_the_top_pick(self):
        self.assertEqual(router._choose(self.CFG, ["a/top", "a/old"], {"a/top": 80, "a/old": 70}, {}, self.BY), ("a/top", "top pick"))

    def test_hysteresis_holds_a_route_until_the_gain_is_worth_a_switch(self):
        model, why = router._choose(self.CFG, ["a/top", "a/old"], {"a/top": 80.5, "a/old": 80.0}, {"model": "a/old"}, self.BY)
        self.assertEqual(model, "a/old")
        self.assertIn("holding", why)
        model, why = router._choose(self.CFG, ["a/top", "a/old"], {"a/top": 81.5, "a/old": 80.0}, {"model": "a/old"}, self.BY)
        self.assertEqual((model, why), ("a/top", "switched to the new top pick"))

    def test_a_route_that_is_no_longer_eligible_switches_immediately(self):
        model, _ = router._choose(self.CFG, ["a/top", "a/third"], {"a/top": 80, "a/third": 79.9}, {"model": "a/old"}, self.BY)
        self.assertEqual(model, "a/top")

    def test_pinned_routes_and_their_safety_net(self):
        cfg = {**self.CFG, "mode": "pinned", "pinned": "a/third"}
        self.assertEqual(router._choose(cfg, ["a/top"], {"a/top": 80}, {}, self.BY), ("a/third", "pinned"))
        model, why = router._choose({**cfg, "pinned": "gone/model"}, ["a/top"], {"a/top": 80}, {}, self.BY)
        self.assertEqual(model, "a/top")
        self.assertIn("not purchasable", why)

    def test_no_candidates(self):
        self.assertEqual(router._choose(self.CFG, [], {}, {}, self.BY)[0], None)


class ChainTests(unittest.TestCase):
    BY = {"m/route": row("m/route", tools=False, ctx=32000), "m/fb1": row("m/fb1"), "m/fb2": row("m/fb2", gateway=False),
          "m/spare": row("m/spare", ctx=1_000_000)}
    ROUTE = {"model": "m/route", "fallbacks": ["m/fb1", "m/fb2"], "pool": ["m/fb1", "m/fb2", "m/spare"]}

    def test_plain_request_uses_route_then_fallbacks(self):
        self.assertEqual(router.serving_chain(self.ROUTE, self.BY, {}, gateway_only=False), ["m/route", "m/fb1", "m/fb2"])

    def test_gateway_skips_models_it_cannot_buy_and_tops_up_from_the_pool(self):
        self.assertEqual(router.serving_chain(self.ROUTE, self.BY, {}), ["m/route", "m/fb1", "m/spare"])

    def test_tool_calls_and_long_prompts_filter_the_chain(self):
        self.assertEqual(router.serving_chain(self.ROUTE, self.BY, {"tools": True}), ["m/fb1", "m/spare"])
        self.assertEqual(router.serving_chain(self.ROUTE, self.BY, {"context": 500_000}), ["m/spare"])

    def test_request_needs(self):
        self.assertEqual(router.request_needs({"messages": [{"role": "user", "content": "hi"}]}), {"tools": False, "context": 0})
        big = {"messages": [{"role": "user", "content": [{"type": "text", "text": "x" * 40000}]}], "tools": [{}], "max_tokens": 1000}
        self.assertEqual(router.request_needs(big), {"tools": True, "context": 11000})


class ConfigTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(router.parse_config({}), router.DEFAULTS)
        self.assertEqual(router.parse_config({"mode": "pinned", "pinned": "a/b", "min_gain": 3, "fallbacks": 4})["pinned"], "a/b")
        for bad in ({"mode": "x"}, {"mode": "pinned"}, {"min_gain": -1}, {"min_gain": 50}, {"fallbacks": 9}, {"fallbacks": True}, []):
            with self.assertRaises(ValueError, msg=bad):
                router.parse_config(bad)

    def test_plan_gates(self):
        free, pro = {"plan": "free", "plan_status": "active"}, {"plan": "pro", "plan_status": "active"}
        with self.assertRaises(plans.Upgrade):
            router.parse_config({"mode": "pinned", "pinned": "a/b"}, free)
        with self.assertRaises(plans.Upgrade):
            router.parse_config({"fallbacks": 2}, free)
        router.parse_config({"mode": "pinned", "pinned": "a/b", "fallbacks": 3}, pro)
        # a downgrade clamps stored controls instead of breaking the route
        eff = router.effective_config(free, {"mode": "pinned", "pinned": "a/b", "min_gain": 5, "fallbacks": 3})
        self.assertEqual((eff["mode"], eff["min_gain"], eff["fallbacks"]), ("auto", 1.0, 1))


class SecretboxTests(unittest.TestCase):
    def setUp(self):
        self.prev = settings._current
        settings.set_current(settings.load({"MODELFIT_SECRET_KEY": "k" * 40, "MODELFIT_DB": "/tmp/unused-secretbox.db"}))

    def tearDown(self):
        settings.set_current(self.prev)

    def test_round_trip_tamper_and_key_rotation(self):
        sealed = secretbox.seal("sk-or-v1-abcdef")
        self.assertNotIn("abcdef", sealed)
        self.assertEqual(secretbox.open_(sealed), "sk-or-v1-abcdef")
        self.assertNotEqual(secretbox.seal("sk-or-v1-abcdef"), sealed)  # fresh nonce
        flipped = sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1]
        self.assertIsNone(secretbox.open_(flipped))
        settings.set_current(settings.load({"MODELFIT_SECRET_KEY": "j" * 40, "MODELFIT_DB": "/tmp/unused-secretbox.db"}))
        self.assertIsNone(secretbox.open_(sealed))
        self.assertIsNone(secretbox.open_("garbage"))


class RouteStateTests(unittest.TestCase):
    def setUp(self):
        self.env = helpers.Env("cloud")
        db.run("INSERT INTO workspaces (id, name, plan, created_at) VALUES ('ws_r', 'R', 'pro', 0)")
        tenants.init_workspace("ws_r")
        self.ws = db.one("SELECT * FROM workspaces WHERE id='ws_r'")

    def tearDown(self):
        self.env.stop()

    def test_routes_follow_picks_and_record_changes(self):
        tenants.recompute(self.ws)
        r = router.load("ws_r")[0]["Cheap"]
        self.assertEqual((r["model"], r["fallbacks"], r["mode"]), ("acme/alpha-1", ["acme/gamma-2"], "auto"))
        tenants.set_route(self.ws, "Cheap", {"mode": "pinned", "pinned": "acme/gamma-2"})
        r = router.load("ws_r")[0]["Cheap"]
        self.assertEqual((r["model"], r["reason"], r["fallbacks"]), ("acme/gamma-2", "pinned", ["acme/alpha-1"]))
        hist = [h for h in tenants.history(self.ws) if h["kind"] == "route"]
        self.assertEqual((hist[0]["from_model"], hist[0]["to_model"]), ("acme/alpha-1", "acme/gamma-2"))

    def test_resolve_and_request_constraints(self):
        tenants.recompute(self.ws)
        out = router.resolve(self.ws, "cheap", self.env.snap)
        self.assertEqual((out["alias"], out["model"], [f["model"] for f in out["fallbacks"]]), ("Cheap", "acme/alpha-1", ["acme/gamma-2"]))
        self.assertTrue(out["details"]["gateway"])
        with self.assertRaises(KeyError):
            router.resolve(self.ws, "Nope", self.env.snap)

    def test_provider_keys_are_sealed_at_rest(self):
        router.set_provider_key("ws_r", "openrouter", "sk-or-v1-" + "x" * 30)
        raw = db.one("SELECT sealed, hint FROM provider_keys WHERE workspace_id='ws_r'")
        self.assertNotIn("sk-or", raw["sealed"])
        self.assertEqual(raw["hint"], "xxxx")
        self.assertEqual(router.provider_key("ws_r"), "sk-or-v1-" + "x" * 30)
        with self.assertRaises(ValueError):
            router.set_provider_key("ws_r", "openrouter", "short")
        router.delete_provider_key("ws_r", "openrouter")
        self.assertIsNone(router.provider_key("ws_r"))


# ── the gateway, against a fake OpenAI-compatible upstream ───────────────────────────
class FakeUpstream(BaseHTTPRequestHandler):
    fail = set()          # model ids that answer 503
    reject = set()        # model ids that answer 400 (a client error: must not fall back)
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeUpstream.calls.append({"model": body["model"], "auth": self.headers.get("Authorization"), "provider": body.get("provider")})
        if body["model"] in self.fail:
            return self._send(503, {"error": {"message": "overloaded"}})
        if body["model"] in self.reject:
            return self._send(400, {"error": {"message": "bad request"}})
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in ({"choices": [{"delta": {"content": "Hel"}}]}, {"choices": [{"delta": {"content": "lo"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}):
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._send(200, {"id": "c1", "object": "chat.completion", "model": body["model"],
                         "choices": [{"message": {"role": "assistant", "content": "hi"}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}})

    def _send(self, status, obj):
        b = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class GatewayTests(ServerCase):
    MODE = "cloud"

    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()
        cls.ENV = {"MODELFIT_UPSTREAM_URL": f"http://127.0.0.1:{cls.up.server_address[1]}"}
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.up.shutdown()
        cls.up.server_close()

    def setUp(self):
        super().setUp()
        FakeUpstream.fail, FakeUpstream.reject, FakeUpstream.calls = set(), set(), []
        self.c.call("POST", "/api/auth/signup", {"email": f"g{time.time_ns()}@example.com", "password": "correcthorse"})
        self.key = self.c.call("POST", "/api/keys", {"name": "agent"})[1]["key"]
        self.agent = Client(self.port)

    def chat(self, body, headers=None):
        # what an OpenAI SDK sends: a bearer key and JSON, no CSRF header, no cookie
        return self.agent.call("POST", "/v1/chat/completions", body, headers={"Authorization": f"Bearer {self.key}", "X-Requested-With": "", **(headers or {})})

    MSG = {"messages": [{"role": "user", "content": "hello"}]}

    def test_needs_a_provider_key_first(self):
        st, out, _ = self.chat({**self.MSG, "model": "Cheap"})
        self.assertEqual((st, out["error"]["code"]), (400, "no_provider_key"))

    def test_routes_an_alias_and_reports_what_it_did(self):
        self.c.call("PUT", "/api/routing/providers/openrouter", {"key": "sk-or-v1-" + "k" * 30})
        st, out, r = self.chat({**self.MSG, "model": "Cheap"})
        self.assertEqual((st, out["model"], r.getheader("X-Modelfit-Model"), r.getheader("X-Modelfit-Attempts")), (200, "acme/alpha-1", "acme/alpha-1", "1"))
        self.assertEqual(out["modelfit"], {"alias": "Cheap", "model": "acme/alpha-1", "attempts": 1})
        self.assertEqual(FakeUpstream.calls[0]["auth"], "Bearer sk-or-v1-" + "k" * 30)
        self.assertEqual(FakeUpstream.calls[0]["provider"], {"sort": "price"})
        log = self.c.call("GET", "/api/routing")[1]["usage"]
        self.assertEqual((log["recent"][0]["status"], log["recent"][0]["prompt_tokens"]), (200, 1000))
        self.assertGreater(log["recent"][0]["cost_usd"], 0)
        self.assertEqual(self.chat({**self.MSG, "model": "modelfit/cheap"})[0], 200)  # prefix and any case

    def test_fails_over_down_the_route(self):
        self.c.call("PUT", "/api/routing/providers/openrouter", {"key": "sk-or-v1-" + "k" * 30})
        FakeUpstream.fail = {"acme/alpha-1"}
        st, out, r = self.chat({**self.MSG, "model": "Cheap"})
        self.assertEqual((st, out["model"], r.getheader("X-Modelfit-Attempts")), (200, "acme/gamma-2", "2"))
        FakeUpstream.fail = {"acme/alpha-1", "acme/gamma-2"}
        st, out, _ = self.chat({**self.MSG, "model": "Cheap"})
        self.assertEqual((st, out["error"]["code"]), (502, "route_exhausted"))

    def test_client_errors_are_relayed_not_retried(self):
        self.c.call("PUT", "/api/routing/providers/openrouter", {"key": "sk-or-v1-" + "k" * 30})
        FakeUpstream.reject = {"acme/alpha-1"}
        st, _, _ = self.chat({**self.MSG, "model": "Cheap"})
        self.assertEqual((st, [c["model"] for c in FakeUpstream.calls]), (400, ["acme/alpha-1"]))

    def test_streaming_passes_through(self):
        self.c.call("PUT", "/api/routing/providers/openrouter", {"key": "sk-or-v1-" + "k" * 30})
        st, raw, r = self.chat({**self.MSG, "model": "Cheap", "stream": True})
        self.assertEqual((st, r.getheader("Content-Type")), (200, "text/event-stream"))
        self.assertIn(b"data: [DONE]", raw)
        self.assertEqual(raw.count(b"data: "), 3)
        recent = self.c.call("GET", "/api/routing")[1]["usage"]["recent"][0]
        self.assertEqual((recent["stream"], recent["completion_tokens"]), (1, 2))

    def test_unknown_models_and_bad_bodies(self):
        self.c.call("PUT", "/api/routing/providers/openrouter", {"key": "sk-or-v1-" + "k" * 30})
        self.assertEqual(self.chat({**self.MSG, "model": "Nope"})[1]["error"]["code"], "model_not_found")
        self.assertEqual(self.chat({"model": "Cheap"})[0], 400)
        st, out, _ = self.chat({**self.MSG, "model": "acme/gamma-2"})  # a concrete id passes through
        self.assertEqual((st, out["model"]), (200, "acme/gamma-2"))
        self.assertEqual(self.agent.call("POST", "/v1/chat/completions", {**self.MSG, "model": "Cheap"}, headers={"X-Requested-With": ""})[0], 401)

    def test_quota(self):
        self.c.call("PUT", "/api/routing/providers/openrouter", {"key": "sk-or-v1-" + "k" * 30})
        ws = self.c.call("GET", "/api/me")[1]["workspace"]["id"]
        db.run("INSERT INTO usage (workspace_id, day, metric, count) VALUES (?, ?, 'gateway', 100)", ws, time.strftime("%Y-%m-%d", time.gmtime()))
        st, out, _ = self.chat({**self.MSG, "model": "Cheap"})
        self.assertEqual((st, out["error"]["code"], out["error"]["upgrade"]), (429, "quota_exceeded", "pro"))

    def test_resolve_api_and_openai_model_list(self):
        h = {"Authorization": f"Bearer {self.key}"}
        st, out, r = self.agent.call("GET", "/v1/routes/Cheap?tools=1", headers=h)
        self.assertEqual((st, out["model"], out["ttl_seconds"]), (200, "acme/alpha-1", router.TTL))
        self.assertTrue(r.getheader("ETag"))
        self.assertEqual(self.agent.call("GET", "/v1/routes/Cheap?context=999999999", headers=h)[1]["model"], None)
        st, models, _ = self.agent.call("GET", "/v1/models", headers=h)
        self.assertEqual((models["object"], models["data"][0]["id"], models["data"][0]["modelfit"]["routes_to"]), ("list", "Cheap", "acme/alpha-1"))
        self.assertIn("Cheap", self.agent.call("GET", "/v1/routes", headers=h)[1]["routes"])

    def test_route_controls_are_gated_on_free(self):
        st, out, _ = self.c.call("PUT", "/api/routing/routes/Cheap", {"mode": "pinned", "pinned": "acme/gamma-2"})
        self.assertEqual((st, out["feature"]), (402, "routing_controls"))
        self.c.call("POST", "/api/billing/checkout", {"plan": "pro"})
        st, out, _ = self.c.call("PUT", "/api/routing/routes/Cheap", {"mode": "pinned", "pinned": "acme/gamma-2", "fallbacks": 3})
        self.assertEqual((st, out["routes"]["Cheap"]["model"]), (200, "acme/gamma-2"))


if __name__ == "__main__":
    unittest.main()
