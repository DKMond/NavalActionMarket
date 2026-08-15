from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from naval_action_market.navigation import fallback_trader_k, load_navigation

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "latest.json"
ROUTES = ROOT / "data" / "latest_routes.json"
OUT = ROOT / "data" / "google_sheets"


def num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def integer(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clean(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return value


def write_csv(path: Path, headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(list(headers))
        for row in rows:
            writer.writerow([clean(v) for v in row])
            count += 1
    return count


def main() -> int:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    metadata = snapshot.get("metadata", {})
    snapshot_utc = metadata.get("finishedAt") or metadata.get("startedAt") or ""
    source_objects = metadata.get("sourceObjects", {})
    shops_source = (source_objects.get("shops") or {}).get("url", "")
    navigation = load_navigation()

    ports = snapshot.get("ports", [])
    port_map: Dict[int, Dict[str, Any]] = {
        int(p["id"]): p for p in ports if p.get("id") is not None
    }

    counts: Dict[str, int] = {}
    counts["live_ports"] = write_csv(
        OUT / "live_ports.csv",
        [
            "portId", "port", "nationId", "nationName", "x", "y", "taxRate",
            "snapshotUTC", "provider", "sourceUrl",
        ],
        (
            (
                p.get("id"), p.get("name"), p.get("nation"), p.get("nationName"),
                p.get("x"), p.get("y"), p.get("taxRate"), snapshot_utc,
                metadata.get("provider"), (source_objects.get("ports") or {}).get("url", ""),
            )
            for p in ports
        ),
    )

    shop_rows = snapshot.get("shopItems", [])
    counts["live_market"] = write_csv(
        OUT / "live_market.csv",
        [
            "portId", "port", "itemId", "item", "weight", "buyPrice", "buyQty",
            "sellPrice", "sellQty", "isContractsExist", "active114", "nationName",
            "taxRate", "snapshotUTC",
        ],
        (
            (
                r.get("portId"), r.get("portName"), r.get("itemId"), r.get("itemName"),
                r.get("weight"), r.get("gameBuyPrice"), r.get("gameBuyQty"),
                r.get("gameSellPrice"), r.get("gameSellQty"), r.get("isContractsExist"),
                r.get("active114"), r.get("nationName"), r.get("taxRate"), snapshot_utc,
            )
            for r in shop_rows
        ),
    )

    counts["live_resources"] = write_csv(
        OUT / "live_resources.csv",
        ["portId", "port", "itemId", "item", "amount", "active114", "snapshotUTC", "sourceUrl"],
        (
            (
                r.get("portId"), r.get("portName"), r.get("itemId"), r.get("itemName"),
                r.get("amount"), r.get("active114"), snapshot_utc, shops_source,
            )
            for r in snapshot.get("resourcesAdded", [])
        ),
    )

    counts["live_trade_goods"] = write_csv(
        OUT / "live_trade_goods.csv",
        [
            "portId", "port", "itemId", "item", "buyPrice", "buyQty", "sellPrice",
            "sellQty", "weight", "active114", "snapshotUTC", "sourceUrl",
        ],
        (
            (
                r.get("portId"), r.get("portName"), r.get("itemId"), r.get("itemName"),
                r.get("price"), r.get("qty"), r.get("sellPrice"), r.get("sellQty"),
                r.get("weight"), r.get("active114"), snapshot_utc, shops_source,
            )
            for r in snapshot.get("tradeGoods", [])
        ),
    )

    sources_by_item: Dict[int, list[Dict[str, Any]]] = defaultdict(list)
    destinations_by_item: Dict[int, list[Dict[str, Any]]] = defaultdict(list)
    for row in shop_rows:
        if not row.get("active114"):
            continue
        item_id = integer(row.get("itemId"))
        if item_id is None:
            continue
        bp, sp = num(row.get("gameBuyPrice")), num(row.get("gameSellPrice"))
        bq, sq = integer(row.get("gameBuyQty")), integer(row.get("gameSellQty"))
        if bp is not None and bp > 0 and bq is not None and bq > 0:
            sources_by_item[item_id].append(row)
        if sp is not None and sp > 0 and sq is not None and sq > 0:
            destinations_by_item[item_id].append(row)

    candidate_rows = []
    candidate_id = 0
    for item_id, sources in sources_by_item.items():
        for src in sources:
            source_port_id = integer(src.get("portId"))
            if source_port_id is None:
                continue
            source_port = port_map.get(source_port_id, {})
            weight = num(src.get("weight"))
            buy_price = num(src.get("gameBuyPrice"))
            source_qty = integer(src.get("gameBuyQty"))
            source_tax = num(source_port.get("taxRate")) or 0.0
            if weight is None or weight <= 0 or buy_price is None or source_qty is None:
                continue
            for dst in destinations_by_item.get(item_id, []):
                destination_port_id = integer(dst.get("portId"))
                if destination_port_id is None or destination_port_id == source_port_id:
                    continue
                sell_price = num(dst.get("gameSellPrice"))
                destination_qty = integer(dst.get("gameSellQty"))
                if sell_price is None or destination_qty is None:
                    continue
                destination_port = port_map.get(destination_port_id, {})
                destination_tax = num(destination_port.get("taxRate")) or 0.0

                approx_margin = sell_price * (1.0 - destination_tax) - buy_price * (1.0 + source_tax)
                if approx_margin <= 0:
                    continue

                sx, sy = num(source_port.get("x")), num(source_port.get("y"))
                dx, dy = num(destination_port.get("x")), num(destination_port.get("y"))
                map_distance = ""
                if None not in (sx, sy, dx, dy):
                    map_distance = math.hypot(float(dx) - float(sx), float(dy) - float(sy))

                nav = navigation.get(source_port_id, destination_port_id)
                trader_k = nav.straight_k if nav else fallback_trader_k(sx, sy, dx, dy)
                shallow_k = nav.shallow_route_k if nav and nav.shallow_valid else None
                deep_k = nav.deep_route_k if nav and nav.deep_valid else None
                shallow_valid = bool(nav and nav.shallow_valid)
                deep_valid = bool(nav and nav.deep_valid)
                shallow_detour = ((shallow_k / trader_k - 1.0) if shallow_k and trader_k else None)
                deep_detour = ((deep_k / trader_k - 1.0) if deep_k and trader_k else None)

                candidate_id += 1
                candidate_rows.append((
                    candidate_id, snapshot_utc, item_id, src.get("itemName"),
                    source_port_id, src.get("portName"), source_port.get("nationName"),
                    destination_port_id, dst.get("portName"), destination_port.get("nationName"),
                    weight, source_qty, destination_qty, int(round(buy_price)), int(round(sell_price)),
                    source_tax, destination_tax, sx, sy, dx, dy, map_distance,
                    trader_k, shallow_k, deep_k, shallow_valid, deep_valid,
                    shallow_detour, deep_detour,
                    "B_RESET_SNAPSHOT",
                ))

    counts["route_candidates"] = write_csv(
        OUT / "route_candidates.csv",
        [
            "candidateId", "snapshotUTC", "itemId", "item", "sourcePortId", "sourcePort",
            "sourceNation", "destinationPortId", "destinationPort", "destinationNation",
            "weight", "sourceQty", "destinationQty", "buyPrice", "sellPrice", "sourceTax",
            "destinationTax", "sourceX", "sourceY", "destinationX", "destinationY",
            "mapDistance", "traderDistanceK", "shallowRouteK", "deepRouteK",
            "shallowValid", "deepValid", "shallowDetourPct", "deepDetourPct", "confidence",
        ],
        candidate_rows,
    )

    if ROUTES.exists():
        routes_payload = json.loads(ROUTES.read_text(encoding="utf-8"))
        counts["latest_routes"] = write_csv(
            OUT / "latest_routes.csv",
            [
                "rank", "itemId", "item", "sourcePortId", "sourcePort", "destinationPortId",
                "destinationPort", "quantity", "weightPerUnit", "cargoWeight", "buyPrice",
                "sellPrice", "totalCost", "netRevenue", "netProfit", "roi", "profitPerWeight",
                "mapDistance", "profitPerMapDistance", "traderDistanceK", "shallowRouteK",
                "deepRouteK", "shallowValid", "deepValid", "shipWaterClass", "routeDistanceK",
                "profitPerK", "navigationConfidence", "sourceAvailableQty", "destinationDemandQty",
                "confidence",
            ],
            (
                (
                    r.get("rank"), r.get("itemId"), r.get("item"), r.get("sourcePortId"),
                    r.get("sourcePort"), r.get("destinationPortId"), r.get("destinationPort"),
                    r.get("quantity"), r.get("weightPerUnit"), r.get("cargoWeight"),
                    r.get("buyPrice"), r.get("sellPrice"), r.get("totalCost"), r.get("netRevenue"),
                    r.get("netProfit"), r.get("roi"), r.get("profitPerWeight"), r.get("mapDistance"),
                    r.get("profitPerMapDistance"), r.get("traderDistanceK"), r.get("shallowRouteK"),
                    r.get("deepRouteK"), r.get("shallowValid"), r.get("deepValid"),
                    r.get("shipWaterClass"), r.get("routeDistanceK"), r.get("profitPerK"),
                    r.get("navigationConfidence"), r.get("sourceAvailableQty"),
                    r.get("destinationDemandQty"), r.get("confidence"),
                )
                for r in routes_payload.get("routes", [])
            ),
        )

    status_rows = [(
        snapshot_utc,
        metadata.get("server"),
        metadata.get("serverLabel"),
        metadata.get("provider"),
        metadata.get("shard"),
        metadata.get("discoveredPorts"),
        metadata.get("successfulPorts"),
        metadata.get("failedPorts"),
        (snapshot.get("diagnostics") or {}).get("shopItemRows"),
        (snapshot.get("diagnostics") or {}).get("marketContractRows"),
        (snapshot.get("diagnostics") or {}).get("resourcesAddedRows"),
        (snapshot.get("diagnostics") or {}).get("tradeGoodsRows"),
        ((source_objects.get("shops") or {}).get("lastModified")),
        ((source_objects.get("shops") or {}).get("generation")),
        ((source_objects.get("shops") or {}).get("etag")),
    )]
    counts["snapshot_status"] = write_csv(
        OUT / "snapshot_status.csv",
        [
            "snapshotUTC", "server", "serverLabel", "provider", "shard", "discoveredPorts",
            "successfulPorts", "failedPorts", "shopRows", "contractRows", "resourceRows",
            "tradeGoodRows", "shopsLastModified", "shopsGeneration", "shopsETag",
        ],
        status_rows,
    )

    print(json.dumps({"snapshotUTC": snapshot_utc, "counts": counts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
