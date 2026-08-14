from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _port_map(snapshot: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    return {int(p["id"]): p for p in snapshot.get("ports", []) if p.get("id") is not None}


def _buy_tax(gross: int, rate: float) -> int:
    # Buy-side fractional rounding is not yet directly validated. Use ceil() so
    # route recommendations never overstate profit by one Real on tiny trades.
    return int(math.ceil(gross * rate - 1e-12))


def _sell_tax(gross: int, rate: float) -> int:
    # Direct in-game sell tests at Aves confirmed truncation/floor.
    return int(math.floor(gross * rate + 1e-12))


def _max_units_by_capital(price: int, tax_rate: float, capital: int) -> int:
    if price <= 0 or capital <= 0:
        return 0
    lo, hi = 0, capital // price
    while lo < hi:
        mid = (lo + hi + 1) // 2
        gross = price * mid
        cost = gross + _buy_tax(gross, tax_rate)
        if cost <= capital:
            lo = mid
        else:
            hi = mid - 1
    return lo


def find_routes(
    snapshot: Dict[str, Any],
    *,
    start_port_id: Optional[int] = None,
    cargo_capacity: float = 1000.0,
    available_reals: int = 1_000_000,
    min_roi: float = 0.0,
    active_only: bool = True,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    ports = _port_map(snapshot)
    shop_rows = snapshot.get("shopItems", [])

    sources: List[Dict[str, Any]] = []
    destinations_by_item: Dict[int, List[Dict[str, Any]]] = {}

    for row in shop_rows:
        if active_only and not row.get("active114"):
            continue
        item_id = row.get("itemId")
        if item_id is None:
            continue
        buy_price = _num(row.get("gameBuyPrice"))
        buy_qty = row.get("gameBuyQty")
        sell_price = _num(row.get("gameSellPrice"))
        sell_qty = row.get("gameSellQty")
        if buy_price is not None and buy_price > 0 and isinstance(buy_qty, int) and buy_qty > 0:
            if start_port_id is None or int(row.get("portId")) == int(start_port_id):
                sources.append(row)
        if sell_price is not None and sell_price > 0 and isinstance(sell_qty, int) and sell_qty > 0:
            destinations_by_item.setdefault(int(item_id), []).append(row)

    routes: List[Dict[str, Any]] = []
    for src in sources:
        item_id = int(src["itemId"])
        buy_price = int(round(float(src["gameBuyPrice"])))
        source_qty = int(src["gameBuyQty"])
        weight = _num(src.get("weight"))
        if weight is None or weight <= 0:
            continue
        source_port_id = int(src["portId"])
        source_port = ports.get(source_port_id, {})
        buy_tax_rate = float(source_port.get("taxRate") or 0.0)

        capacity_units = int(math.floor(cargo_capacity / weight))
        capital_units = _max_units_by_capital(buy_price, buy_tax_rate, available_reals)
        if capacity_units <= 0 or capital_units <= 0:
            continue

        for dst in destinations_by_item.get(item_id, []):
            dest_port_id = int(dst["portId"])
            if dest_port_id == source_port_id:
                continue
            sell_price = int(round(float(dst["gameSellPrice"])))
            demand_qty = int(dst["gameSellQty"])
            qty = min(source_qty, demand_qty, capacity_units, capital_units)
            if qty <= 0:
                continue

            dest_port = ports.get(dest_port_id, {})
            sell_tax_rate = float(dest_port.get("taxRate") or 0.0)
            gross_cost = buy_price * qty
            buy_tax = _buy_tax(gross_cost, buy_tax_rate)
            total_cost = gross_cost + buy_tax
            gross_revenue = sell_price * qty
            sell_tax = _sell_tax(gross_revenue, sell_tax_rate)
            net_revenue = gross_revenue - sell_tax
            profit = net_revenue - total_cost
            roi = profit / total_cost if total_cost else 0.0
            if profit <= 0 or roi < min_roi:
                continue

            sx, sy = _num(source_port.get("x")), _num(source_port.get("y"))
            dx, dy = _num(dest_port.get("x")), _num(dest_port.get("y"))
            distance = None
            if None not in (sx, sy, dx, dy):
                distance = math.hypot(dx - sx, dy - sy)

            cargo_weight = qty * weight
            routes.append({
                "itemId": item_id,
                "item": src.get("itemName"),
                "sourcePortId": source_port_id,
                "sourcePort": src.get("portName"),
                "destinationPortId": dest_port_id,
                "destinationPort": dst.get("portName"),
                "quantity": qty,
                "weightPerUnit": weight,
                "cargoWeight": cargo_weight,
                "buyPrice": buy_price,
                "sellPrice": sell_price,
                "grossPurchase": gross_cost,
                "buyTax": buy_tax,
                "totalCost": total_cost,
                "grossRevenue": gross_revenue,
                "sellTax": sell_tax,
                "netRevenue": net_revenue,
                "netProfit": profit,
                "roi": roi,
                "profitPerWeight": profit / cargo_weight if cargo_weight else None,
                "mapDistance": distance,
                "profitPerMapDistance": profit / distance if distance else None,
                "sourceAvailableQty": source_qty,
                "destinationDemandQty": demand_qty,
                "confidence": "B_RESET_SNAPSHOT",
                "buyTaxRounding": "conservative-ceil-until-in-game-rounding-validated",
                "sellTaxRounding": "floor-validated-in-game",
            })

    routes.sort(key=lambda r: (r["netProfit"], r["roi"], r.get("profitPerWeight") or 0), reverse=True)
    for index, route in enumerate(routes[:limit], start=1):
        route["rank"] = index
    return routes[:limit]
