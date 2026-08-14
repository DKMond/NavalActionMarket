from __future__ import annotations

import json
import math
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from naval_action_market.providers import NavalGamingHTMLProvider

BASE = "https://storage.googleapis.com/nacleanopenworldprodshards"
SHARD = "cleanopenworldprodeu3"
OUT = Path("research/live_source_compare.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_js_assignment(text: str) -> Any:
    text = text.lstrip("\ufeff").strip()
    if text.startswith(("[", "{")):
        return json.loads(text.rstrip(";"))
    eq = text.find("=")
    if eq < 0:
        raise ValueError("API payload is not JSON or a JavaScript assignment")
    return json.loads(text[eq + 1 :].strip().rstrip(";").strip())


def fetch_api_object(name: str) -> tuple[Any, dict[str, Any]]:
    url = f"{BASE}/{name}_{SHARD}.json"
    response = requests.get(url, timeout=90, headers={"User-Agent": "NavalActionMarket-comparison/1.0"})
    response.raise_for_status()
    return parse_js_assignment(response.text), {
        "url": url,
        "etag": response.headers.get("ETag"),
        "lastModified": response.headers.get("Last-Modified"),
        "generation": response.headers.get("x-goog-generation"),
        "bytes": len(response.content),
    }


def norm_name(value: str | None) -> str:
    value = (value or "").replace("’", "'").replace("–", "-")
    value = re.sub(r"\s+", " ", value).strip().casefold()
    return value


def rint(value: float | int | None) -> int | None:
    if value is None:
        return None
    # Python round is bankers rounding while JavaScript Math.round is half-up for positive values.
    return int(math.floor(float(value) + 0.5))


def main() -> int:
    started = utc_now()

    ports_api, ports_meta = fetch_api_object("Ports")
    shops_api, shops_meta = fetch_api_object("Shops")
    items_api, items_meta = fetch_api_object("ItemTemplates")
    nations_api, nations_meta = fetch_api_object("Nations")

    item_by_id = {int(x["Id"]): x for x in items_api if isinstance(x, dict) and "Id" in x}
    shop_by_id = {int(x["Id"]): x for x in shops_api if isinstance(x, dict) and "Id" in x}
    api_port_by_id = {int(x["Id"]): x for x in ports_api if isinstance(x, dict) and "Id" in x}

    nation_names = {}
    if isinstance(nations_api, dict):
        for row in nations_api.get("Nations", []):
            nation_names[int(row["Id"])] = row.get("Name")

    # Crawl NavalGaming immediately after API retrieval so the two observations are as close as practical.
    ng_provider = NavalGamingHTMLProvider(batch_pause=0.05)
    ng = ng_provider.collect()
    ng_finished = utc_now()

    ng_ports = {int(x["id"]): x for x in ng.get("ports", [])}
    ng_resources = {(int(x["portId"]), int(x["itemId"])): x for x in ng.get("resourcesAdded", [])}
    ng_goods = {(int(x["portId"]), int(x["itemId"])): x for x in ng.get("tradeGoods", [])}
    ng_market = {(int(x["portId"]), int(x["itemId"])): x for x in ng.get("marketContracts", [])}

    # ---------- ports ----------
    common_port_ids = sorted(set(api_port_by_id) & set(ng_ports))
    nation_matches = 0
    tax_matches = 0
    name_matches = 0
    port_mismatches = []
    for pid in common_port_ids:
        a = api_port_by_id[pid]
        b = ng_ports[pid]
        same_nation = int(a.get("Nation", -999)) == int(b.get("nation", -998))
        same_tax = abs(float(a.get("PortTax", 0) or 0) - float(b.get("taxRate", 0) or 0)) < 1e-9
        # NavalGaming appends region/county text, so startswith is the useful semantic comparison.
        an = norm_name(a.get("Name"))
        bn = norm_name(b.get("name"))
        same_name = bn == an or bn.startswith(an + ",") or bn.startswith(an + " -")
        nation_matches += same_nation
        tax_matches += same_tax
        name_matches += same_name
        if len(port_mismatches) < 25 and not (same_nation and same_tax and same_name):
            port_mismatches.append({
                "portId": pid,
                "apiName": a.get("Name"),
                "ngName": b.get("name"),
                "apiNation": a.get("Nation"),
                "ngNation": b.get("nation"),
                "apiTax": a.get("PortTax"),
                "ngTax": b.get("taxRate"),
            })

    # ---------- ResourcesAdded ----------
    api_resources: dict[tuple[int, int], dict[str, Any]] = {}
    duplicate_resource_keys = Counter()
    for pid, shop in shop_by_id.items():
        for row in shop.get("ResourcesAdded", []) or []:
            key = (pid, int(row["Template"]))
            duplicate_resource_keys[key] += 1
            # NavalGaming de-duplicates display aliases by item ID. Keep final identical-key row here.
            api_resources[key] = {
                "portId": pid,
                "itemId": key[1],
                "amount": row.get("Amount"),
                "isTrading": row.get("IsTrading"),
                "source": row.get("Source"),
            }

    resource_keys_common = set(api_resources) & set(ng_resources)
    resource_amount_exact = sum(
        api_resources[k].get("amount") == ng_resources[k].get("amount") for k in resource_keys_common
    )
    resource_examples = []
    for key in sorted(resource_keys_common):
        a, b = api_resources[key], ng_resources[key]
        if a.get("amount") != b.get("amount") and len(resource_examples) < 25:
            resource_examples.append({
                "portId": key[0], "itemId": key[1],
                "item": item_by_id.get(key[1], {}).get("Name"),
                "apiAmount": a.get("amount"), "navalGamingAmount": b.get("amount"),
            })

    # ---------- Trade Goods ----------
    # NA-map classifies server trade goods as SortingGroup == Resource.Trading.
    api_goods: dict[tuple[int, int], dict[str, Any]] = {}
    for pid, shop in shop_by_id.items():
        tax = float(api_port_by_id.get(pid, {}).get("PortTax", 0) or 0)
        for row in shop.get("RegularItems", []) or []:
            iid = int(row["TemplateId"])
            item = item_by_id.get(iid, {})
            if item.get("SortingGroup") != "Resource.Trading":
                continue
            qty = row.get("BuyContractQuantity") if row.get("Quantity") == -1 else row.get("Quantity")
            api_goods[(pid, iid)] = {
                "portId": pid,
                "itemId": iid,
                "item": item.get("Name"),
                "rawBuyPrice": row.get("BuyPrice"),
                "effectiveBuyPrice": rint(float(row.get("BuyPrice", 0)) * (1 + tax)),
                "qty": qty,
                "weight": item.get("ItemWeight"),
            }

    goods_common = set(api_goods) & set(ng_goods)
    goods_price_raw_match = sum(api_goods[k]["rawBuyPrice"] == ng_goods[k].get("price") for k in goods_common)
    goods_price_tax_match = sum(api_goods[k]["effectiveBuyPrice"] == ng_goods[k].get("price") for k in goods_common)
    goods_qty_match = sum(api_goods[k]["qty"] == ng_goods[k].get("qty") for k in goods_common)
    goods_weight_match = sum(api_goods[k]["weight"] == ng_goods[k].get("weight") for k in goods_common)
    goods_examples = []
    for key in sorted(goods_common):
        a, b = api_goods[key], ng_goods[key]
        if (
            a["effectiveBuyPrice"] != b.get("price")
            or a["qty"] != b.get("qty")
            or a["weight"] != b.get("weight")
        ) and len(goods_examples) < 25:
            goods_examples.append({
                "portId": key[0], "itemId": key[1], "item": a["item"],
                "apiRawBuyPrice": a["rawBuyPrice"], "apiTaxBuyPrice": a["effectiveBuyPrice"],
                "ngPrice": b.get("price"), "apiQty": a["qty"], "ngQty": b.get("qty"),
                "apiWeight": a["weight"], "ngWeight": b.get("weight"),
            })

    # ---------- Market Contracts ----------
    # Test candidate interpretations rather than assuming one. The report scores each mapping.
    candidate_rows: dict[tuple[int, int], dict[str, Any]] = {}
    contract_flag_count = 0
    for pid, shop in shop_by_id.items():
        tax = float(api_port_by_id.get(pid, {}).get("PortTax", 0) or 0)
        for row in shop.get("RegularItems", []) or []:
            iid = int(row["TemplateId"])
            item = item_by_id.get(iid, {})
            if item.get("SortingGroup") == "Resource.Trading":
                continue
            if row.get("IsContractsExist"):
                contract_flag_count += 1
            supply_qty = row.get("BuyContractQuantity") if row.get("Quantity") == -1 else row.get("Quantity")
            demand_qty = item.get("PriceTierQuantity") if row.get("SellContractQuantity") == -1 else row.get("SellContractQuantity")
            candidate_rows[(pid, iid)] = {
                "isContractsExist": bool(row.get("IsContractsExist")),
                "rawSupplyPrice": row.get("BuyPrice"),
                "taxSupplyPrice": rint(float(row.get("BuyPrice", 0)) * (1 + tax)),
                "supplyQty": supply_qty,
                "rawDemandPrice": row.get("SellPrice"),
                "taxDemandPrice": rint(float(row.get("SellPrice", 0)) / (1 + tax)) if (1 + tax) else row.get("SellPrice"),
                "demandQty": demand_qty,
                "item": item.get("Name"),
                "itemType": item.get("ItemType"),
                "sortingGroup": item.get("SortingGroup"),
            }

    market_common = set(candidate_rows) & set(ng_market)

    def market_score(filter_contracts: bool, tax_adjusted: bool) -> dict[str, Any]:
        keys = {
            k for k, v in candidate_rows.items()
            if (not filter_contracts or v["isContractsExist"])
        }
        overlap = keys & set(ng_market)
        exact = 0
        price_matches = 0
        qty_matches = 0
        for k in overlap:
            a, b = candidate_rows[k], ng_market[k]
            sp = a["taxSupplyPrice"] if tax_adjusted else a["rawSupplyPrice"]
            dp = a["taxDemandPrice"] if tax_adjusted else a["rawDemandPrice"]
            price_ok = sp == b.get("supplyPrice") and dp == b.get("demandPrice")
            qty_ok = a["supplyQty"] == b.get("supplyQty") and a["demandQty"] == b.get("demandQty")
            price_matches += price_ok
            qty_matches += qty_ok
            exact += price_ok and qty_ok
        return {
            "apiCandidateRows": len(keys),
            "navalGamingRows": len(ng_market),
            "overlapRows": len(overlap),
            "apiOnlyRows": len(keys - set(ng_market)),
            "navalGamingOnlyRows": len(set(ng_market) - keys),
            "exactFourFieldMatches": exact,
            "pricePairMatches": price_matches,
            "quantityPairMatches": qty_matches,
        }

    market_scores = {
        "all_non_trade_raw_prices": market_score(False, False),
        "all_non_trade_tax_adjusted": market_score(False, True),
        "contracts_flag_raw_prices": market_score(True, False),
        "contracts_flag_tax_adjusted": market_score(True, True),
    }

    market_examples = []
    for key in sorted(market_common):
        a, b = candidate_rows[key], ng_market[key]
        if len(market_examples) >= 40:
            break
        market_examples.append({
            "portId": key[0], "itemId": key[1], "item": a["item"],
            "isContractsExist": a["isContractsExist"],
            "apiRawSupplyPrice": a["rawSupplyPrice"], "apiTaxSupplyPrice": a["taxSupplyPrice"],
            "ngSupplyPrice": b.get("supplyPrice"),
            "apiSupplyQty": a["supplyQty"], "ngSupplyQty": b.get("supplyQty"),
            "apiRawDemandPrice": a["rawDemandPrice"], "apiTaxDemandPrice": a["taxDemandPrice"],
            "ngDemandPrice": b.get("demandPrice"),
            "apiDemandQty": a["demandQty"], "ngDemandQty": b.get("demandQty"),
        })

    report = {
        "generatedAt": utc_now(),
        "startedAt": started,
        "navalGamingFinishedAt": ng_finished,
        "api": {
            "shard": SHARD,
            "ports": ports_meta,
            "shops": shops_meta,
            "items": items_meta,
            "nations": nations_meta,
        },
        "navalGaming": {
            "metadata": ng.get("metadata"),
            "diagnostics": ng.get("diagnostics"),
        },
        "comparison": {
            "ports": {
                "apiRows": len(api_port_by_id),
                "navalGamingRows": len(ng_ports),
                "commonIds": len(common_port_ids),
                "nationMatches": nation_matches,
                "taxMatches": tax_matches,
                "nameMatches": name_matches,
                "mismatchExamples": port_mismatches,
            },
            "resourcesAdded": {
                "apiUniqueRows": len(api_resources),
                "apiRawRows": sum(len((s.get("ResourcesAdded") or [])) for s in shops_api),
                "navalGamingRows": len(ng_resources),
                "commonKeys": len(resource_keys_common),
                "apiOnlyKeys": len(set(api_resources) - set(ng_resources)),
                "navalGamingOnlyKeys": len(set(ng_resources) - set(api_resources)),
                "amountExactMatches": resource_amount_exact,
                "duplicateApiKeys": sum(1 for v in duplicate_resource_keys.values() if v > 1),
                "mismatchExamples": resource_examples,
            },
            "tradeGoods": {
                "apiRows": len(api_goods),
                "navalGamingRows": len(ng_goods),
                "commonKeys": len(goods_common),
                "apiOnlyKeys": len(set(api_goods) - set(ng_goods)),
                "navalGamingOnlyKeys": len(set(ng_goods) - set(api_goods)),
                "rawPriceMatches": goods_price_raw_match,
                "taxAdjustedPriceMatches": goods_price_tax_match,
                "quantityMatches": goods_qty_match,
                "weightMatches": goods_weight_match,
                "mismatchExamples": goods_examples,
            },
            "marketContracts": {
                "regularNonTradeCandidateRows": len(candidate_rows),
                "isContractsExistTrueRows": contract_flag_count,
                "navalGamingRows": len(ng_market),
                "scores": market_scores,
                "overlapExamples": market_examples,
            },
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(OUT),
        "ports": report["comparison"]["ports"],
        "resources": {k: v for k, v in report["comparison"]["resourcesAdded"].items() if k != "mismatchExamples"},
        "tradeGoods": {k: v for k, v in report["comparison"]["tradeGoods"].items() if k != "mismatchExamples"},
        "marketScores": market_scores,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
