from __future__ import annotations

import argparse
import json
from pathlib import Path

from .routes import find_routes


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Rank Naval Action MAIN trade routes from a normalized snapshot")
    p.add_argument("--snapshot", default="data/latest.json")
    p.add_argument("--start-port-id", type=int)
    p.add_argument("--cargo", type=float, default=1000.0, help="Cargo capacity by weight")
    p.add_argument("--reals", type=int, default=1_000_000, help="Available Reals")
    p.add_argument("--min-roi", type=float, default=0.0, help="Minimum ROI as decimal, e.g. 0.25")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--include-nonactive", action="store_true")
    p.add_argument("--ship-water-class", choices=("deep", "shallow"), default="deep")
    p.add_argument("--navigation-data", help="Optional Navigation v2 CSV path")
    p.add_argument("--output")
    return p


def main() -> int:
    args = parser().parse_args()
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    routes = find_routes(
        snapshot,
        start_port_id=args.start_port_id,
        cargo_capacity=args.cargo,
        available_reals=args.reals,
        min_roi=args.min_roi,
        active_only=not args.include_nonactive,
        limit=args.limit,
        ship_water_class=args.ship_water_class,
        navigation_path=args.navigation_data,
    )
    payload = {
        "snapshot": args.snapshot,
        "inputs": {
            "startPortId": args.start_port_id,
            "cargoCapacity": args.cargo,
            "availableReals": args.reals,
            "minROI": args.min_roi,
            "shipWaterClass": args.ship_water_class,
            "navigationData": args.navigation_data or "data/navigation_v2.csv",
        },
        "routeCount": len(routes),
        "routes": routes,
    }
    encoded = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
