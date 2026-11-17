"""Arena (LMArena) leaderboards, read from the public Hugging Face dataset as JSON.

https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset  (CC-BY-4.0)
The datasets-server `rows` endpoint returns at most 100 rows per call, and the `text`
board is ~11k rows (every category), so pages are fetched in parallel and filtered here.
"""

import concurrent.futures as cf
import time
import urllib.parse

from ..util import fetch_json, log, num

NAME = "arena"
LABEL = "Arena (LMArena) leaderboards"
URL = "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset"
ROWS = "https://datasets-server.huggingface.co/rows"
DATASET = "lmarena-ai/leaderboard-dataset"

# metric key -> (hf config, category, value column)
BOARDS = {
    "arena_text": ("text_style_control", "overall", "rating"),
    "arena_coding": ("text_style_control", "coding", "rating"),
    "arena_hard_prompts": ("text_style_control", "hard_prompts", "rating"),
    "arena_instruction_following": ("text_style_control", "instruction_following", "rating"),
    "arena_multi_turn": ("text_style_control", "multi_turn", "rating"),
    "arena_webdev": ("webdev", "overall", "rating"),
    "arena_agent": ("agent", "overall", "score"),
}


def _page(config, offset):
    q = urllib.parse.urlencode(
        {"dataset": DATASET, "config": config, "split": "latest", "offset": offset, "length": 100}
    )
    # the datasets-server rate-limits bursts (HTTP 429): back off generously rather than fail the whole source
    return fetch_json(f"{ROWS}?{q}", timeout=60, retries=6, backoff=2.5)


def _scan(config):
    first = _page(config, 0)
    total = int(first.get("num_rows_total", 0))
    rows = [r["row"] for r in first.get("rows", [])]
    offsets = list(range(100, total, 100))
    if offsets:
        with cf.ThreadPoolExecutor(3) as pool:
            for page in pool.map(lambda o: _page(config, o), offsets):
                rows.extend(r["row"] for r in page.get("rows", []))
    return rows


def fetch(cfg, prev=None):
    by_config = {}
    for config, _cat, _col in BOARDS.values():
        by_config.setdefault(config, None)
    for config in by_config:
        by_config[config] = _scan(config)
        log.info("arena: %s %d rows", config, len(by_config[config]))
    records = []
    published = {}
    for metric, (config, cat, col) in BOARDS.items():
        for r in by_config[config]:
            if r.get("category") != cat:
                continue
            v = num(r.get(col))
            if v is None:
                continue
            records.append(
                {
                    "metric": metric,
                    "name": r["model_name"],
                    "org": r.get("organization"),
                    "value": v,
                    "votes": num(r.get("vote_count") or r.get("observation_count")),
                    "rank": r.get("rank"),
                    "date": r.get("leaderboard_publish_date"),
                    "cost_per_task": num(r.get("cost_per_task_p50")),
                }
            )
            published[metric] = max(published.get(metric, ""), r.get("leaderboard_publish_date") or "")
    return {"fetched_at": time.time(), "records": records, "published": published}


def count(raw):
    return len(raw.get("records", []))
