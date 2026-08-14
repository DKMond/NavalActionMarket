from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List


SCHEMA_VERSION = "NG-MAIN-SNAPSHOT-2"
SERVER = "main"
SERVER_LABEL = "MAIN / CARIBBEAN"


@dataclass
class ValidationResult:
    valid: bool
    discovered_ports: int
    successful_ports: int
    failed_ports: int
    market_rows: int
    resource_rows: int
    trade_good_rows: int
    reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_snapshot(snapshot: Dict[str, Any]) -> ValidationResult:
    meta = snapshot.get("metadata", {})
    diagnostics = snapshot.get("diagnostics", {})

    discovered = int(meta.get("discoveredPorts") or 0)
    successful = int(meta.get("successfulPorts") or 0)
    failed = int(meta.get("failedPorts") or 0)
    market_rows = int(diagnostics.get("marketContractRows") or 0)
    resource_rows = int(diagnostics.get("resourcesAddedRows") or 0)
    trade_rows = int(diagnostics.get("tradeGoodsRows") or 0)

    reasons: List[str] = []
    if meta.get("server") != SERVER:
        reasons.append("server is not MAIN")
    if discovered < 180:
        reasons.append("fewer than 180 ports discovered")
    if successful < 180:
        reasons.append("fewer than 180 ports parsed successfully")
    if failed > 0:
        reasons.append(f"{failed} port pages failed")
    if trade_rows < 200:
        reasons.append("trade-goods dataset looks incomplete")

    return ValidationResult(
        valid=not reasons,
        discovered_ports=discovered,
        successful_ports=successful,
        failed_ports=failed,
        market_rows=market_rows,
        resource_rows=resource_rows,
        trade_good_rows=trade_rows,
        reasons=reasons,
    )
