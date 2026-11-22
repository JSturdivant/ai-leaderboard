import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from modelfit import scheduler


class NeedsRebuildTests(unittest.TestCase):
    """The scheduler rebuilds when config.json is newer than data/latest.json."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.latest, self.config = d / "latest.json", d / "config.json"
        for p in (self.latest, self.config):
            p.write_text("{}")
        for target, value in (("LATEST", self.latest), ("CONFIG_PATH", self.config)):
            patcher = mock.patch.object(scheduler, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.set_times(latest=100, config=50)

    def tearDown(self):
        self.tmp.cleanup()

    def set_times(self, **t):
        for name, ts in t.items():
            os.utime(getattr(self, name), (ts, ts))

    def test_up_to_date_means_no_rebuild(self):
        self.assertFalse(scheduler.needs_rebuild(False))

    def test_fetch_or_missing_output_always_rebuilds(self):
        self.assertTrue(scheduler.needs_rebuild(True))
        self.latest.unlink()
        self.assertTrue(scheduler.needs_rebuild(False))

    def test_newer_config_rebuilds(self):
        self.set_times(config=200)
        self.assertTrue(scheduler.needs_rebuild(False))


if __name__ == "__main__":
    unittest.main()
