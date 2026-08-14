from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = "https://storage.googleapis.com/nacleanopenworldprodshards"
SHARDS = [f"cleanopenworldprodeu{i}" for i in range(1, 9)]
FILES = ["Ports", "Shops", "ItemTemplates", "Nations"]
KNOWN_PORTS = {"Nassau", "Guacata", "Pitt's Town", "Saint George's Town"}
OUT = Path("research/public_api_probe.json")


def parse_payload(raw: bytes) -> tuple[Any, str]:
    text = raw.decode("utf-8-sig", errors="replace").strip()
    wrapper = "json"
    if not text:
        raise ValueError("empty response")

    if not text.startswith(("{", "[")):
        eq = text.find("=")
        if eq < 0:
            raise ValueError("payload is neither JSON nor a JS assignment")
        wrapper = text[:eq].strip()
        text = text[eq + 1 :].strip()

    text = text.rstrip().rstrip(";").strip()
    return json.loads(text), wrapper


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def top_level_summary(obj: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"type": type(obj).__name__}
    if isinstance(obj, dict):
        result["keys"] = list(obj.keys())[:30]
        result["keyCount"] = len(obj)
        list_sizes = {k: len(v) for k, v in obj.items() if isinstance(v, list)}
        if list_sizes:
            result["listSizes"] = list_sizes
    elif isinstance(obj, list):
        result["length"] = len(obj)
    return result


def analyze_ports(obj: Any) -> dict[str, Any]:
    ports: list[dict[str, Any]] = []
    if isinstance(obj, dict) and isinstance(obj.get("Ports"), list):
        ports = [p for p in obj["Ports"] if isinstance(p, dict)]
    elif isinstance(obj, list):
        ports = [p for p in obj if isinstance(p, dict)]
    else:
        for d in walk(obj):
            name = d.get("Name") or d.get("name")
            if isinstance(name, str) and ("Id" in d or "ID" in d or "id" in d):
                ports.append(d)

    known: list[dict[str, Any]] = []
    for p in ports:
        name = p.get("Name") or p.get("name")
        if name in KNOWN_PORTS:
            known.append(
                {
                    "name": name,
                    "id": p.get("Id", p.get("ID", p.get("id"))),
                    "nation": p.get("Nation", p.get("nation")),
                    "x": p.get("x", p.get("PositionX", p.get("sourcePosition_x"))),
                    "y": p.get("y", p.get("PositionZ", p.get("sourcePosition_y"))),
                }
            )

    return {"portCount": len(ports), "knownPorts": known}


def analyze_generic(obj: Any) -> dict[str, Any]:
    key_counts: Counter[str] = Counter()
    dict_count = 0
    interesting_examples: list[dict[str, Any]] = []
    interesting_re = re.compile(r"(port|location|shop|item|template|price|amount|quantity|qty|buy|sell)", re.I)

    for d in walk(obj):
        dict_count += 1
        key_counts.update(map(str, d.keys()))
        if len(interesting_examples) < 8 and any(interesting_re.search(str(k)) for k in d.keys()):
            sample = {k: d[k] for k in list(d.keys())[:12]}
            try:
                json.dumps(sample)
                interesting_examples.append(sample)
            except TypeError:
                pass

    return {
        "dictCount": dict_count,
        "commonKeys": key_counts.most_common(30),
        "examples": interesting_examples,
    }


def fetch(url: str) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NavalActionMarket-public-feed-research/1.0",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read(1024)
        return exc.code, dict(exc.headers.items()), body


def probe_one(file_name: str, shard: str) -> dict[str, Any]:
    url = f"{BASE}/{file_name}_{shard}.json"
    record: dict[str, Any] = {"file": file_name, "shard": shard, "url": url}
    try:
        status, headers, raw = fetch(url)
        record.update(
            {
                "status": status,
                "bytes": len(raw),
                "contentType": headers.get("Content-Type"),
                "lastModified": headers.get("Last-Modified"),
                "etag": headers.get("ETag"),
                "generation": headers.get("x-goog-generation"),
                "sha256": hashlib.sha256(raw).hexdigest() if status == 200 else None,
            }
        )
        if status != 200:
            record["responsePrefix"] = raw[:160].decode("utf-8", errors="replace")
            return record

        obj, wrapper = parse_payload(raw)
        record["parseOk"] = True
        record["wrapper"] = wrapper
        record["topLevel"] = top_level_summary(obj)
        if file_name == "Ports":
            record["analysis"] = analyze_ports(obj)
        else:
            record["analysis"] = analyze_generic(obj)
        return record
    except Exception as exc:  # research script: preserve every failure in output
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record


def main() -> int:
    results: list[dict[str, Any]] = []
    for shard in SHARDS:
        for file_name in FILES:
            print(f"Probing {file_name} {shard} ...", flush=True)
            rec = probe_one(file_name, shard)
            results.append(rec)
            print(
                f"  status={rec.get('status')} bytes={rec.get('bytes')} "
                f"parse={rec.get('parseOk', False)} modified={rec.get('lastModified')}",
                flush=True,
            )

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "base": BASE,
        "results": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")

    live = [r for r in results if r.get("status") == 200 and r.get("parseOk")]
    print(f"Successful parseable objects: {len(live)}/{len(results)}")
    return 0 if live else 2


if __name__ == "__main__":
    sys.exit(main())
