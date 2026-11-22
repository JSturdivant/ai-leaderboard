#!/usr/bin/env python3
"""Refresh Modelfit's market data from the command line (cron / launchd / Task Scheduler friendly).

  python3 refresh.py                 fetch sources that are past their interval, then rebuild
  python3 refresh.py --force         fetch everything now
  python3 refresh.py --only openrouter nous
  python3 refresh.py --build-only    no network: rebuild data/latest.json from the cached raw data
                                     (use after editing config.json)
  python3 refresh.py --loop          keep refreshing on the configured intervals (no web server)
  python3 refresh.py --report        print the per-alias picks when done
"""

import argparse
import json
import logging
import sys
import time

from modelfit import LATEST, build, settings, tenants
from modelfit.build import build_and_write
from modelfit.scheduler import run_cycle
from modelfit.sources import SOURCES
from modelfit.store import load_config


def report():
    """Print each alias's top 3 (the self-hosted workspace's aliases and tuning)."""
    data = json.loads(LATEST.read_text())
    recs = tenants.recompute(tenants.selfhost_workspace(), data, notify=False) or {}
    print(f"\nData generated {data['generated_at']}  ({len(data['models'])} models)\n")
    for alias, r in (recs.get("aliases") or {}).items():
        cap = r["cap"]
        cap_s = "free" if cap == 0 else ("no cap" if cap is None else f"< ${cap:g}/M")
        print(f"{alias:9s} [{cap_s}, {r['candidates']} candidates, {r['unscored']} unscored{', custom weights' if r['custom_weights'] else ''}]")
        for p in r["picks"]:
            print(f"    top fit #{p['rank']} {p['model']}  ${p['cost']['blended']:.3f}/M  fit {p['fit']}")
        if not r["picks"]:
            print("    top fit    —")
        cur = r.get("current")
        if cur:
            print(f"    your pick  {cur['id']}: {cur['status']}" + (f", rank {cur.get('rank')}/{cur.get('of')}" if cur.get("rank") else ""))
            if cur.get("better"):
                print("               same-or-lower cost, higher fit: " + ", ".join(cur["better"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="fetch every source now")
    ap.add_argument("--only", nargs="+", choices=sorted(SOURCES), help="fetch just these sources")
    ap.add_argument("--build-only", action="store_true", help="rebuild from cached raw data, no network")
    ap.add_argument("--loop", action="store_true", help="run forever on the configured intervals")
    ap.add_argument("--report", action="store_true", help="print alias picks afterwards")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    cfg = load_config()
    if not settings.get().cloud:
        tenants.selfhost_workspace()
    build.ON_SNAPSHOT.append(tenants.on_snapshot)  # every workspace re-scores against the new data
    if args.build_only:
        build_and_write(cfg)
    elif args.loop:
        while True:
            run_cycle(load_config(), force=args.force)
            args.force = False
            time.sleep(float(load_config()["refresh"].get("check_seconds", 60)))
    else:
        results = run_cycle(cfg, names=args.only, force=args.force or bool(args.only))
        for name, res in results.items():
            print(f"  {name:11s} {res}")
        if any(r == "error" for r in results.values()):
            print("some sources failed; the previous copy (if any) was kept. See data/status.json", file=sys.stderr)
    if args.report:
        report()


if __name__ == "__main__":
    main()
