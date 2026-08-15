from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from naval_action_market.navigation import NavigationLookup


EXPECTED_PORTS = 207
EXPECTED_PAIRS = EXPECTED_PORTS * (EXPECTED_PORTS - 1) // 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Validate a Navigation v2 all-pairs CSV")
    p.add_argument("path", nargs="?", default="data/navigation_v2.csv")
    return p


def main() -> int:
    path = Path(parser().parse_args().path)
    if not path.exists():
        raise SystemExit(f"Navigation data not found: {path}")

    lookup = NavigationLookup.from_csv(path)
    if len(lookup) != EXPECTED_PAIRS:
        raise SystemExit(f"Expected {EXPECTED_PAIRS} unique pairs, got {len(lookup)}")

    ids = set()
    invalid_shallow = 0
    invalid_deep = 0
    below_straight_after_load = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        ids.add(int(row["origin_id"]))
        ids.add(int(row["destination_id"]))
        pair = lookup.get(int(row["origin_id"]), int(row["destination_id"]))
        if pair is None:
            raise SystemExit("Lookup missing a CSV pair")
        if not pair.shallow_valid:
            invalid_shallow += 1
        if not pair.deep_valid:
            invalid_deep += 1
        if pair.shallow_valid and pair.shallow_route_k is not None and pair.shallow_route_k + 1e-9 < pair.straight_k:
            below_straight_after_load += 1
        if pair.deep_valid and pair.deep_route_k is not None and pair.deep_route_k + 1e-9 < pair.straight_k:
            below_straight_after_load += 1

    if len(ids) != EXPECTED_PORTS:
        raise SystemExit(f"Expected {EXPECTED_PORTS} ports, got {len(ids)}")
    if below_straight_after_load:
        raise SystemExit(f"Found {below_straight_after_load} routes below straight-distance lower bound")

    print(json.dumps({
        "path": str(path),
        "ports": len(ids),
        "pairs": len(rows),
        "lookupPairs": len(lookup),
        "invalidShallowPairs": invalid_shallow,
        "invalidDeepPairs": invalid_deep,
        "status": "ok",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
