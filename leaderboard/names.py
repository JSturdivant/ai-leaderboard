"""Model-name normalisation, so records from different sources can be joined.

Every source spells models differently:

    OpenRouter  anthropic/claude-opus-5.5            Arena  "Claude Opus 5.5 (Max)"
    Epoch       claude-opus-5-5_max                  ECI    "Claude Opus 5.5"
    LiteLLM     claude-opus-5-5 / gemini/gemini-4-carbon

We reduce each to a slug ("claude-opus-5-5") plus an optional reasoning-effort tag.
Matching is deliberately conservative: exact slug first, then the same slug with a
trailing snapshot date removed. There is no fuzzy matching, because attaching one
model's benchmark to another is worse than leaving it blank.
"""

import re

# Reasoning-effort tags. Benchmarks list one row per effort level of the same model.
EFFORTS = {"max", "xhigh", "high", "medium", "low", "minimal", "unknown", "default"}
_BUDGET = re.compile(r"^\d+k$")  # thinking/context budget tags such as '32k'
_DATE_TAG = re.compile(r"^(20)?\d{6}$")  # '20251101' / '251101' inside a parenthetical

_DATE_SUFFIXES = [
    re.compile(r"-20\d{2}-\d{2}-\d{2}$"),  # -2026-03-05
    re.compile(r"-20\d{6}$"),  # -20260423
    re.compile(r"-\d{6}$"),  # -260628  (YYMMDD)
    re.compile(r"-\d{4}$"),  # -0813 / -2507 (MMDD / YYMM)
    re.compile(r"-\d{2}-\d{2}$"),  # -02-23
]


def slug(name):
    """'anthropic/Claude Opus 5.5:free' -> 'claude-opus-5-5'."""
    s = (name or "").strip().lower()
    s = s.split("/")[-1]
    s = s.split(":")[0]
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def strip_date(s):
    """Remove one trailing snapshot-date suffix ('x-0813' -> 'x')."""
    for rx in _DATE_SUFFIXES:
        t = rx.sub("", s)
        if t != s and t:
            return t
    return s


def parse_benchmark_name(raw):
    """Split a benchmark-source label into (slug, effort, variant).

    'claude-fable-5-2_max'     -> ('claude-fable-5-2', 'max', None)      (Epoch)
    'Gemini 4 Carbon (High)'   -> ('gemini-4-carbon', 'high', None)      (Arena)
    'Kimi K2 (Thinking)'       -> ('kimi-k2', None, 'thinking')
    'Qwen3.8-Max'              -> ('qwen3-8-max', None, None)            ('Max' is part of the name)
    """
    s = (raw or "").strip()
    effort = variant = None
    m = re.search(r"\(([^()]*)\)\s*$", s)
    if m:
        s = s[: m.start()].strip()
        rest = []
        for tok in (t.strip().lower() for t in m.group(1).split(",")):
            if tok in EFFORTS:
                effort = effort or tok
            elif tok and not _BUDGET.match(tok) and not _DATE_TAG.match(tok):
                rest.append(tok)
        variant = re.sub(r"[^a-z0-9]+", "-", "-".join(rest)).strip("-") or None
    elif "_" in s:
        head, _, tail = s.rpartition("_")
        t = tail.strip().lower()
        if t in EFFORTS:
            s, effort = head, t
        elif _BUDGET.match(t):
            s, effort = head, "budget"
    return slug(s), effort, variant


class NameIndex:
    """Index of benchmark records keyed by slug; lookup by candidate slugs."""

    def __init__(self, overrides=None):
        self.exact = {}  # slug -> [record]
        self.nodate = {}  # strip_date(slug) -> [record]
        # user overrides: {"benchmark label": "vendor/openrouter-id"}
        self.overrides = {parse_benchmark_name(k)[0]: slug(v) for k, v in (overrides or {}).items()}

    def add(self, raw_name, record):
        key, effort, variant = parse_benchmark_name(raw_name)
        if not key:
            return
        key = self.overrides.get(key, key)
        record = dict(record, effort=effort, variant=variant, label=raw_name)
        keys = [key]
        if variant:
            # 'Kimi K2 (Thinking)' only counts for 'kimi-k2-thinking', never for plain 'kimi-k2'.
            keys = [f"{key}-{variant}"]
        else:
            # An effort-specific alias lets 'o3-mini-high' style ids match too.
            if effort:
                self.exact.setdefault(f"{key}-{effort}", []).append(record)
        for k in keys:
            self.exact.setdefault(k, []).append(record)
            self.nodate.setdefault(strip_date(k), []).append(record)

    def lookup(self, candidates):
        """Return (records, how) for the first candidate slug that matches."""
        cands = [c for c in dict.fromkeys(candidates) if c]
        for c in cands:
            if c in self.exact:
                return self.exact[c], "exact"
        for c in cands:
            d = strip_date(c)
            if d in self.exact:
                return self.exact[d], "family"
        for c in cands:
            d = strip_date(c)
            if d in self.nodate:
                return self.nodate[d], "family"
        return [], None
