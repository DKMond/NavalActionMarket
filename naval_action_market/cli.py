from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .providers import DirectAPIConfig, DirectAPIProvider, NavalGamingHTMLProvider, ProviderError
from .public_shard import PublicShardProvider
from .schema import validate_snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect a Naval Action MAIN market snapshot")
    parser.add_argument(
        "--provider",
        choices=["shard", "api", "html"],
        default=os.getenv("MARKET_PROVIDER", "shard"),
    )
    parser.add_argument("--output", default="data/latest.json")
    parser.add_argument(
        "--allow-html",
        action="store_true",
        help="Explicitly acknowledge use of the fallback HTML collector.",
    )
    parser.add_argument("--api-base-url", default=os.getenv("NAAPI_BASE_URL"))
    parser.add_argument("--api-key", default=os.getenv("NAAPI_API_KEY"))
    parser.add_argument("--api-snapshot-path", default=os.getenv("NAAPI_SNAPSHOT_PATH", "/market/snapshot"))
    parser.add_argument("--history-dir", default="data/history")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.provider == "shard":
        provider = PublicShardProvider()
    elif args.provider == "html":
        if not args.allow_html and os.getenv("ALLOW_HTML_COLLECTOR") != "1":
            raise SystemExit(
                "HTML collection is disabled by default. Use --allow-html only after authorization is confirmed."
            )
        provider = NavalGamingHTMLProvider()
    else:
        if not args.api_base_url:
            raise SystemExit("NAAPI_BASE_URL is required for provider=api")
        provider = DirectAPIProvider(
            DirectAPIConfig(
                base_url=args.api_base_url,
                api_key=args.api_key,
                snapshot_path=args.api_snapshot_path,
            )
        )

    try:
        snapshot = provider.collect()
    except ProviderError as exc:
        raise SystemExit(str(exc)) from exc

    validation = validate_snapshot(snapshot)
    snapshot["validation"] = validation.to_dict()

    if not validation.valid:
        print(json.dumps(snapshot["validation"], indent=2))
        raise SystemExit("snapshot validation failed; latest.json was not replaced")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    history_dir = Path(args.history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = snapshot["metadata"]["finishedAt"].replace(":", "-").replace(".", "-")
    history_file = history_dir / f"MAIN_{stamp}.json"

    encoded = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    history_file.write_text(encoded, encoding="utf-8")
    output.write_text(encoded, encoding="utf-8")

    print(json.dumps({
        "provider": snapshot["metadata"].get("provider"),
        "latest": str(output),
        "history": str(history_file),
        "validation": snapshot["validation"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
