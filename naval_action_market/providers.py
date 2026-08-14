from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .schema import SCHEMA_VERSION, SERVER, SERVER_LABEL, utc_now_iso


class ProviderError(RuntimeError):
    pass


class BaseProvider:
    name = "base"

    def collect(self) -> Dict[str, Any]:
        raise NotImplementedError


@dataclass
class DirectAPIConfig:
    base_url: str
    api_key: Optional[str] = None
    snapshot_path: str = "/market/snapshot"


class DirectAPIProvider(BaseProvider):
    """Adapter for an authorized NAAPI endpoint once its contract is known.

    Expected ideal response is already normalized. If NavalGaming/Game-Labs provides
    different endpoints, only this adapter should need to change.
    """

    name = "api"

    def __init__(self, config: DirectAPIConfig, timeout: int = 30) -> None:
        self.config = config
        self.timeout = timeout

    def collect(self) -> Dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        url = urljoin(self.config.base_url.rstrip("/") + "/", self.config.snapshot_path.lstrip("/"))
        response = requests.get(
            url,
            params={"server": SERVER},
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()

        if payload.get("metadata", {}).get("server") == SERVER:
            return payload

        raise ProviderError(
            "Direct API response contract is not configured yet. "
            "Set the actual NAAPI endpoint/adapter after access is granted."
        )


def _number(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return None
    value = float(match.group(0))
    return int(value) if value.is_integer() else value


def _clean(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("▼", "")).strip()


def _query_id(href: Optional[str], key: str) -> Optional[int]:
    if not href:
        return None
    parsed = urlparse(href)
    values = parse_qs(parsed.query).get(key)
    if not values:
        return None
    try:
        return int(values[0])
    except ValueError:
        return None


def _extract_nav_map_configs(html: str) -> List[Dict[str, Any]]:
    configs: List[Dict[str, Any]] = []
    for match in re.finditer(r"NavalMap\.render\((\{.*?\})\);", html, re.S):
        raw = match.group(1)
        try:
            configs.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return configs


class NavalGamingHTMLProvider(BaseProvider):
    name = "html"

    def __init__(
        self,
        base_url: str = "https://navalgaming.com",
        timeout: int = 30,
        batch_pause: float = 0.25,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.batch_pause = batch_pause
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "NavalActionMarket/0.1 (+private analytical collector)",
            "Accept": "text/html,application/xhtml+xml",
        })

    def _get(self, path: str) -> str:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                response = self.session.get(url, timeout=self.timeout)
                if response.status_code == 429:
                    time.sleep(3 * attempt)
                    continue
                response.raise_for_status()
                return response.text
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(attempt)
        raise ProviderError(f"failed to fetch {url}: {last_error}")

    def discover_ports(self) -> List[Dict[str, Any]]:
        found: Dict[int, Dict[str, Any]] = {}
        paths = [
            f"/map/?server={SERVER}",
            f"/tacticalmap/?server={SERVER}",
            f"/trading/market/?server={SERVER}",
            f"/market/?server={SERVER}",
        ]

        for path in paths:
            try:
                html = self._get(path)
            except ProviderError:
                continue

            for cfg in _extract_nav_map_configs(html):
                for port in cfg.get("ports", []):
                    try:
                        port_id = int(port.get("Id"))
                    except (TypeError, ValueError):
                        continue
                    found[port_id] = {
                        **found.get(port_id, {}),
                        "id": port_id,
                        "name": port.get("Name") or found.get(port_id, {}).get("name"),
                        "nation": port.get("Nation"),
                        "nationName": port.get("NationName"),
                        "x": _number(str(port.get("x"))) if port.get("x") is not None else None,
                        "y": _number(str(port.get("y"))) if port.get("y") is not None else None,
                    }

            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.select('a[href*="portid="]'):
                port_id = _query_id(anchor.get("href"), "portid")
                if port_id is None:
                    continue
                row = found.setdefault(port_id, {"id": port_id})
                if not row.get("name"):
                    row["name"] = _clean(anchor.get_text(" ", strip=True)) or None

        return sorted(found.values(), key=lambda p: p["id"])

    @staticmethod
    def _parse_price_qty(cell: Any) -> Dict[str, Optional[float]]:
        text = _clean(cell.get_text(" ", strip=True) if cell else "")
        if not text or "—" in text:
            return {"price": None, "qty": None}
        price_el = cell.select_one(".price-value") if cell else None
        price = _number(price_el.get_text(" ", strip=True) if price_el else None)
        qty_match = re.search(r"\(([\d,]+)\)", text)
        qty = _number(qty_match.group(1)) if qty_match else None
        return {"price": price, "qty": qty}

    def parse_port_page(self, html: str, requested_port: Dict[str, Any]) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".standard-parchment-card")

        def card_named(title: str) -> Any:
            for card in cards:
                heading = card.select_one(".standard-card-header h4")
                if heading and _clean(heading.get_text(" ", strip=True)).startswith(title):
                    return card
            return None

        special = {"Market Contracts", "Resources Added", "Trade Goods"}
        port_card = None
        for card in cards:
            heading = card.select_one(".standard-card-header h4")
            title = _clean(heading.get_text(" ", strip=True) if heading else "")
            if title and title not in special:
                port_card = card
                break
        if port_card is None and cards:
            port_card = cards[0]

        title_el = port_card.select_one(".standard-card-header h4") if port_card else None
        port_name = _clean(title_el.get_text(" ", strip=True) if title_el else requested_port.get("name"))

        meta = {
            "id": requested_port["id"],
            "name": port_name or requested_port.get("name"),
            "nation": requested_port.get("nation"),
            "nationName": requested_port.get("nationName"),
            "x": requested_port.get("x"),
            "y": requested_port.get("y"),
            "taxRate": None,
            "lastTax": None,
            "lastCost": None,
            "net": None,
            "serverVerification": "UNKNOWN",
            "sourceUrl": f"{self.base_url}/trading/market/?portid={requested_port['id']}&server={SERVER}",
        }

        if port_card:
            for detail in port_card.select(".trading-detail"):
                text = _clean(detail.get_text(" ", strip=True))
                if text.lower().startswith("port tax rate:"):
                    n = _number(text)
                    meta["taxRate"] = None if n is None else n / 100
                elif text.lower().startswith("last tax:"):
                    meta["lastTax"] = _number(text)
                elif text.lower().startswith("last cost:"):
                    meta["lastCost"] = _number(text)
                elif text.lower().startswith("net:"):
                    meta["net"] = _number(text)

        raw_contracts: List[Dict[str, Any]] = []
        market_card = card_named("Market Contracts")
        if market_card:
            for section in market_card.select(".itemtype-section"):
                category_el = section.select_one(".itemtype-header .detail-label")
                category = _clean(category_el.get_text(" ", strip=True) if category_el else "") or None
                for row in section.select(".market-grid-row"):
                    cells = row.find_all(recursive=False)
                    if len(cells) < 3:
                        continue
                    link = cells[0].select_one('a[href*="itemid="]')
                    if not link:
                        continue
                    item_id = _query_id(link.get("href"), "itemid")
                    if item_id is None:
                        continue
                    supply = self._parse_price_qty(cells[1])
                    demand = self._parse_price_qty(cells[2])
                    if all(v is None for v in [supply["price"], supply["qty"], demand["price"], demand["qty"]]):
                        continue
                    raw_contracts.append({
                        "portId": requested_port["id"],
                        "portName": meta["name"],
                        "itemId": item_id,
                        "displayedName": _clean(link.get_text(" ", strip=True)),
                        "category": category,
                        "supplyPrice": supply["price"],
                        "supplyQty": supply["qty"],
                        "demandPrice": demand["price"],
                        "demandQty": demand["qty"],
                    })

        priorities = {"Resource": 100, "Material": 90, "Consumables": 80, "ConvertibleItem": 70, "MarkItem": 60, "Cannon": 50}
        groups: Dict[int, List[Dict[str, Any]]] = {}
        for row in raw_contracts:
            groups.setdefault(row["itemId"], []).append(row)

        market_contracts: List[Dict[str, Any]] = []
        alias_conflicts: List[Dict[str, Any]] = []
        for item_id, rows in groups.items():
            signatures = {
                (r["supplyPrice"], r["supplyQty"], r["demandPrice"], r["demandQty"])
                for r in rows
            }
            if len(signatures) > 1:
                alias_conflicts.append({"portId": requested_port["id"], "itemId": item_id, "rows": rows})
            rows.sort(key=lambda r: priorities.get(r.get("category"), 0), reverse=True)
            selected = dict(rows[0])
            canonical = {2975: "Ball Ammo", 2976: "Chain Ammo", 2977: "Grape Ammo"}
            if item_id in canonical:
                selected["displayedName"] = canonical[item_id]
            market_contracts.append(selected)

        resources: List[Dict[str, Any]] = []
        resources_card = card_named("Resources Added")
        if resources_card:
            for row in resources_card.select(".standard-card-body > .trading-detail"):
                link = row.select_one('a[href*="itemid="]')
                if not link:
                    continue
                item_id = _query_id(link.get("href"), "itemid")
                if item_id is None:
                    continue
                price_el = row.select_one(".price-value")
                resources.append({
                    "portId": requested_port["id"],
                    "portName": meta["name"],
                    "itemId": item_id,
                    "displayedName": _clean(link.get_text(" ", strip=True)),
                    "amount": _number(price_el.get_text(" ", strip=True) if price_el else None),
                })

        trade_goods: List[Dict[str, Any]] = []
        goods_card = card_named("Trade Goods")
        if goods_card:
            for row in goods_card.select(".standard-card-body > .trading-detail"):
                link = row.select_one('a[href*="itemid="]')
                if not link:
                    continue
                item_id = _query_id(link.get("href"), "itemid")
                if item_id is None:
                    continue
                text = _clean(row.get_text(" ", strip=True))
                qty_match = re.search(r"Qty:\s*([\d,]+)", text, re.I)
                weight_match = re.search(r"Weight:\s*([\d,.]+)", text, re.I)
                price_el = row.select_one(".price-value")
                trade_goods.append({
                    "portId": requested_port["id"],
                    "portName": meta["name"],
                    "itemId": item_id,
                    "displayedName": _clean(link.get_text(" ", strip=True)),
                    "price": _number(price_el.get_text(" ", strip=True) if price_el else None),
                    "qty": _number(qty_match.group(1)) if qty_match else None,
                    "weight": _number(weight_match.group(1)) if weight_match else None,
                })

        return {
            "port": meta,
            "marketContracts": market_contracts,
            "resourcesAdded": resources,
            "tradeGoods": trade_goods,
            "aliasConflicts": alias_conflicts,
        }

    def collect(self) -> Dict[str, Any]:
        started = utc_now_iso()
        ports = self.discover_ports()
        if len(ports) < 180:
            raise ProviderError(f"only {len(ports)} ports discovered; refusing incomplete snapshot")

        results: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        for index, port in enumerate(ports):
            try:
                html = self._get(f"/trading/market/?portid={port['id']}&server={SERVER}")
                results.append(self.parse_port_page(html, port))
            except Exception as exc:  # retain per-port failures in snapshot diagnostics
                failures.append({"portId": port["id"], "name": port.get("name"), "error": str(exc)})
            if index + 1 < len(ports):
                time.sleep(self.batch_pause)

        port_rows = [r["port"] for r in results]
        contracts = [x for r in results for x in r["marketContracts"]]
        resources = [x for r in results for x in r["resourcesAdded"]]
        goods = [x for r in results for x in r["tradeGoods"]]
        conflicts = [x for r in results for x in r["aliasConflicts"]]

        return {
            "metadata": {
                "schemaVersion": SCHEMA_VERSION,
                "server": SERVER,
                "serverLabel": SERVER_LABEL,
                "provider": self.name,
                "startedAt": started,
                "finishedAt": utc_now_iso(),
                "discoveredPorts": len(ports),
                "successfulPorts": len(results),
                "failedPorts": len(failures),
            },
            "diagnostics": {
                "portRows": len(port_rows),
                "marketContractRows": len(contracts),
                "resourcesAddedRows": len(resources),
                "tradeGoodsRows": len(goods),
                "aliasConflictCount": len(conflicts),
            },
            "ports": port_rows,
            "marketContracts": contracts,
            "resourcesAdded": resources,
            "tradeGoods": goods,
            "aliasConflicts": conflicts,
            "failures": failures,
        }
