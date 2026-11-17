# LLM cost vs. capability leaderboard

Choose (and sanity-check) the model behind each of your agent aliases from live price and benchmark data.
This repository holds two versions side by side:

| Folder | What it is | Status |
|---|---|---|
| [`local/`](local/) | The app. Runs on your own machine (Python standard library only), refreshes its data in the background, and saves your tuning and its latest recommendations to `local/weights.json`. | Working |
| [`vercel/`](vercel/) | A version that can be hosted on Vercel. | Not built yet; [the plan and open decisions](vercel/README.md) are written down |

Start here:

```bash
cd local
python3 server.py --open
```

Everything in [`local/README.md`](local/README.md) (data sources, scoring, weights, recommendations,
configuration) applies to the local version.
