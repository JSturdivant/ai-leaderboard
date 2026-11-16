import unittest

from leaderboard.names import NameIndex, parse_benchmark_name, slug, strip_date


class SlugTests(unittest.TestCase):
    def test_sources_converge_on_one_slug(self):
        # the same model as spelled by OpenRouter, Epoch, Arena, ECI and LiteLLM
        names = ["anthropic/claude-opus-5.5", "claude-opus-5-5_max", "Claude Opus 5.5 (Max)", "Claude Opus 5.5", "claude-opus-5-5"]
        self.assertEqual({parse_benchmark_name(n)[0] if n[0].isupper() or "_" in n else slug(n) for n in names}, {"claude-opus-5-5"})

    def test_variants_and_vendor_prefix_are_dropped(self):
        self.assertEqual(slug("meta-llama/llama-3.3-70b-instruct:free"), "llama-3-3-70b-instruct")
        self.assertEqual(slug("gemini/gemini-4-carbon"), "gemini-4-carbon")

    def test_strip_date(self):
        for raw, want in [("x-20260423", "x"), ("x-2026-03-05", "x"), ("x-260628", "x"), ("x-0813", "x"), ("x-02-23", "x"),
                          ("claude-fable-5-2", "claude-fable-5-2"), ("grok-4-20", "grok-4-20"), ("gpt-6-1-sol", "gpt-6-1-sol")]:
            self.assertEqual(strip_date(raw), want, raw)


class ParseTests(unittest.TestCase):
    def test_effort_suffixes(self):
        self.assertEqual(parse_benchmark_name("claude-fable-5-2_max"), ("claude-fable-5-2", "max", None))
        self.assertEqual(parse_benchmark_name("Gemini 4 Carbon (High)"), ("gemini-4-carbon", "high", None))
        self.assertEqual(parse_benchmark_name("Claude Opus 4.5 (High, 32K, 20251101)"), ("claude-opus-4-5", "high", None))
        self.assertEqual(parse_benchmark_name("claude-opus-4-6_32K")[0], "claude-opus-4-6")

    def test_max_in_the_name_is_not_an_effort(self):
        self.assertEqual(parse_benchmark_name("Qwen3.8-Max"), ("qwen3-8-max", None, None))
        self.assertEqual(parse_benchmark_name("qwen3.7-max")[1], None)

    def test_named_variant_stays_distinct(self):
        self.assertEqual(parse_benchmark_name("Kimi K2 (Thinking)"), ("kimi-k2", None, "thinking"))


class IndexTests(unittest.TestCase):
    def build(self, names, overrides=None):
        idx = NameIndex(overrides)
        for i, n in enumerate(names):
            idx.add(n, {"value": i})
        return idx

    def test_exact_beats_family(self):
        idx = self.build(["qwen3.8-max_xhigh", "qwen3.8-max-0902"])
        recs, how = idx.lookup(["qwen3-8-max-0902"])
        self.assertEqual((how, [r["label"] for r in recs]), ("exact", ["qwen3.8-max-0902"]))

    def test_snapshot_falls_back_to_family(self):
        recs, how = self.build(["deepseek-v4-pro_max"]).lookup(["deepseek-v4-pro-0813"])
        self.assertEqual((how, len(recs)), ("family", 1))

    def test_effort_levels_collect_under_one_key(self):
        recs, _ = self.build(["gpt-6-astra_max", "gpt-6-astra_high"]).lookup(["gpt-6-astra"])
        self.assertEqual(len(recs), 2)

    def test_named_variant_does_not_leak_into_base_model(self):
        idx = self.build(["Kimi K2 (Thinking)"])
        self.assertEqual(idx.lookup(["kimi-k2"])[0], [])
        self.assertEqual(len(idx.lookup(["kimi-k2-thinking"])[0]), 1)

    def test_no_fuzzy_matching(self):
        # 'muse-spark' (v1) must not be attached to 'muse-spark-1-3'
        self.assertEqual(self.build(["muse-spark"]).lookup(["muse-spark-1-3"])[0], [])

    def test_override(self):
        idx = self.build(["My Weird Name"], overrides={"My Weird Name": "acme/real-model"})
        self.assertEqual(len(idx.lookup(["real-model"])[0]), 1)


if __name__ == "__main__":
    unittest.main()
