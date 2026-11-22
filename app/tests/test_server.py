"""The HTTP layer end to end: guards, accounts, workspace API, plan gates, API keys, /v1, billing webhook."""

import hashlib
import hmac
import http.client
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

import server
from modelfit import db, ratelimit
from tests import helpers


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def call(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        hdrs = {"X-Requested-With": "modelfit", "Content-Type": "application/json", "Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if self.cookie:
            hdrs["Cookie"] = self.cookie
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        sc = resp.getheader("Set-Cookie")
        if sc:
            self.cookie = sc.split(";")[0] if "Max-Age=0" not in sc else None
        conn.close()
        out = json.loads(payload) if payload[:1] in (b"{", b"[") else payload
        return resp.status, out, resp


class ServerCase(unittest.TestCase):
    MODE = "selfhost"
    ENV = {}

    @classmethod
    def setUpClass(cls):
        cls.env = helpers.Env(cls.MODE, cls.ENV)
        cls.cfg_patch = mock.patch.object(server, "load_config", return_value=cls.env.cfg)
        cls.cfg_patch.start()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        server.Handler.allowed_hosts = {f"127.0.0.1:{cls.port}"}
        server.Handler.scheduler = None
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.cfg_patch.stop()
        cls.env.stop()

    def setUp(self):
        self.c = Client(self.port)
        ratelimit.AUTH._b.clear()


class SelfhostTests(ServerCase):
    def test_pages_and_redirects(self):
        st, _, r = self.c.call("GET", "/")
        self.assertEqual((st, r.getheader("Location")), (302, "/app"))
        self.assertEqual(self.c.call("GET", "/app")[0], 200)
        self.assertEqual(self.c.call("GET", "/nope")[0], 404)
        self.assertEqual(self.c.call("GET", "/api/nope")[0], 404)
        st, _, r = self.c.call("GET", "/app")
        self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"))

    def test_no_sign_in_and_every_feature(self):
        st, me, _ = self.c.call("GET", "/api/me")
        self.assertEqual((st, me["authenticated"], me["workspace"]["effective_plan"]), (200, True, "selfhost"))
        self.assertTrue(me["workspace"]["entitlements"]["features"]["custom_weights"])
        st, ws, _ = self.c.call("GET", "/api/workspace")
        self.assertEqual(list(ws["alias_defs"]), ["Cheap", "Free"])  # every preset

    def test_tuning_saves_validates_and_rescored(self):
        good = {"weights": {"Cheap": {"cats": {"coding": 1.0}}}, "caps": {"Cheap": 0.5, "Free": None}}
        st, out, _ = self.c.call("PUT", "/api/workspace/tuning", good)
        self.assertEqual((st, out["weights"], out["caps"]), (200, good["weights"], good["caps"]))
        self.assertTrue(out["recommendations"]["aliases"]["Cheap"]["custom_weights"])
        for bad in ({"weights": {"Nope": {"cats": {"coding": 1}}}}, {"weights": {"Cheap": {"cats": {"coding": -1}}}}, {"nothing": 1}):
            self.assertEqual(self.c.call("PUT", "/api/workspace/tuning", bad)[0], 400, bad)
        self.assertEqual(self.c.call("GET", "/api/workspace")[1]["weights"], good["weights"])

    def test_body_limits(self):
        self.assertEqual(self.c.call("PUT", "/api/workspace/tuning", raw="{nope")[0], 400)
        self.assertEqual(self.c.call("PUT", "/api/workspace/tuning", raw=json.dumps({"weights": {}, "pad": "x" * 70000}))[0], 413)

    def test_csrf_and_host_guards(self):
        body = {"weights": {}}
        self.assertEqual(self.c.call("PUT", "/api/workspace/tuning", body, headers={"X-Requested-With": "nope"})[0], 403)
        self.assertEqual(self.c.call("PUT", "/api/workspace/tuning", body, headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.c.call("PUT", "/api/workspace/tuning", body, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.c.call("GET", "/api/workspace", headers={"Host": "evil.example"})[0], 403)

    def test_version_tracks_changes(self):
        v1 = self.c.call("GET", "/api/workspace/version")[1]["version"]
        self.c.call("PUT", "/api/workspace/current", {"alias": "Cheap", "model": "acme/gamma-2"})
        self.assertGreater(self.c.call("GET", "/api/workspace/version")[1]["version"], v1)
        recs = self.c.call("GET", "/v1/recommendations")[1]
        self.assertEqual(recs["aliases"]["Cheap"]["current"]["id"], "acme/gamma-2")

    def test_v1_works_without_a_key_locally(self):
        st, out, r = self.c.call("GET", "/v1/recommendations/Cheap")
        self.assertEqual((st, out["alias"]), (200, "Cheap"))
        self.assertEqual(r.getheader("X-RateLimit-Limit"), "unlimited")
        self.assertEqual(self.c.call("GET", "/v1/recommendations/Nope")[0], 404)
        st, models, _ = self.c.call("GET", "/v1/market?q=alpha&limit=5")
        self.assertTrue(all("alpha" in m["id"] for m in models["models"]))

    def test_accounts_and_billing_are_cloud_only(self):
        self.assertEqual(self.c.call("POST", "/api/auth/signup", {"email": "a@b.co", "password": "x" * 10})[0], 404)
        self.assertEqual(self.c.call("POST", "/api/billing/checkout", {"plan": "pro"})[0], 404)


class CloudTests(ServerCase):
    MODE = "cloud"
    ENV = {"STRIPE_WEBHOOK_SECRET": "whsec_stripe", "STRIPE_PRICE_PRO_MONTHLY": "price_pro_m"}

    def signup(self, email=None):
        email = email or f"u{time.time_ns()}@example.com"
        st, out, _ = self.c.call("POST", "/api/auth/signup", {"email": email, "password": "correcthorse", "name": "Ada"})
        self.assertEqual(st, 201, out)
        return email

    def test_landing_is_public_and_the_app_works_for_guests(self):
        self.assertEqual(self.c.call("GET", "/")[0], 200)
        st, me, _ = self.c.call("GET", "/api/me")
        self.assertFalse(me["authenticated"])
        st, ws, _ = self.c.call("GET", "/api/workspace")
        self.assertTrue(ws["guest"])
        self.assertEqual(self.c.call("PUT", "/api/workspace/tuning", {"weights": {}})[0], 401)
        self.assertEqual(self.c.call("GET", "/v1/recommendations")[0], 401)

    def test_signup_login_logout(self):
        email = self.signup()
        st, me, _ = self.c.call("GET", "/api/me")
        self.assertEqual((me["authenticated"], me["role"], me["workspace"]["plan"]), (True, "owner", "free"))
        self.assertEqual(list(self.c.call("GET", "/api/workspace")[1]["alias_defs"]), ["Cheap"])
        self.assertEqual(self.c.call("POST", "/api/auth/signup", {"email": email, "password": "correcthorse"})[0], 409)
        self.c.call("POST", "/api/auth/logout", {})
        self.assertFalse(self.c.call("GET", "/api/me")[1]["authenticated"])
        self.assertEqual(self.c.call("POST", "/api/auth/login", {"email": email, "password": "wrong-password"})[0], 401)
        self.assertEqual(self.c.call("POST", "/api/auth/login", {"email": email.upper(), "password": "correcthorse"})[0], 200)
        self.assertTrue(self.c.call("GET", "/api/me")[1]["authenticated"])

    def test_signup_validation_and_rate_limit(self):
        self.assertEqual(self.c.call("POST", "/api/auth/signup", {"email": "nope", "password": "correcthorse"})[0], 400)
        self.assertEqual(self.c.call("POST", "/api/auth/signup", {"email": "a@b.co", "password": "short"})[0], 400)
        codes = [self.c.call("POST", "/api/auth/login", {"email": "x@y.co", "password": "nopenope"})[0] for _ in range(12)]
        self.assertIn(429, codes)

    def test_free_plan_gates_and_dev_upgrade(self):
        self.signup()
        st, out, _ = self.c.call("PUT", "/api/workspace/tuning", {"weights": {"Cheap": {"cats": {"coding": 1}}}})
        self.assertEqual((st, out["upgrade"], out["feature"]), (402, "pro", "custom_weights"))
        self.assertEqual(self.c.call("PUT", "/api/workspace/webhook", {"url": "https://example.com/h"})[0], 402)
        st, out, _ = self.c.call("POST", "/api/billing/checkout", {"plan": "pro"})
        self.assertTrue(out["dev"])
        self.assertEqual(self.c.call("GET", "/api/me")[1]["workspace"]["plan"], "pro")
        self.assertEqual(self.c.call("PUT", "/api/workspace/tuning", {"weights": {"Cheap": {"cats": {"coding": 1}}}})[0], 200)

    def test_api_keys_and_metering(self):
        self.signup()
        st, k, _ = self.c.call("POST", "/api/keys", {"name": "router"})
        self.assertEqual(st, 201)
        self.assertTrue(k["key"].startswith("mf_live_"))
        self.assertEqual(self.c.call("POST", "/api/keys", {"name": "second"})[0], 402)  # Free: 1 key
        listed = self.c.call("GET", "/api/keys")[1]["keys"]
        self.assertNotIn("key", listed[0])
        anon = Client(self.port)
        st, recs, r = anon.call("GET", "/v1/recommendations", headers={"Authorization": f"Bearer {k['key']}"})
        self.assertEqual((st, r.getheader("X-RateLimit-Limit"), r.getheader("X-RateLimit-Remaining")), (200, "200", "199"))
        self.assertIn("Cheap", recs["aliases"])
        self.assertEqual(anon.call("GET", "/v1/recommendations", headers={"Authorization": "Bearer mf_live_wrong"})[0], 401)
        ws = self.c.call("GET", "/api/me")[1]["workspace"]["id"]
        db.run("UPDATE usage SET count=200 WHERE workspace_id=?", ws)
        st, out, _ = anon.call("GET", "/v1/recommendations", headers={"Authorization": f"Bearer {k['key']}"})
        self.assertEqual(st, 429)
        self.c.call("DELETE", f"/api/keys/{k['id']}")
        self.assertEqual(anon.call("GET", "/v1/models", headers={"Authorization": f"Bearer {k['key']}"})[0], 401)

    def test_workspaces_are_isolated(self):
        self.signup()
        self.c.call("PUT", "/api/workspace/tuning", {"weights": {}, "caps": {"Cheap": 0.2}})
        other = Client(self.port)
        other.call("POST", "/api/auth/signup", {"email": f"o{time.time_ns()}@example.com", "password": "correcthorse"})
        self.assertEqual(other.call("GET", "/api/workspace")[1]["caps"], {})
        self.assertEqual(self.c.call("GET", "/api/workspace")[1]["caps"], {"Cheap": 0.2})

    def test_stripe_webhook_signature_and_plan_sync(self):
        self.signup()
        ws = self.c.call("GET", "/api/me")[1]["workspace"]["id"]
        evt = {"id": f"evt_{time.time_ns()}", "type": "customer.subscription.updated", "data": {"object": {
            "id": "sub_1", "customer": "cus_1", "status": "active", "current_period_end": 1900000000, "metadata": {"workspace_id": ws},
            "items": {"data": [{"price": {"id": "price_pro_m"}, "quantity": 1}]}}}}
        body = json.dumps(evt).encode()
        anon = Client(self.port)
        self.assertEqual(anon.call("POST", "/api/billing/webhook", raw=body, headers={"Stripe-Signature": "t=1,v1=bad", "X-Requested-With": ""})[0], 400)
        ts = str(int(time.time()))
        sig = hmac.new(b"whsec_stripe", ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        st, out, _ = anon.call("POST", "/api/billing/webhook", raw=body, headers={"Stripe-Signature": f"t={ts},v1={sig}", "X-Requested-With": ""})
        self.assertEqual((st, out["result"]), (200, "plan pro (active)"))
        self.assertEqual(self.c.call("GET", "/api/me")[1]["workspace"]["plan"], "pro")
        st, out, _ = anon.call("POST", "/api/billing/webhook", raw=body, headers={"Stripe-Signature": f"t={ts},v1={sig}", "X-Requested-With": ""})
        self.assertEqual(out["result"], "duplicate")
        evt2 = {**evt, "id": evt["id"] + "b", "type": "customer.subscription.deleted"}
        body2 = json.dumps(evt2).encode()
        sig2 = hmac.new(b"whsec_stripe", ts.encode() + b"." + body2, hashlib.sha256).hexdigest()
        anon.call("POST", "/api/billing/webhook", raw=body2, headers={"Stripe-Signature": f"t={ts},v1={sig2}", "X-Requested-With": ""})
        self.assertEqual(self.c.call("GET", "/api/me")[1]["workspace"]["plan"], "free")

    def test_team_members(self):
        teammate = self.signup()
        owner = Client(self.port)
        self.c = owner
        self.signup()
        self.assertEqual(owner.call("POST", "/api/members", {"email": teammate})[0], 402)
        owner.call("POST", "/api/billing/checkout", {"plan": "team"})
        self.assertEqual(owner.call("POST", "/api/members", {"email": teammate})[0], 201)
        self.assertEqual(len(owner.call("GET", "/api/members")[1]["members"]), 2)
        self.assertEqual(owner.call("POST", "/api/members", {"email": "nobody@example.com"})[0], 404)


if __name__ == "__main__":
    unittest.main()
