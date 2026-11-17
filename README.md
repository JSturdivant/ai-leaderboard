# LLM Cost vs. Capability — local leaderboard

A leaderboard you run on your own machine. It pulls live pricing and benchmark data from consolidated
sources, saves the latest snapshot to `data/latest.json`, and helps you choose (and sanity-check) the
model behind each agent alias: **Free, Cheap, Daily, Big, Jumbo, Thinking, Coding, Task**.

Python 3.9+ standard library only. No `pip install`, no Node, no API keys.

```bash
python3 server.py --open
```

First run collects data (about 20 seconds) and the page fills in by itself. After that the server keeps
the data fresh in the background; leave it running and the page updates live.

## What you get

- **Cost vs. capability matrix** (top of the page) — one tab per use case (Coding/Engineering, Agentic
  task execution, Reasoning & problem solving, Everyday agent chat), or the fit for a single alias. Older
  models fade out and shrink; hollow dots have thin benchmark coverage; the dashed line is the efficiency
  frontier; vertical lines mark your alias cost caps; ◇ marks the models you entered in the alias cards.
  Optional 2×2 grid. **My aliases only** hides everything except the models set for your aliases, each
  labelled with its alias name.
- **Alias picker** — for each alias, the three highest-fit *different* models under that alias's cost cap
  (equal fits go to the cheaper one; a model sold both paid and `:free` is listed once). Set the cap with the slider (free → $0.01 → $50 → no cap, log scale) or type an
  exact value; the two stay in sync. Type the model you use today into an alias card and it tells you its
  rank and whether anything is both cheaper-or-equal and better.
- **Fit explained** — click any *fit* number (or **How is fit calculated?**) for the formula, every
  benchmark with its weight and 0→100 scale, and a table of how each model scored on each benchmark.
- **Fit weights** — every alias card has a collapsible panel with a slider for each dimension and each
  benchmark. The % beside a slider is that item's share of the alias's fit (0 turns it off); the picks,
  matrix and dialog update as you drag. Every change is **saved automatically to `weights.json`** (see
  below), the card is marked *custom*, and **Reset to defaults** returns to the `config.json` weights.
- **Glossary** (bottom of the page) — what each dimension and benchmark means, how to read its scale, its
  source, which dimensions use it, the main caveat, and the key terms (fit, $/100 pts, frontier, thin, …).
- **$ / Intelligence leaderboard** — sortable, filterable table across all four dimensions.
  `$/100 pts = blended $/M tokens ÷ score × 100`: what 100 points of capability would cost at that model's
  efficiency (lower is better).
- Click any dot or row for the full breakdown: every price channel, every benchmark, how each matched.

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
python3 refresh.py                 # fetch whatever is past its interval, rebuild data/latest.json
python3 refresh.py --force         # fetch everything now
python3 refresh.py --build-only    # no network: rebuild from cached data after editing config.json
python3 refresh.py --report        # print each alias's top 3 fits in the terminal
python3 refresh.py --loop          # keep refreshing on the intervals, no web UI
```

Example crontab (daily at 06:10): `10 6 * * * cd /path/to/ai-leaderboard && python3 refresh.py`

`data/latest.json` is the companion data file: every model with its channel prices, benchmark values,
category scores, $/intelligence, alias fits and the per-alias picks. Your agent or scripts can read it
directly. Source caches live in `data/raw/`, refresh status in `data/status.json`. Everything under
`data/` is disposable: delete it and the next run rebuilds it.

## Your saved weights (`weights.json`)

The fit-weight sliders are saved to **`weights.json`** in the project folder, deliberately *outside*
`data/` so clearing the cache can't lose them. Saving is automatic: about half a second after you stop
moving a slider the page sends the change to the local server, which writes the file and rebuilds
`data/latest.json` with the same weights, so the page, `refresh.py --report` and anything reading
`latest.json` all agree. The indicator beside **Alias picker** shows *Saving… / Saved / Not saved*; if a
save fails it retries automatically, and a change made just before closing the tab is still flushed.

- The file is plain JSON and holds only what differs from `config.json`, e.g.
  `{"aliases": {"Coding": {"cats": {"coding": 0.6}, "metrics": {"coding": {"swe_bench_verified": 5}}}}}`
  (`cats` = dimension weights, `metrics` = benchmark weights per dimension, `0` = off). Edit it by hand if
  you like (invalid entries are ignored, never fatal); changes reach the picks on the next rebuild and the
  sliders on the next page load. Delete it to reset every alias.
- It is yours: commit it to keep it in git, copy it to carry your tuning to another machine.
- Weights an earlier version kept in the browser are moved into this file the first time you open the page.
- Cost caps and the model boxes on the alias cards are still browser-only settings.
- Use another location with the `LEADERBOARD_WEIGHTS` environment variable.

## Tuning (`config.json`)

- `aliases` — cost cap (`max_cost`, `null` = no cap), requirements (`free`, `tools`, `reasoning`),
  `min_context`, and how much each use case counts toward that alias's fit.
  Caps can also be edited live on each card (kept in your browser).
  To set different *default* benchmark weights for one alias, add `metric_weights` (only the benchmarks you
  want to change; `0` switches one off), e.g.
  `"Coding": { "weights": {"coding": 0.6, "agentic": 0.3, "reasoning": 0.1},
  "metric_weights": {"coding": {"swe_bench_verified": 5, "arena_webdev": 0}} }`,
  then `python3 refresh.py --build-only`. Slider changes are layered on top of these defaults and saved
  separately in `weights.json`, so config defaults and your own tuning never overwrite each other.
- `current_models` — the model you run for each alias today, e.g. `"Daily": "z-ai/glm-5.3-flash"`.
  The boxes on the alias cards override this (kept in your browser). Both feed the **My aliases only**
  matrix view.
- `categories` — which benchmarks feed each use case, and their weights.
- `cost.blend` — input:output weighting for "blended" cost (default 3:1). The page can also switch to
  input-only or output-only, which is useful for cache-heavy agent loops.
- `name_overrides` — map a benchmark's label to a model when automatic matching can't
  (the *Sources* section lists strong benchmark entries that matched nothing).

### Assumptions I made for you

- **Daily** had no cap in your description; I used **$0.50/M**. Thinking, Coding and Task have no cap
  (they rank purely by fit). Edit in `config.json`.
- Your caps (<$0.10, <$1, <$2) are applied to **blended 3:1** $/M tokens.
- Coding and Task additionally require tool use and ≥128k context; Thinking requires reasoning support.

## How scores are built

1. Each metric is rescaled to 0–100 between its 5th and 99th percentile across currently purchasable
   models, so Elo, % and index scales can be averaged.
2. A category score is the weighted mean of the metrics a model has, only if they carry ≥35% of that
   category's weight. Below 60% it is flagged *thin*.
3. Alias fit = weighted mean of the category scores, using the alias's weights (`aliases.<name>.weights`).
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

## Development

```bash
python3 -m unittest discover -s tests -t .
```

Layout: `leaderboard/sources/` one collector per source · `names.py` model-name matching ·
`scoring.py` pure maths · `build.py` joins everything into `data/latest.json` · `server.py` + `web/` UI.
Chart.js is vendored in `web/vendor/`, so the page works offline.
