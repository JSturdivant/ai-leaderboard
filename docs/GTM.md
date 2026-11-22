# Go-to-market: freemium

## Positioning

**Modelfit tells every model slot in your product which LLM to use, keeps that answer current, and routes your
agents' traffic to it through one OpenAI-compatible endpoint.**

* **For:** engineers and AI platform teams running LLMs in production (routers, gateways, agent frameworks) who
  maintain several "model slots" (a cheap classifier, a daily chat model, a coding agent, a reasoning planner).
* **Pain:** new models ship weekly, prices move daily, and benchmarks live in five places with five scales. Teams
  over-pay on habit or spend hours re-evaluating by hand.
* **Alternative today:** spreadsheets, Artificial Analysis / LMArena tabs, OpenRouter's catalogue, gut feel.
* **Why us:** one honest, explained score per slot (no fuzzy matching, missing data never counted as zero),
  live prices across every channel, and an API + webhook so routing follows the data automatically.

## Funnel

| Stage | Surface | Goal / metric |
|---|---|---|
| Discover | public landing page with live "today's top picks", SEO, shared matrix links, HN/Reddit/X launches | visits |
| Try (no account) | `/app` as a guest: full matrix, leaderboard, methodology, preview tuning | guest → signup rate |
| Activate | sign up → 3 starter aliases (Daily, Cheap, Coding) → connect an OpenRouter key → first gateway call | **activation = first successful gateway (or `/v1/routes`) call within 24 h** |
| Depend | agents call aliases instead of model ids; routes switch for them | gateway requests per workspace per week |
| Habit | verdict line ("3 options are cheaper and better, 88% cheaper"), pick history | weekly active workspaces |
| Convert | gates at moments of intent (below) | free → paid ≥ 3–5% |
| Expand | Team seats, more API volume, Enterprise | net revenue retention |

## Plans

| | Free | Pro $19/mo ($190/yr) | Team $79/mo ($790/yr, 5 seats) | Enterprise |
|---|---|---|---|---|
| Matrix, leaderboard, methodology | ✓ | ✓ | ✓ | ✓ |
| Aliases | 4 | 25 | 100 | custom |
| Routing gateway (OpenAI-compatible, BYOK) | 100 req/day | 25k req/day | 250k req/day | custom |
| Fallback models per alias | 1 | 3 | 5 | custom |
| Pinned routes & switch thresholds | — | ✓ | ✓ | ✓ |
| Custom fit weights (save) | preview only | ✓ | ✓ | ✓ |
| Webhooks on #1 change | — | ✓ | ✓ | ✓ |
| API requests / day | 200 | 10,000 | 100,000 | custom |
| API keys | 1 | 5 | 25 | custom |
| CSV export | — | ✓ | ✓ | ✓ |
| Pick history | 7 days | 90 days | 1 year | custom |
| Shared workspace, roles | — | — | ✓ | ✓ + SSO, audit |

Limits live in `app/modelfit/plans.py`; prices map to Stripe price ids in the environment.

**Routing is the retention engine.** Once agents call `model="Coding"` instead of a model id, Modelfit is in the
request path: switching away means re-pinning every service. Gateway volume is also the clearest value metric, so
it is what the plans meter. BYOK keeps us out of token resale (no COGS, no credit risk) and makes the
"zero markup" claim true; a managed-credits option can come later for teams that want one invoice.

**Rationale.** The free tier is generous with *insight* (everyone sees the full market and their top picks) and
meters *automation and customisation*, which is where business value and willingness to pay sit. $19 is an
impulse, card-on-file price for an individual engineer; one switched alias typically saves more than that in a
week of traffic, which is the core pricing-page claim. Team is priced on shared model policy, not per-seat
extraction (5 seats included).

## Upgrade moments (built into the product)

1. **Gateway quota (100/day on Free)** → 429 with `upgrade: pro` the day an agent goes to production.
2. **Pinning a route or changing the switch threshold, a 2nd fallback** → Pro.
3. **Dragging a weight slider on Free** → instant preview, "saving custom weights is a Pro feature" → upgrade dialog.
4. **Adding a 5th alias** → 402 → upgrade dialog with Pro recommended.
5. **Webhook / CSV / second API key** → lock notes with an inline Upgrade button.
6. **API 429** at the daily limit → response carries `upgrade: pro`.
7. **Team features** (adding a member) → Team recommended.

Every gate is enforced server-side (HTTP 402 with `upgrade` and `feature`) and drawn client-side from
`/api/plans`, so the UI can never drift from what the server allows.

## Product-qualified leads

Flag a workspace as a PQL when any of: ≥ 3 aliases with a current model set; API key used on ≥ 3 days in a week;
≥ 150 API calls/day on Free; ≥ 2 sign-ins from the same company domain. The `events` and `usage` tables carry
everything needed; pipe them to the CRM weekly.

## Channels

* **Content/SEO:** weekly "model market report" generated from pick history (what changed, who got cheaper);
  per-model and per-use-case pages ("best coding model under $1/M").
* **Developer:** API docs, copy-paste router snippets (LiteLLM, OpenRouter, Vercel AI SDK), a GitHub Action that
  fails CI when a pinned model is dominated.
* **Community:** share links to matrix views; launch on HN / Product Hunt; data-source attribution kept visible.
* **Self-host:** the open single-user mode builds trust and is the top of funnel for teams who later want hosted
  sharing, webhooks and SLAs.

## Metrics to watch

Visitor → signup, signup → activation (24 h), weekly active workspaces, free → paid, time-to-upgrade, API calls
per paid workspace, logo churn, NRR. Track via `events` (`signup`, `login`, `alias.added`, `key.created`,
`checkout.started`, `plan.changed`, `webhook.sent`).

## Open decisions

* Open-source the self-host mode (trust, distribution) or keep it source-available?
* Usage-based overage for API volume vs. hard daily caps.
* Data licensing: Epoch and LMArena data are CC-BY 4.0 (attribution shown everywhere); confirm commercial terms
  for each source before launch, and add an "about our data" page.
* Domain and trademark search for the name "Modelfit".
