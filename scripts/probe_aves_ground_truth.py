from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from naval_action_market.providers import NavalGamingHTMLProvider

BASE = "https://storage.googleapis.com/nacleanopenworldprodshards"
SHARD = "cleanopenworldprodeu3"
PORT_ID = 150
OUT = Path("research/aves_ground_truth_compare.json")

# Transcribed from the user's Aves shop screenshots posted 2026-08-14 around 15:57 +03:00.
# In the game UI, '-' under a price is represented here as None quantity.
GAME_OBSERVATIONS = {
    "Fish Meat": {"sellPrice": 1, "sellQty": None, "buyPrice": 4, "buyQty": 161},
    "Molasses": {"sellPrice": 1, "sellQty": None, "buyPrice": 4, "buyQty": 1},
    "Tuna": {"sellPrice": 150, "sellQty": 101, "buyPrice": 1, "buyQty": None},
    "Bull Shark": {"sellPrice": 150, "sellQty": 82, "buyPrice": 1, "buyQty": None},
    "Malabar Teak": {"sellPrice": 1, "sellQty": None, "buyPrice": 1500, "buyQty": 955},
    "Silver ingots": {"sellPrice": 1000, "sellQty": None, "buyPrice": 1095, "buyQty": 4},
    "Languedoc Violins": {"sellPrice": 1000, "sellQty": None, "buyPrice": 1093, "buyQty": 3},
    "Tobacco": {"sellPrice": 1, "sellQty": None, "buyPrice": 3, "buyQty": 2400000},
    "Iron Ore": {"sellPrice": 1, "sellQty": None, "buyPrice": 5, "buyQty": 3600000},
    "Fishing Nets": {"sellPrice": 1, "sellQty": None, "buyPrice": 4, "buyQty": 121},
    "Salt": {"sellPrice": 1, "sellQty": None, "buyPrice": 4, "buyQty": 134},
    "Fishing Hooks": {"sellPrice": 1, "sellQty": None, "buyPrice": 4, "buyQty": 100},
    "Oak Log": {"sellPrice": 1, "sellQty": None, "buyPrice": 3, "buyQty": 4800000},
    "Lignum Vitae Log": {"sellPrice": 1, "sellQty": None, "buyPrice": 5, "buyQty": 2400000},
}

ALIASES = {
    "Malabar Teak": ["Malabar Teak", "Malabar Teak Log"],
    "Silver ingots": ["Silver ingots", "Silver Ingots"],
    "Languedoc Violins": ["Languedoc Violins"],
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_js_assignment(raw: bytes) -> Any:
    text = raw.decode("utf-8-sig", errors="strict").strip()
    if text.startswith(("[", "{")):
        return json.loads(text.rstrip(";"))
    eq = text.find("=")
    if eq < 0:
        raise ValueError("payload is not JSON or a JavaScript assignment")
    return json.loads(text[eq + 1 :].strip().rstrip(";").strip())


def fetch_object(name: str) -> tuple[Any, dict[str, Any]]:
    url = f"{BASE}/{name}_{SHARD}.json"
    response = requests.get(url, timeout=90, headers={"User-Agent": "NavalActionMarket-Aves-ground-truth/1.0"})
    response.raise_for_status()
    return parse_js_assignment(response.content), {
        "url": url,
        "etag": response.headers.get("ETag"),
        "lastModified": response.headers.get("Last-Modified"),
        "generation": response.headers.get("x-goog-generation"),
        "bytes": len(response.content),
    }


def norm(value: str | None) -> str:
    value = (value or "").casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def find_item(items: list[dict[str, Any]], observed_name: str) -> dict[str, Any] | None:
    candidates = [observed_name, *ALIASES.get(observed_name, [])]
    wanted = {norm(x) for x in candidates}
    for item in items:
        if norm(item.get("Name")) in wanted:
            return item
    # Conservative substring fallback only after exact normalized aliases fail.
    for item in items:
        item_name = norm(item.get("Name"))
        if any(w and (w in item_name or item_name in w) for w in wanted):
            return item
    return None


def ui_buy_qty(row: dict[str, Any]) -> Any:
    # Historical NA-map logic uses BuyContractQuantity only when Quantity is the sentinel -1.
    return row.get("BuyContractQuantity") if row.get("Quantity") == -1 else row.get("Quantity")


def ui_sell_qty(row: dict[str, Any]) -> Any:
    value = row.get("SellContractQuantity")
    return None if value == -1 else value


def main() -> int:
    started = now_utc()
    ports, ports_meta = fetch_object("Ports")
    shops, shops_meta = fetch_object("Shops")
    items, items_meta = fetch_object("ItemTemplates")

    port = next(x for x in ports if int(x.get("Id", -1)) == PORT_ID)
    shop = next(x for x in shops if int(x.get("Id", -1)) == PORT_ID)
    regular = {int(x["TemplateId"]): x for x in (shop.get("RegularItems") or [])}

    # Read only Aves from NavalGaming, rather than crawling every port.
    ng_provider = NavalGamingHTMLProvider(batch_pause=0)
    requested_port = {
        "id": PORT_ID,
        "name": "Aves, Central Antilles",
        "nation": port.get("Nation"),
        "nationName": None,
        "x": port.get("Position", {}).get("x") if isinstance(port.get("Position"), dict) else None,
        "y": port.get("Position", {}).get("z") if isinstance(port.get("Position"), dict) else None,
    }
    ng_html = ng_provider._get(f"/trading/market/?portid={PORT_ID}&server=main")
    ng = ng_provider.parse_port_page(ng_html, requested_port)
    ng_market = {int(x["itemId"]): x for x in ng.get("marketContracts", [])}
    ng_goods = {int(x["itemId"]): x for x in ng.get("tradeGoods", [])}
    ng_resources = {int(x["itemId"]): x for x in ng.get("resourcesAdded", [])}

    rows = []
    totals = {
        "observations": len(GAME_OBSERVATIONS),
        "itemResolved": 0,
        "apiRegularItemResolved": 0,
        "apiRawBuyPriceMatchesGame": 0,
        "apiDerivedBuyQtyMatchesGame": 0,
        "apiRawSellPriceMatchesGame": 0,
        "apiDerivedSellQtyMatchesGame": 0,
        "allFourApiUiFieldsMatch": 0,
    }

    for observed_name, game in GAME_OBSERVATIONS.items():
        item = find_item(items, observed_name)
        if item is None:
            rows.append({"observedName": observed_name, "game": game, "error": "item template not resolved"})
            continue
        totals["itemResolved"] += 1
        iid = int(item["Id"])
        raw = regular.get(iid)
        row_out: dict[str, Any] = {
            "observedName": observed_name,
            "itemId": iid,
            "apiName": item.get("Name"),
            "itemType": item.get("ItemType"),
            "sortingGroup": item.get("SortingGroup"),
            "game": game,
            "api": None,
            "navalGamingMarket": ng_market.get(iid),
            "navalGamingTradeGood": ng_goods.get(iid),
            "navalGamingResourceAdded": ng_resources.get(iid),
        }
        if raw is None:
            row_out["error"] = "item absent from Aves RegularItems"
            rows.append(row_out)
            continue

        totals["apiRegularItemResolved"] += 1
        derived_buy_qty = ui_buy_qty(raw)
        derived_sell_qty = ui_sell_qty(raw)
        api_view = {
            "TemplateId": raw.get("TemplateId"),
            "Quantity": raw.get("Quantity"),
            "QuantityBought": raw.get("QuantityBought"),
            "BuyPrice": raw.get("BuyPrice"),
            "SellPrice": raw.get("SellPrice"),
            "BuyContractQuantity": raw.get("BuyContractQuantity"),
            "SellContractQuantity": raw.get("SellContractQuantity"),
            "IsContractsExist": raw.get("IsContractsExist"),
            "derivedGameBuyQty": derived_buy_qty,
            "derivedGameSellQty": derived_sell_qty,
            "PriceTierQuantity": item.get("PriceTierQuantity"),
            "MaxQuantity": item.get("MaxQuantity"),
            "ResetStockOnServerStart": item.get("ResetStockOnServerStart"),
        }
        row_out["api"] = api_view

        matches = {
            "buyPrice": raw.get("BuyPrice") == game["buyPrice"],
            "buyQty": derived_buy_qty == game["buyQty"],
            "sellPrice": raw.get("SellPrice") == game["sellPrice"],
            "sellQty": derived_sell_qty == game["sellQty"],
        }
        row_out["apiVsGame"] = matches
        totals["apiRawBuyPriceMatchesGame"] += int(matches["buyPrice"])
        totals["apiDerivedBuyQtyMatchesGame"] += int(matches["buyQty"])
        totals["apiRawSellPriceMatchesGame"] += int(matches["sellPrice"])
        totals["apiDerivedSellQtyMatchesGame"] += int(matches["sellQty"])
        totals["allFourApiUiFieldsMatch"] += int(all(matches.values()))
        rows.append(row_out)

    report = {
        "generatedAt": now_utc(),
        "startedAt": started,
        "manualObservation": {
            "portId": PORT_ID,
            "port": "Aves",
            "observedAtApprox": "2026-08-14T15:57:00+03:00",
            "source": "user-provided in-game shop screenshots",
            "notes": "Screenshot values are player-facing Sell/Buy columns; '-' quantities are encoded as null.",
        },
        "api": {
            "shard": SHARD,
            "portName": port.get("Name"),
            "portNation": port.get("Nation"),
            "portTax": port.get("PortTax"),
            "portsMeta": ports_meta,
            "shopsMeta": shops_meta,
            "itemsMeta": items_meta,
        },
        "navalGaming": {
            "sourceUrl": ng.get("port", {}).get("sourceUrl"),
            "marketRowsAtAves": len(ng_market),
            "tradeGoodRowsAtAves": len(ng_goods),
            "resourceAddedRowsAtAves": len(ng_resources),
        },
        "summary": totals,
        "rows": rows,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps({"report": str(OUT), "summary": totals, "rows": rows}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
