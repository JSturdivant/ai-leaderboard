# Modelfit

**The right LLM for every job, at the right price.** Modelfit joins live prices from every major provider with
two dozen independent benchmarks, picks the best model for each model slot ("alias") in your product, and routes
your agents' requests to it through one OpenAI-compatible endpoint, keeping the answer current as the market moves.

* **Routing:** point any OpenAI SDK at `/v1` and use an alias as `model`. Each alias has a stable route (switches
  only when a model is clearly better, or pin one), ordered fallbacks that respect tool calls and context length,
  and a request log. Bring your own OpenRouter key: provider prices, no markup. Or resolve routes yourself with
  `GET /v1/routes/{alias}`.
* **Model Ranking API (beta):** `POST /v1/rank` with an optional prompt, conversation or task description, weights
  (performance per dimension, savings, latency, reliability, context, recency), cost limits and filters (country, open
  weights, tools, vision, context…) returns every purchasable model ranked, scored and explained. Requests are
  classified by a built-in NLP triage. Mid-conversation, pass `current_model`: costs are simulated over the next
  turns with prompt caching priced per provider (warm cache on the current model, cold elsewhere), and a
  `switching` block says stay or switch, with break-even turn. Free public beta key `mf_beta_public`; docs and
  playground at `/developers`.
* **Picks:** for each alias (budget + requirements + what matters), the top three fits, always current.
* **Matrix:** every purchasable model on one cost × capability chart with the efficiency frontier.
* **Leaderboard:** $ per 100 points of capability, per use case.
* **Explainable:** every fit shows its arithmetic, benchmark by benchmark.
* **API & webhooks:** `GET /v1/recommendations`; HMAC-signed `route.changed` / `picks.changed` events.

Python 3.9+ standard library only: no `pip install`, no Node, no API keys for the data sources.

## Run it

**Self-hosted** (one user, no sign-in, every feature on):

```bash
cd app
python3 server.py --open          # http://localhost:8787 — first data pass takes ~20 s
```

**Cloud / SaaS mode locally** (accounts, workspaces, plans; upgrades switch instantly without Stripe):

```bash
cd app
MODELFIT_MODE=cloud python3 server.py
# landing page at /, pricing at /pricing, sign up at /signup, the app at /app
```

**Production** (Docker, behind any TLS proxy):

```bash
cp .env.example .env      # fill in MODELFIT_SECRET_KEY, MODELFIT_BASE_URL and the Stripe keys
docker build -t modelfit .
docker run -p 8787:8787 --env-file .env -v modelfit-data:/data modelfit
```

## Repository

| Path | What |
|---|---|
| `app/server.py` | web server: marketing pages, the app, `/api/*`, public `/v1/*` |
| `app/refresh.py` | CLI: refresh market data, rebuild, print picks (`--report`) |
| `app/modelfit/` | data pipeline, scoring, and the SaaS layer (auth, plans, billing, tenants) |
| `app/web/` | landing, pricing, sign-in, API docs, and the app (vanilla JS + vendored Chart.js) |
| `app/config.json` | data sources, benchmark weights, alias presets (no secrets) |
| `app/modelfit/ranker.py`, `triage.py` | the ranking API and the request classifier |
| `docs/` | [architecture](docs/ARCHITECTURE.md), [go-to-market](docs/GTM.md), [brand](docs/BRAND.md), [methodology](docs/METHODOLOGY.md) |

Public API reference: `/docs` on any running instance.

## Plans

Free (4 aliases, 100 gateway requests/day, 1 fallback, preview tuning) · Pro $19/mo (25 aliases, 25k gateway
requests/day, pinning and switch thresholds, 3 fallbacks, custom weights, webhooks, CSV) · Team $79/mo (shared
workspace, 5 seats, 250k gateway requests/day, 5 fallbacks). Limits live in
`app/modelfit/plans.py`. Rationale and funnel: [docs/GTM.md](docs/GTM.md).

## Upgrading from the single-user leaderboard

* The app moved from `local/` to `app/` and the package from `leaderboard` to `modelfit`.
* Your tuning is imported automatically on first run: a `weights.json` in `app/`, `local/` or the repository root is
  read into the self-hosted workspace (stored in `app/var/modelfit.db`). Recommendations are now served at
  `http://localhost:8787/v1/recommendations` (no key needed locally) instead of being written into `weights.json`.
  Self-hosted agents can route through `http://localhost:8787/v1/chat/completions` with `OPENROUTER_API_KEY` set.
* The webhook token is no longer stored in `config.json`. Set `MODELFIT_WEBHOOK_URL` and `MODELFIT_WEBHOOK_SECRET`
  (your old token) before the first run, or set the URL under *Settings → Webhook*. Self-hosted deliveries still
  send `X-Sync-Token` when seeded from a token, plus the new `X-Modelfit-Signature`.
* `current_models` from `config.json` seed the self-hosted workspace; afterwards set them per alias under *Tune*.

## Development

```bash
cd app
python3 -m unittest discover -s tests -t .
```

Data: Artificial Analysis (via OpenRouter), Epoch AI (CC-BY 4.0), LMArena (CC-BY 4.0); prices from OpenRouter,
Nous Portal and LiteLLM. Chart.js (MIT) is vendored in `app/web/vendor/`; Inter (OFL) in `app/web/assets/fonts/`.
