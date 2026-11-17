"""Epoch AI Benchmarking Hub: one CSV bundle covering many benchmarks plus the
Epoch Capabilities Index (ECI). Licence: CC-BY-4.0, credit "Epoch AI" (https://epoch.ai/benchmarks).
"""

import csv
import io
import time
import zipfile

from ..util import fetch as http_get, log, num

NAME = "epoch"
LABEL = "Epoch AI Benchmarking Hub"
URL = "https://epoch.ai/data/benchmark_data.zip"

# metric key -> (csv file, score column, multiplier to percent)
FILES = {
    "swe_bench_verified": ("swe_bench_verified.csv", "Best score (across scorers)", 100),
    "gpqa_diamond": ("gpqa_diamond.csv", "Best score (across scorers)", 100),
    "hle": ("hle_external.csv", "Accuracy", 100),
    "arc_agi_2": ("arc_agi_2_external.csv", "Score", 100),
    "frontiermath": ("frontiermath_tiers_1_3_v2.csv", "Best score (across scorers)", 100),
    "aider_polyglot": ("aider_polyglot_external.csv", "Percent correct", 1),
    "scicode": ("scicode_external.csv", "Score", 100),
    "terminal_bench": ("terminalbench_external.csv", "Accuracy mean", 100),
    "apex_agents": ("apex_agents_external.csv", "Pass@1 score", 100),
}
ECI_FILE = "epoch_capabilities_index/eci_scores.csv"


def _rows(z, name):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))


def fetch(cfg, prev=None):
    z = zipfile.ZipFile(io.BytesIO(http_get(URL, timeout=120)))
    records = []
    for metric, (fname, col, mult) in FILES.items():
        try:
            for r in _rows(z, fname):
                v = num(r.get(col))
                name = (r.get("Model version") or r.get("Name") or "").strip()
                if v is None or not name:
                    continue
                records.append(
                    {
                        "metric": metric,
                        "name": name,
                        "value": v * mult,
                        "date": r.get("Release date") or None,
                        "agent": (r.get("Agent") or "").strip() or None,
                    }
                )
        except KeyError:
            log.warning("epoch: %s missing from bundle", fname)
    try:
        for r in _rows(z, ECI_FILE):
            v = num(r.get("eci"))
            name = (r.get("Model") or "").strip()
            if v is not None and name:
                records.append({"metric": "epoch_eci", "name": name, "value": v, "date": r.get("date") or None, "agent": None})
    except KeyError:
        log.warning("epoch: ECI file missing from bundle")
    return {"fetched_at": time.time(), "records": records}


def count(raw):
    return len(raw.get("records", []))
