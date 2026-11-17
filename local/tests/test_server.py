"""The weights API: validation, the CSRF/host guard, size limit, and that a save schedules a rebuild."""

import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import server
from leaderboard import weights as weights_mod


class WeightsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "weights.json"
        os.environ["LEADERBOARD_WEIGHTS"] = str(cls.path)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        server.Handler.allowed_hosts = {f"127.0.0.1:{cls.port}"}
        server.Handler.scheduler = None
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        os.environ.pop("LEADERBOARD_WEIGHTS", None)
        cls.tmp.cleanup()

    def setUp(self):
        if self.path.exists():
            self.path.unlink()
        patcher = mock.patch.object(server, "rebuild_soon")
        self.rebuild = patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        hdrs = {"X-Requested-With": "leaderboard", "Content-Type": "application/json", **(headers or {})}
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, (json.loads(payload) if payload[:1] in (b"{", b"[") else payload)

    GOOD = {"aliases": {"Coding": {"cats": {"coding": 0.6}, "metrics": {"coding": {"swe_bench_verified": 5}}}}}

    def test_save_writes_the_file_and_schedules_a_rebuild(self):
        status, out = self.call("POST", "/api/weights", self.GOOD)
        self.assertEqual(status, 200)
        self.assertEqual(out["aliases"], self.GOOD["aliases"])
        self.assertEqual(json.loads(self.path.read_text())["aliases"], self.GOOD["aliases"])
        self.rebuild.assert_called_once()

    def test_get_returns_what_was_saved(self):
        self.call("POST", "/api/weights", self.GOOD)
        status, out = self.call("GET", "/api/weights")
        self.assertEqual((status, out["aliases"], out["file"]), (200, self.GOOD["aliases"], "weights.json"))

    def test_get_with_no_file_is_empty_not_an_error(self):
        status, out = self.call("GET", "/api/weights")
        self.assertEqual((status, out["aliases"]), (200, {}))

    def test_invalid_input_is_rejected_and_nothing_is_written(self):
        for body in ({"aliases": {"Nope": {"cats": {"coding": 1}}}}, {"aliases": {"Coding": {"cats": {"coding": -1}}}}, {"nothing": 1}, [1, 2]):
            status, out = self.call("POST", "/api/weights", body)
            self.assertEqual(status, 400, body)
            self.assertIn("error", out)
        self.assertFalse(self.path.exists())
        self.rebuild.assert_not_called()

    def test_malformed_json_and_oversize_bodies(self):
        self.assertEqual(self.call("POST", "/api/weights", raw="{nope")[0], 400)
        self.assertEqual(self.call("POST", "/api/weights", raw=json.dumps({"aliases": {}, "pad": "x" * 70000}))[0], 413)
        self.assertEqual(self.call("POST", "/api/weights", raw="")[0], 400)

    def test_guards_apply_to_the_weights_api(self):
        self.assertEqual(self.call("POST", "/api/weights", self.GOOD, headers={"X-Requested-With": "nope"})[0], 403)
        self.assertEqual(self.call("POST", "/api/weights", self.GOOD, headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/weights", self.GOOD, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/api/weights", headers={"Host": "evil.example"})[0], 403)
        self.assertFalse(self.path.exists())

    def test_caps_are_saved_returned_and_kept_when_omitted(self):
        status, out = self.call("POST", "/api/weights", {"aliases": {}, "caps": {"Coding": 0.5, "Free": None}})
        self.assertEqual((status, out["caps"]), (200, {"Coding": 0.5, "Free": None}))
        self.call("POST", "/api/weights", self.GOOD)  # no caps key: unchanged
        status, out = self.call("GET", "/api/weights")
        self.assertEqual(out["caps"], {"Coding": 0.5, "Free": None})
        self.assertEqual(json.loads(self.path.read_text())["caps"], {"Coding": 0.5, "Free": None})

    def test_bad_caps_are_rejected_and_change_nothing(self):
        self.call("POST", "/api/weights", {"aliases": {}, "caps": {"Coding": 0.5}})
        before = self.path.read_text()
        for caps in ({"Nope": 1}, {"Coding": -1}, {"Coding": "lots"}, [1]):
            self.assertEqual(self.call("POST", "/api/weights", {"aliases": {}, "caps": caps})[0], 400, caps)
        self.assertEqual(self.path.read_text(), before)

    def test_get_includes_the_stored_recommendations_and_a_save_does_not_drop_them(self):
        recs = {"generated_at": "2026-11-17T18:00:00+00:00", "aliases": {"Coding": {"picks": [{"rank": 1, "model": "a/b"}]}}}
        weights_mod.write_recommendations(recs)
        self.assertEqual(self.call("GET", "/api/weights")[1]["recommendations"], recs)
        self.call("POST", "/api/weights", self.GOOD)
        self.assertEqual(self.call("GET", "/api/weights")[1]["recommendations"], recs)

    def test_version_endpoint_tracks_every_change_and_is_guarded(self):
        self.assertEqual(self.call("GET", "/api/weights/version")[1], {"version": None})   # no file yet
        status, saved = self.call("POST", "/api/weights", self.GOOD)
        v1 = self.call("GET", "/api/weights/version")[1]["version"]
        self.assertEqual(v1, saved["version"])                                              # the save reports it too
        self.assertEqual(self.call("GET", "/api/weights")[1]["version"], v1)
        weights_mod.write_recommendations({"generated_at": "x", "aliases": {"Coding": {"picks": []}}})
        v2 = self.call("GET", "/api/weights/version")[1]["version"]
        self.assertNotEqual(v1, v2)                                                         # a rebuild's write is visible
        self.assertEqual(self.call("GET", "/api/weights/version", headers={"Host": "evil.example"})[0], 403)

    def test_empty_save_clears_the_overrides(self):
        self.call("POST", "/api/weights", self.GOOD)
        status, out = self.call("POST", "/api/weights", {"aliases": {}})
        self.assertEqual((status, out["aliases"]), (200, {}))
        self.assertEqual(json.loads(self.path.read_text())["aliases"], {})


if __name__ == "__main__":
    unittest.main()
