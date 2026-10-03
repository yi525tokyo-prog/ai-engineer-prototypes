"""Command line entry point.

  python -m quiethousing run              full pipeline (crawl -> enrich -> evaluate -> export)
  python -m quiethousing run --no-acquire re-evaluate / enrich from stored data only
  python -m quiethousing report [-n 30]   ranked table of current results
  python -m quiethousing probe LAT LON    environment profile + evaluation for any point
  python -m quiethousing serve            web UI on http://127.0.0.1:8765
  python -m quiethousing init-config      write the default config.json to edit
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import DEFAULTS, save_config
from .pipeline import Context, flat_row, results, run_all


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="quiethousing")
    ap.add_argument("--data", default="data", help="data directory (sqlite, caches, config.json, exports)")
    ap.add_argument("--config", default=None, help="config path (default: <data>/config.json)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--no-acquire", action="store_true", help="skip goodroom crawling")
    rep = sub.add_parser("report")
    rep.add_argument("-n", type=int, default=30)
    rep.add_argument("--decision", default=None)
    pr = sub.add_parser("probe")
    pr.add_argument("lat", type=float)
    pr.add_argument("lon", type=float)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    sub.add_parser("init-config")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "init-config":
        ctx = Context.open(args.data, args.config, offline=True)
        if ctx.config_path.exists():
            print(f"{ctx.config_path} exists; not overwriting")
            return 1
        save_config(ctx.config_path, DEFAULTS)
        print(f"wrote {ctx.config_path}")
        return 0

    ctx = Context.open(args.data, args.config, offline=args.cmd in ("report",))
    ctx.progress = lambda m: print(m, flush=True)
    if args.cmd == "run":
        stats = run_all(ctx, skip_acquire=args.no_acquire)
        print(json.dumps(stats, ensure_ascii=False, indent=1))
    elif args.cmd == "report":
        rows = results(ctx)
        if args.decision:
            rows = [x for x in rows if x["evaluation"]["decision"] == args.decision.upper()]
        for x in rows[: args.n]:
            f = flat_row(x)
            print(f"{f['decision']:7} Q={f['quality'] if f['quality'] is not None else '-':>5}  ¥{(f['rent'] or 0):>7,} {f['layout'] or '':5} {f['floor_area_m2'] or '':>6}㎡  {f['building'] or ''}  {f['address'] or ''}")
            print(f"        {f['url']}")
            for reason in x["evaluation"]["reasons"]:
                print(f"        - {reason}")
    elif args.cmd == "probe":
        from .geo.features import FeatureSet, parse_elements
        from .geo.metrics import SEARCH_M, FeatureIndex
        from .geo.overpass import LAYERS, tiles_for
        from .scoring import evaluate

        fs, seen = FeatureSet(), set()
        for layer in LAYERS:
            for d in ctx.tiles.get_many(layer, tiles_for(args.lat, args.lon, SEARCH_M + 50)):
                parse_elements(d["elements"], fs, seen)
        prof = FeatureIndex(fs, ctx.station_index()).measure(args.lat, args.lon)
        print(json.dumps({"profile": prof, "evaluation": evaluate(prof, ctx.cfg["evaluation"])}, ensure_ascii=False, indent=1))
    elif args.cmd == "serve":
        from .web.server import serve

        serve(ctx, args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
