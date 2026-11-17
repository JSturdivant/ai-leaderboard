# Vercel version (planned, not built yet)

This folder is reserved for a version of the leaderboard that Vercel can host. Today everything works in
[`../local/`](../local/); nothing here is implemented, so there is nothing to deploy yet.

## Why `local/` can't simply be deployed

The local app leans on things a serverless host does not provide:

| `local/` relies on | On Vercel |
|---|---|
| a writable disk (`data/raw/*.json`, `data/latest.json`, `weights.json`) | functions have no persistent disk |
| a background thread that refreshes sources every 30 min to 24 h | no long-running process; use Vercel Cron instead |
| a long-lived local server with `/api/status`, `/api/refresh`, `/api/weights` | each endpoint becomes a function |
| one user, bound to `127.0.0.1` | a public URL: someone has to decide who may change the weights |

## What a hosted version would need

1. **UI.** The page in `local/web/` is static and already calls only `/data/latest.json` and `/api/*`, so it can
   be reused as is (or shared from one place).
2. **Scheduled refresh.** A Vercel Cron job calling a function that runs the collectors in
   `local/leaderboard/sources/` (plain standard-library Python, so they run as Python functions) and then the
   build. The slowest source takes roughly 10 to 15 seconds, so set the function's `maxDuration` accordingly.
3. **Storage instead of the filesystem.** Somewhere to keep the source caches, `latest.json`, and the saved
   weights and recommendations (Vercel Blob, KV or Edge Config). The data layer reads and writes through a few
   small functions (`store.py`, `weights.py`), which is the seam to swap.
4. **API as functions.** `/api/status`, `/api/refresh` and `/api/weights` re-implemented on top of that storage.
5. **Project setting.** Point the Vercel project's *Root Directory* at `vercel/`.

## Decisions to make before building it

- **Who can change weights?** A public site means anyone could. Options: password or Vercel auth for saving,
  or keep weights per browser and have the hosted page read-only.
- **Where do recommendations go?** Locally they are written into `weights.json`. A hosted copy could expose them
  at a stable JSON URL for the agent to fetch.
- **How fresh must the data be?** Cron frequency drives function usage and the rate limits of the free sources.
- **Share code or copy it?** Importing `local/leaderboard` keeps one implementation; copying lets each version
  evolve independently but means fixes are made twice.
