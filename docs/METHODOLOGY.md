# Methodology

How Modelfit collects market data and turns it into scores, fits and picks. The same rules run in the
build (Python), in the per-workspace re-scoring (`app/modelfit/picks.py`) and in the browser (`app/web/app.js`),
with the same exactly-rounded arithmetic, so all three agree to the last decimal.

## Where the data comes from

| Need | Source | Key? |
|---|---|---|
| Cost, OpenRouter | `openrouter.ai/api/v1/models` + each model's `/endpoints` (every provider's own price, uptime, quantization) | none |
| Cost, Nous Portal | `inference-api.nousresearch.com/v1/models` (live prices, `:free` tier, promos) | none |
| Cost, primary providers | the model lab's own endpoint on OpenRouter, plus [LiteLLM's price table](https://github.com/BerriAI/litellm) filtered to first-party providers | none |
| Coding / Agentic / Reasoning | Artificial Analysis intelligence, coding and agentic indices (as republished by OpenRouter); Epoch AI Benchmarking Hub (ECI, SWE-bench Verified, Terminal-Bench, GPQA, HLE, ARC-AGI-2, APEX-Agents, …) | none |
| Everyday chat, plus coding/agentic votes | Arena (LMArena) text, instruction-following, multi-turn, coding, WebDev and agent leaderboards via Hugging Face | none |

Epoch data is CC-BY-4.0 (credit Epoch AI); the Arena dataset is CC-BY-4.0.

**Lowest available cost** = the cheapest of: primary-provider list price, OpenRouter's cheapest healthy
endpoint, and Nous Portal. To keep the comparison honest, it ignores batch pricing, priority/flex tiers,
unhealthy endpoints (status ≠ OK or <80% uptime) and *third-party* 4-bit endpoints (benchmarks were run on
full-precision serving). A lab's own endpoint is never excluded. All of that is in `config.json`.

## Keeping it fresh

Each source has its own interval (`config.json` → `refresh.intervals_minutes`): OpenRouter and Nous every
30 min, LiteLLM every 6 h, Arena every 12 h, Epoch daily. While `server.py` runs, anything past its
interval is refreshed automatically, and the **Refresh** button fetches everything now (heavy sources are
skipped if fetched within `min_force_minutes`).

A failed source never blanks the page: the last good copy in `data/raw/` is kept and the status dot turns
red with the error. Edits to `config.json` are picked up within a minute.

No server? Use the CLI from cron/launchd/Task Scheduler:

```bash
python3 refresh.py                 # fetch whatever is past its interval, rebuild app/data/latest.json
python3 refresh.py --force         # fetch everything now
python3 refresh.py --build-only    # no network: rebuild from cached data after editing config.json
python3 refresh.py --report        # print the self-hosted workspace's top 3 per alias
python3 refresh.py --loop          # keep refreshing on the intervals, no web UI
```

Example crontab (daily at 06:10): `10 6 * * * cd /path/to/ai-leaderboard/app && python3 refresh.py`

`app/data/latest.json` is the global market snapshot: every model with its channel prices, benchmark values,
category scores, $/intelligence and the fits of the preset aliases. It is served at `/api/snapshot`; workspaces
re-score from it (see [ARCHITECTURE.md](ARCHITECTURE.md)). Source caches live in `data/raw/`, refresh status in `data/status.json`. Everything under
`data/` is disposable: delete it and the next run rebuilds it.

## How scores are built

1. Each metric is rescaled to 0–100 between its 5th and 99th percentile across currently purchasable
   models, so Elo, % and index scales can be averaged.
2. A category score is the weighted mean of the metrics a model has, only if they carry ≥35% of that
   category's weight. Below 60% it is flagged *thin*.
3. Alias fit = weighted mean of the category scores, using the alias's weights (the workspace's alias definition plus its tuning).
   A missing score is never counted as 0: its weight is spread over the categories the model does have,
   and the model needs ≥60% of the alias's weight (`min_share`, 40% for Free) to be ranked at all.
   The fit dialog shows this exactly, including the points each benchmark adds to a model's fit.
   The browser recomputes fits for custom slider weights with the same rules and the same exactly-rounded
   arithmetic (`math.fsum`), so it matches what the build would produce for the same weights.
4. Benchmarks are joined to models by exact normalized name, then by model family (snapshot date
   stripped; flagged *family* in the detail view). There is no fuzzy matching.
5. Reasoning-effort variants of one model collapse to the best score; Terminal-Bench uses the median
   across agent harnesses so one heavily tuned harness can't inflate a model.

## Limitations

- **Price is per token, not per task.** Reasoning models spend more output tokens, so they cost more than
  their $/M suggests. Treat the leaderboard as a shortlist generator, then check real task cost.
- Artificial Analysis' own API now needs a key (its legacy endpoints were retired Nov 2026), so only the
  indices OpenRouter republishes are used. Speed/latency aren't shown: OpenRouter returns them only to
  authenticated requests.
- Many cheap and free models have little benchmark data; they show *thin* or *no data* rather than a guess.
- LiteLLM is a community-maintained table and can lag a new model by days; OpenRouter's first-party
  endpoint covers most of that gap.
