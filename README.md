# Modelfit

**The right LLM for every job, at the right price.** Modelfit joins live prices from every major provider with
two dozen independent benchmarks, then tells each model slot ("alias") in your product which model to use, over
an API and signed webhooks, and keeps that answer current as the market moves.

* **Picks:** for each alias (budget + requirements + what matters), the top three fits, always current.
* **Matrix:** every purchasable model on one cost × capability chart with the efficiency frontier.
* **Leaderboard:** $ per 100 points of capability, per use case.
* **Explainable:** every fit shows its arithmetic, benchmark by benchmark.
* **API & webhooks:** `GET /v1/recommendations` for your router; an HMAC-signed POST when a #1 pick changes.

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
| `docs/` | [architecture](docs/ARCHITECTURE.md), [go-to-market](docs/GTM.md), [brand](docs/BRAND.md), [methodology](docs/METHODOLOGY.md) |

Public API reference: `/docs` on any running instance.

## Plans

Free (4 aliases, 200 API calls/day, preview tuning) · Pro $19/mo (25 aliases, custom weights, webhooks, 10k
calls/day, CSV) · Team $79/mo (shared workspace, 5 seats, 100k calls/day). Limits live in
`app/modelfit/plans.py`. Rationale and funnel: [docs/GTM.md](docs/GTM.md).

## Upgrading from the single-user leaderboard

* The app moved from `local/` to `app/` and the package from `leaderboard` to `modelfit`.
* Your tuning is imported automatically on first run: a `weights.json` in `app/`, `local/` or the repository root is
  read into the self-hosted workspace (stored in `app/var/modelfit.db`). Recommendations are now served at
  `http://localhost:8787/v1/recommendations` (no key needed locally) instead of being written into `weights.json`.
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
