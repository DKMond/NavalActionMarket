from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple


DEFAULT_NAVIGATION_PATH = Path(__file__).resolve().parents[1] / "data" / "navigation_v2.csv"
VALID_WATER_CLASSES = {"deep", "shallow"}


@dataclass(frozen=True)
class NavigationPair:
    origin_id: int
    destination_id: int
    straight_k: float
    shallow_route_k: Optional[float]
    deep_route_k: Optional[float]
    shallow_valid: bool
    deep_valid: bool

    def route_k(self, water_class: str) -> Optional[float]:
        water_class = water_class.lower().strip()
        if water_class == "shallow":
            return self.shallow_route_k if self.shallow_valid else None
        if water_class == "deep":
            return self.deep_route_k if self.deep_valid else None
        raise ValueError(f"Unsupported water class: {water_class!r}")


def _key(a: int, b: int) -> Tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def _optional_float(value: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


class NavigationLookup:
    def __init__(self, pairs: Dict[Tuple[int, int], NavigationPair], source: Path):
        self._pairs = pairs
        self.source = source

    @classmethod
    def from_csv(cls, path: Path | str = DEFAULT_NAVIGATION_PATH) -> "NavigationLookup":
        source = Path(path)
        pairs: Dict[Tuple[int, int], NavigationPair] = {}
        if not source.exists():
            return cls(pairs, source)

        with source.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    a = int(row["origin_id"])
                    b = int(row["destination_id"])
                    straight = float(row["straight_K"])
                except (KeyError, TypeError, ValueError):
                    continue

                shallow = _optional_float(row.get("shallow_route_K", ""))
                deep = _optional_float(row.get("deep_route_K", ""))
                shallow_valid = _bool(row.get("shallow_valid", "")) and shallow is not None
                deep_valid = _bool(row.get("deep_valid", "")) and deep is not None

                # The routing graph uses snapped water anchors on a raster grid.
                # Quantization can make a route appear a few K shorter than the
                # direct geometric lower bound. Never serve an impossible value.
                if shallow_valid and shallow is not None:
                    shallow = max(straight, shallow)
                if deep_valid and deep is not None:
                    deep = max(straight, deep)

                pairs[_key(a, b)] = NavigationPair(
                    origin_id=min(a, b),
                    destination_id=max(a, b),
                    straight_k=straight,
                    shallow_route_k=shallow,
                    deep_route_k=deep,
                    shallow_valid=shallow_valid,
                    deep_valid=deep_valid,
                )
        return cls(pairs, source)

    def get(self, origin_id: int, destination_id: int) -> Optional[NavigationPair]:
        if origin_id == destination_id:
            return NavigationPair(origin_id, destination_id, 0.0, 0.0, 0.0, True, True)
        return self._pairs.get(_key(int(origin_id), int(destination_id)))

    def __len__(self) -> int:
        return len(self._pairs)


@lru_cache(maxsize=4)
def load_navigation(path: str = str(DEFAULT_NAVIGATION_PATH)) -> NavigationLookup:
    return NavigationLookup.from_csv(Path(path))


def fallback_trader_k(
    sx: Optional[float], sy: Optional[float], dx: Optional[float], dy: Optional[float]
) -> Optional[float]:
    """Trader Tool K from public-shard/game coordinates if v2 data is absent."""
    if None in (sx, sy, dx, dy):
        return None
    return math.hypot(float(dx) - float(sx), float(dy) - float(sy)) / 1000.0
