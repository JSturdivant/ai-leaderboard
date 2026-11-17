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

    def test_empty_save_clears_the_overrides(self):
        self.call("POST", "/api/weights", self.GOOD)
        status, out = self.call("POST", "/api/weights", {"aliases": {}})
        self.assertEqual((status, out["aliases"]), (200, {}))
        self.assertEqual(json.loads(self.path.read_text())["aliases"], {})


if __name__ == "__main__":
    unittest.main()
