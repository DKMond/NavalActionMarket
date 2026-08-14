from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from .config import ACTIVE_TRADE_ITEM_IDS, MAIN_SHARD, PUBLIC_BUCKET
from .providers import BaseProvider, ProviderError
from .schema import SCHEMA_VERSION, SERVER, SERVER_LABEL, utc_now_iso


@dataclass(frozen=True)
class PublicShardObject:
    name: str
    payload: Any
    url: str
    etag: Optional[str]
    last_modified: Optional[str]
    generation: Optional[str]
    size: int
    sha256: str


def _parse_js_assignment(raw: bytes) -> Any:
    text = raw.decode("utf-8-sig", errors="strict").strip()
    if not text:
        raise ProviderError("public shard returned an empty payload")
    if text.startswith(("[", "{")):
        body = text.rstrip(";").strip()
    else:
        eq = text.find("=")
        if eq < 0:
            raise ProviderError("public shard payload is neither JSON nor a JS assignment")
        body = text[eq + 1 :].strip().rstrip(";").strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"failed to decode public shard payload: {exc}") from exc


def _position(port: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    pos = port.get("Position")
    if isinstance(pos, dict):
        x = pos.get("x", pos.get("X"))
        y = pos.get("z", pos.get("y", pos.get("Y")))
        return x, y
    return (
        port.get("x", port.get("sourcePosition_x")),
        port.get("y", port.get("sourcePosition_y")),
    )


def _nation_lookup(payload: Any) -> Dict[int, str]:
    rows: Iterable[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("Nations") or payload.get("nations") or []
    else:
        rows = []
    out: Dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("Id", row.get("ID", row.get("id")))
        try:
            nation_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        name = row.get("Name") or row.get("name") or row.get("Title")
        if isinstance(name, str) and name.strip():
            out[nation_id] = name.strip()
    return out


def _item_lookup(payload: Any) -> Dict[int, Dict[str, Any]]:
    rows: Iterable[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("ItemTemplates") or payload.get("Items") or payload.get("items") or []
    else:
        rows = []
    out: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            item_id = int(row.get("Id", row.get("ID", row.get("id"))))
        except (TypeError, ValueError):
            continue
        out[item_id] = row
    return out


def _list_payload(payload: Any, preferred_key: str) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        rows = payload.get(preferred_key) or payload.get(preferred_key.lower()) or []
        if isinstance(rows, list):
            return [x for x in rows if isinstance(x, dict)]
    return []


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _available_buy_qty(row: Dict[str, Any]) -> Optional[int]:
    """Player-facing Buy quantity derived from validated in-game observations.

    If an active buy-side contract quantity exists, the client exposes that quantity.
    Otherwise it exposes ordinary shop Quantity. Sentinel -1 means unavailable.
    """
    contract_qty = _as_int(row.get("BuyContractQuantity"))
    if bool(row.get("IsContractsExist")) and contract_qty is not None and contract_qty >= 0:
        return contract_qty
    qty = _as_int(row.get("Quantity"))
    return None if qty is None or qty < 0 else qty


def _available_sell_qty(row: Dict[str, Any]) -> Optional[int]:
    qty = _as_int(row.get("SellContractQuantity"))
    return None if qty is None or qty < 0 else qty


class PublicShardProvider(BaseProvider):
    """Production adapter for the public Naval Action MAIN/Caribbean shard.

    The current MAIN shard is ``cleanopenworldprodeu3``. The provider keeps raw
    shop fields alongside derived player-facing fields so later discoveries do not
    invalidate historical snapshots.
    """

    name = "public-shard"

    def __init__(
        self,
        bucket: str = PUBLIC_BUCKET,
        shard: str = MAIN_SHARD,
        timeout: int = 90,
    ) -> None:
        self.bucket = bucket.rstrip("/")
        self.shard = shard
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "NavalActionMarket/1.0 public-shard collector",
            "Accept": "*/*",
        })

    def _fetch(self, name: str) -> PublicShardObject:
        url = f"{self.bucket}/{name}_{self.shard}.json"
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ProviderError(f"failed to fetch {url}: {exc}") from exc
        raw = response.content
        return PublicShardObject(
            name=name,
            payload=_parse_js_assignment(raw),
            url=url,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            generation=response.headers.get("x-goog-generation"),
            size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )

    @staticmethod
    def _source_meta(obj: PublicShardObject) -> Dict[str, Any]:
        return {
            "url": obj.url,
            "etag": obj.etag,
            "lastModified": obj.last_modified,
            "generation": obj.generation,
            "bytes": obj.size,
            "sha256": obj.sha256,
        }

    def collect(self) -> Dict[str, Any]:
        started = utc_now_iso()
        ports_obj = self._fetch("Ports")
        shops_obj = self._fetch("Shops")
        items_obj = self._fetch("ItemTemplates")
        nations_obj = self._fetch("Nations")

        nation_names = _nation_lookup(nations_obj.payload)
        items_by_id = _item_lookup(items_obj.payload)
        raw_ports = _list_payload(ports_obj.payload, "Ports")
        raw_shops = _list_payload(shops_obj.payload, "Shops")

        ports: List[Dict[str, Any]] = []
        ports_by_id: Dict[int, Dict[str, Any]] = {}
        for raw in raw_ports:
            port_id = _as_int(raw.get("Id", raw.get("ID", raw.get("id"))))
            if port_id is None:
                continue
            nation = _as_int(raw.get("Nation", raw.get("nation")))
            x, y = _position(raw)
            tax = raw.get("PortTax", raw.get("Tax", raw.get("taxRate")))
            port = {
                "id": port_id,
                "name": raw.get("Name") or raw.get("name"),
                "nation": nation,
                "nationName": nation_names.get(nation) if nation is not None else None,
                "x": x,
                "y": y,
                "taxRate": tax,
                "raw": raw,
            }
            ports.append(port)
            ports_by_id[port_id] = port

        item_rows: List[Dict[str, Any]] = []
        for item_id, raw in items_by_id.items():
            item_rows.append({
                "id": item_id,
                "name": raw.get("Name") or raw.get("name"),
                "itemType": raw.get("ItemType"),
                "sortingGroup": raw.get("SortingGroup"),
                "weight": raw.get("Weight"),
                "basePrice": raw.get("BasePrice"),
                "priceTierQuantity": raw.get("PriceTierQuantity"),
                "maxQuantity": raw.get("MaxQuantity"),
                "resetStockOnServerStart": raw.get("ResetStockOnServerStart"),
                "active114": item_id in ACTIVE_TRADE_ITEM_IDS,
                "raw": raw,
            })

        shop_items: List[Dict[str, Any]] = []
        contracts: List[Dict[str, Any]] = []
        resources_added: List[Dict[str, Any]] = []
        trade_goods: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []

        seen_shop_ports = set()
        for shop in raw_shops:
            port_id = _as_int(shop.get("Id", shop.get("ID", shop.get("id"))))
            if port_id is None:
                continue
            seen_shop_ports.add(port_id)
            port = ports_by_id.get(port_id, {"id": port_id, "name": None, "taxRate": None})

            for raw_item in shop.get("RegularItems") or []:
                if not isinstance(raw_item, dict):
                    continue
                item_id = _as_int(raw_item.get("TemplateId"))
                if item_id is None:
                    continue
                item = items_by_id.get(item_id, {})
                buy_price = raw_item.get("BuyPrice")
                sell_price = raw_item.get("SellPrice")
                buy_qty = _available_buy_qty(raw_item)
                sell_qty = _available_sell_qty(raw_item)
                row = {
                    "portId": port_id,
                    "portName": port.get("name"),
                    "nation": port.get("nation"),
                    "nationName": port.get("nationName"),
                    "taxRate": port.get("taxRate"),
                    "itemId": item_id,
                    "itemName": item.get("Name") or item.get("name"),
                    "itemType": item.get("ItemType"),
                    "sortingGroup": item.get("SortingGroup"),
                    "weight": item.get("Weight"),
                    "active114": item_id in ACTIVE_TRADE_ITEM_IDS,
                    "gameBuyPrice": buy_price,
                    "gameBuyQty": buy_qty,
                    "gameSellPrice": sell_price,
                    "gameSellQty": sell_qty,
                    "rawQuantity": raw_item.get("Quantity"),
                    "rawQuantityBought": raw_item.get("QuantityBought"),
                    "rawBuyPrice": buy_price,
                    "rawSellPrice": sell_price,
                    "rawBuyContractQuantity": raw_item.get("BuyContractQuantity"),
                    "rawSellContractQuantity": raw_item.get("SellContractQuantity"),
                    "isContractsExist": bool(raw_item.get("IsContractsExist")),
                    "raw": raw_item,
                }
                shop_items.append(row)

                buy_contract_qty = _as_int(raw_item.get("BuyContractQuantity"))
                sell_contract_qty = _as_int(raw_item.get("SellContractQuantity"))
                if bool(raw_item.get("IsContractsExist")) and (
                    (buy_contract_qty is not None and buy_contract_qty >= 0)
                    or (sell_contract_qty is not None and sell_contract_qty >= 0)
                ):
                    contracts.append({
                        "portId": port_id,
                        "portName": port.get("name"),
                        "itemId": item_id,
                        "itemName": item.get("Name") or item.get("name"),
                        "supplyPrice": buy_price if buy_contract_qty is not None and buy_contract_qty >= 0 else None,
                        "supplyQty": buy_contract_qty if buy_contract_qty is not None and buy_contract_qty >= 0 else None,
                        "demandPrice": sell_price if sell_contract_qty is not None and sell_contract_qty >= 0 else None,
                        "demandQty": sell_contract_qty if sell_contract_qty is not None and sell_contract_qty >= 0 else None,
                        "active114": item_id in ACTIVE_TRADE_ITEM_IDS,
                        "source": "public-shard-contract-fields",
                    })

                if item.get("SortingGroup") == "Resource.Trading":
                    trade_goods.append({
                        "portId": port_id,
                        "portName": port.get("name"),
                        "itemId": item_id,
                        "itemName": item.get("Name") or item.get("name"),
                        "price": buy_price,
                        "qty": buy_qty,
                        "sellPrice": sell_price,
                        "sellQty": sell_qty,
                        "weight": item.get("Weight"),
                        "active114": item_id in ACTIVE_TRADE_ITEM_IDS,
                    })

            for raw_resource in shop.get("ResourcesAdded") or []:
                if not isinstance(raw_resource, dict):
                    continue
                item_id = _as_int(
                    raw_resource.get("TemplateId", raw_resource.get("ItemId", raw_resource.get("Id")))
                )
                if item_id is None:
                    continue
                item = items_by_id.get(item_id, {})
                amount = raw_resource.get("Amount", raw_resource.get("Quantity", raw_resource.get("Value")))
                resources_added.append({
                    "portId": port_id,
                    "portName": port.get("name"),
                    "itemId": item_id,
                    "itemName": item.get("Name") or item.get("name"),
                    "amount": amount,
                    "active114": item_id in ACTIVE_TRADE_ITEM_IDS,
                    "raw": raw_resource,
                })

        missing_shop_ports = sorted(set(ports_by_id) - seen_shop_ports)
        for port_id in missing_shop_ports:
            failures.append({"portId": port_id, "error": "port missing from Shops object"})

        finished = utc_now_iso()
        return {
            "metadata": {
                "schemaVersion": SCHEMA_VERSION,
                "server": SERVER,
                "serverLabel": SERVER_LABEL,
                "provider": self.name,
                "shard": self.shard,
                "startedAt": started,
                "finishedAt": finished,
                "discoveredPorts": len(ports),
                "successfulPorts": len(seen_shop_ports),
                "failedPorts": len(failures),
                "sourceObjects": {
                    "ports": self._source_meta(ports_obj),
                    "shops": self._source_meta(shops_obj),
                    "items": self._source_meta(items_obj),
                    "nations": self._source_meta(nations_obj),
                },
                "freshnessClass": "daily-public-shard",
            },
            "diagnostics": {
                "portRows": len(ports),
                "itemRows": len(item_rows),
                "shopItemRows": len(shop_items),
                "marketContractRows": len(contracts),
                "resourcesAddedRows": len(resources_added),
                "tradeGoodsRows": len(trade_goods),
                "active114ShopRows": sum(1 for x in shop_items if x["active114"]),
            },
            "ports": ports,
            "items": item_rows,
            "shopItems": shop_items,
            "marketContracts": contracts,
            "resourcesAdded": resources_added,
            "tradeGoods": trade_goods,
            "failures": failures,
        }
