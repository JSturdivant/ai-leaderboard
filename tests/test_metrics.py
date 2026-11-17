import unittest

from leaderboard.metrics import METRICS, SOURCE_LABELS
from leaderboard.sources import arena


class MetricCatalogueTests(unittest.TestCase):
    def test_every_metric_is_documented_for_the_ui(self):
        for key, m in METRICS.items():
            for field in ("label", "short", "source", "unit", "url", "desc"):
                self.assertTrue(m.get(field), f"{key} is missing {field}")
            self.assertIn(m["source"], SOURCE_LABELS, key)

    def test_arena_boards_are_all_catalogued(self):
        for key in arena.BOARDS:
            self.assertIn(key, METRICS)

    def test_config_only_references_known_metrics(self):
        import json
        from leaderboard import CONFIG_PATH
        cfg = json.loads(CONFIG_PATH.read_text())
        for cat in cfg["categories"].values():
            for k in cat["metrics"]:
                self.assertIn(k, METRICS, k)
        for alias in cfg["aliases"].values():
            for c in alias["weights"]:
                self.assertIn(c, cfg["categories"], c)


if __name__ == "__main__":
    unittest.main()
