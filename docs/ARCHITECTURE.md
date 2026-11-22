# Architecture

Modelfit is one Python codebase (standard library only) that runs in two modes:

| Mode | Who | Auth | Plans | Storage |
|---|---|---|---|---|
| `selfhost` (default) | one person on their own machine | none: localhost / allowed hosts only | `selfhost` (every feature, no limits) | SQLite in `app/var/` |
| `cloud` | the hosted SaaS | email + password sessions, API keys | Free / Pro / Team via Stripe | SQLite now, Postgres later |

## The key split: global market data vs. per-workspace scoring

The single-user version rebuilt everything (fetch → join → score → picks) per user and wrote the result into
`weights.json`. That cannot scale to many customers, so the pipeline is now split in two:

```
                 every 30 min – 24 h, once for everyone
 sources/*  ──►  build.py  ──►  app/data/latest.json  (market snapshot: prices, benchmark scores, preset fits)
 (OpenRouter,                         │
  Nous, LiteLLM,                      │  ON_SNAPSHOT hook
  Arena, Epoch)                       ▼
                               tenants.on_snapshot ──► for each workspace: picks.score_workspace()
                                                         └─► ws_recommendations, pick_history, webhook
                 on every save (cheap, milliseconds)
 PUT /api/workspace/tuning ──► tenants.save_tuning ──► picks.score_workspace() for that workspace
```

* **Market snapshot** (`build.py`): expensive (network, name matching, normalisation). Runs once per refresh
  for the whole service. Contains no customer data.
* **Workspace scoring** (`picks.py`): pure arithmetic over the snapshot with the workspace's alias definitions
  and tuning. Milliseconds per workspace, so it runs synchronously on save and for every workspace after each
  new snapshot. The build uses the same function for the preset aliases, and `app.js` mirrors it exactly
  (same `fsum` and Python-style rounding), so the browser preview, the API and the build always agree.

## Modules (`app/modelfit/`)

| Module | Responsibility |
|---|---|
| `sources/`, `names.py`, `metrics.py`, `build.py`, `scoring.py`, `scheduler.py`, `store.py` | market data pipeline (unchanged in spirit from the single-user tool) |
| `picks.py` | alias fit and top-3 picks from a snapshot |
| `recommend.py` | the recommendations payload (`/v1/recommendations`, webhooks) and change detection |
| `weights.py` | validation of tuning overlays and alias definitions; reads a legacy `weights.json` once |
| `settings.py` | environment configuration; refuses insecure settings when `MODELFIT_ENV=production` |
| `db.py` | SQLite (WAL), append-only migrations, usage metering, audit/events |
| `auth.py` | scrypt passwords, HMAC'd session tokens and API keys |
| `plans.py` | **the** source of truth for plans, limits and feature flags |
| `tenants.py` | workspace settings, re-scoring, pick history, signed webhooks (SSRF-guarded), the self-host workspace |
| `billing.py` | Stripe Checkout, customer portal, webhook verification and idempotent plan sync |
| `ratelimit.py` | in-process token buckets (sign-in, API bursts, writes) |

`app/server.py` is a small router on `ThreadingHTTPServer`: pages, `/api/*` (the app), `/v1/*` (public API).

## Data model

```
users ─┬─< members >─┬─ workspaces ── ws_settings (alias_defs, weights, caps, current_models, webhook; version)
       │             │              ├─ ws_recommendations (latest picks JSON)
       └─< sessions ─┘              ├─< pick_history (alias, from, to, at)
                                    ├─< api_keys (hashed, prefix, last_used)
                                    └─< usage (day, metric, count)
events (audit + product analytics)   billing_events (Stripe idempotency)
```

Settings are JSON in one row per workspace with a monotonically increasing `version`; the UI polls the version
(every 4 s) so teammates and other tabs see each other's changes without websockets.

## Security

* Sessions: 256-bit tokens, HttpOnly + SameSite=Lax (+ Secure behind https); only an HMAC is stored.
* CSRF: every write needs `X-Requested-With: modelfit` (cross-site forms cannot send it) and an allowed `Origin`.
* DNS rebinding: requests must carry an allowed `Host`.
* CSP `default-src 'self'` (no third-party scripts, fonts are self-hosted), `frame-ancestors 'none'`, HSTS on https.
* Webhooks: HMAC-SHA256 signed with a per-workspace secret; in the cloud only to public https addresses.
* Stripe: signature + timestamp tolerance, idempotent by event id; the webhook is the only thing that changes plans.
* Login: constant-work password check for unknown emails, per-IP rate limit.
* No secrets in `config.json`: everything sensitive comes from the environment (`.env.example`).

## Running in production

Today (one instance, fine to thousands of workspaces):

```
Internet ─► Caddy / Fly / Render (TLS) ─► docker: python3 server.py (MODELFIT_MODE=cloud) ─► /data volume (SQLite, snapshot)
```

`docker build -t modelfit . && docker run -p 8787:8787 --env-file .env -v modelfit-data:/data modelfit`

The scheduler runs in-process; `/healthz` checks the database and snapshot.

## Scaling path (in order, when needed)

1. **Postgres** instead of SQLite (the SQL is portable; swap `db.py`'s connection and `ON CONFLICT` stays valid).
2. **Separate the refresher**: run `refresh.py --loop` as one worker (or a cron job) and publish the snapshot to
   object storage (S3/R2); web instances read it from there. Start web instances with `--no-schedule`.
3. **Stateless web tier** behind a load balancer: move `ratelimit.py` buckets to Redis.
4. **Queue workspace re-scoring** after each snapshot (it is embarrassingly parallel) once workspace count makes
   the in-process loop slow; also queue webhook deliveries with retries.
5. **CDN** for `/assets`, `/vendor` and the marketing pages.
6. Observability: structured logs, request metrics, Sentry; the `events` table feeds product analytics.

## Not built yet (roadmap)

Email verification, password reset and team invitations by email (need an email provider such as Postmark/SES);
SSO/SAML for Enterprise; per-workspace cost basis; pick-change email alerts; audit-log export; a public, SEO-indexed
per-model page; usage-based overage billing for the API.
