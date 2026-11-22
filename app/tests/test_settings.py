"""Environment settings, plans and auth primitives."""

import tempfile
import unittest
from pathlib import Path

from modelfit import auth, billing, plans, settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "x.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_are_selfhost_with_a_persisted_dev_key(self):
        s = settings.load({"MODELFIT_DB": self.db})
        self.assertEqual((s.mode, s.cloud, s.dev_billing), ("selfhost", False, False))
        self.assertEqual(settings.load({"MODELFIT_DB": self.db}).secret_key, s.secret_key)

    def test_cloud_development_switches_plans_without_stripe(self):
        s = settings.load({"MODELFIT_DB": self.db, "MODELFIT_MODE": "cloud"})
        self.assertTrue(s.dev_billing)
        s = settings.load({"MODELFIT_DB": self.db, "MODELFIT_MODE": "cloud", "STRIPE_SECRET_KEY": "sk_test"})
        self.assertFalse(s.dev_billing)

    def test_production_refuses_insecure_settings(self):
        with self.assertRaises(SystemExit):
            settings.load({"MODELFIT_DB": self.db, "MODELFIT_MODE": "cloud", "MODELFIT_ENV": "production"})
        s = settings.load({"MODELFIT_DB": self.db, "MODELFIT_MODE": "cloud", "MODELFIT_ENV": "production", "MODELFIT_SECRET_KEY": "k" * 40,
                           "MODELFIT_BASE_URL": "https://modelfit.ai/", "STRIPE_SECRET_KEY": "sk", "STRIPE_WEBHOOK_SECRET": "wh",
                           "STRIPE_PRICE_PRO_YEARLY": "price_y"})
        self.assertFalse(s.dev_billing)
        self.assertTrue(s.secure_cookies)
        self.assertIn("modelfit.ai", s.allowed_hosts)
        self.assertEqual(s.price_for("pro", "year"), "price_y")


class PlanTests(unittest.TestCase):
    def ws(self, plan, status="active", seats=1):
        return {"plan": plan, "plan_status": status, "seats": seats}

    def test_entitlements(self):
        self.assertFalse(plans.allows(self.ws("free"), "custom_weights"))
        self.assertTrue(plans.allows(self.ws("pro"), "webhooks"))
        self.assertFalse(plans.allows(self.ws("pro"), "team"))
        self.assertIsNone(plans.limit(self.ws("selfhost"), "aliases"))

    def test_lapsed_subscription_falls_back_to_free(self):
        self.assertEqual(plans.effective_plan(self.ws("pro", "canceled")), "free")
        self.assertEqual(plans.effective_plan(self.ws("pro", "past_due")), "pro")

    def test_limits_name_the_plan_to_upgrade_to(self):
        with self.assertRaises(plans.Upgrade) as cm:
            plans.check_limit(self.ws("free"), "aliases", 4)
        self.assertEqual(cm.exception.plan, "pro")
        with self.assertRaises(plans.Upgrade) as cm:
            plans.require(self.ws("pro"), "team")
        self.assertEqual(cm.exception.plan, "team")
        self.assertEqual(plans.limit(self.ws("team", seats=12), "members"), 12)

    def test_public_table_hides_internal_plans(self):
        self.assertEqual(set(plans.public()), {"free", "pro", "team"})


class AuthPrimitiveTests(unittest.TestCase):
    def test_password_hashing(self):
        h = auth.hash_password("correct horse")
        self.assertTrue(h.startswith("scrypt$"))
        self.assertTrue(auth.check_password("correct horse", h))
        self.assertFalse(auth.check_password("wrong", h))
        self.assertFalse(auth.check_password("x", "garbage"))

    def test_stripe_signature_verification(self):
        import hashlib, hmac
        body = b'{"id":"evt"}'
        sig = hmac.new(b"sec", b"1000." + body, hashlib.sha256).hexdigest()
        self.assertTrue(billing.verify(body, f"t=1000,v1=bad,v1={sig}", "sec", now=1100))
        self.assertFalse(billing.verify(body, f"t=1000,v1={sig}", "sec", now=2000))   # too old
        self.assertFalse(billing.verify(body, f"t=1000,v1={sig}", "other", now=1100))
        self.assertFalse(billing.verify(body, None, "sec"))

    def test_stripe_form_encoding(self):
        self.assertEqual(billing._flatten({"a": {"b": 1}, "l": [{"x": 2}], "t": True, "n": None}),
                         [("a[b]", 1), ("l[0][x]", 2), ("t", "true")])


if __name__ == "__main__":
    unittest.main()
