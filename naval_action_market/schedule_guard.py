from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Europe/Bucharest")
PRE_SHUTDOWN = {(12, 55)}
POST_RESTART = {(13, 35), (13, 40), (13, 45), (13, 50), (13, 55)}


def classify(now: datetime | None = None) -> str:
    current = now.astimezone(TZ) if now else datetime.now(TZ)
    hhmm = (current.hour, current.minute)
    if hhmm in PRE_SHUTDOWN:
        return "pre"
    if hhmm in POST_RESTART:
        return "post"
    return "skip"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-local", action="store_true")
    args = parser.parse_args()

    now = datetime.now(TZ)
    mode = classify(now)
    if args.print_local:
        print(now.isoformat())
    print(mode)
    return 0 if mode != "skip" else 3


if __name__ == "__main__":
    raise SystemExit(main())
