"""List production dates that are about to pass.

Reads every *_VALID_UNTIL setting in config/environment-sync.json plus the items in
config/expiries.json, and prints one line per date. Under GitHub Actions it writes
`warn` (lines due within --warn-days) and `urgent` (due within --urgent-days, or already past)
to $GITHUB_OUTPUT as multi-line values.

    python3 scripts/check-expiries.py                 # report
    python3 scripts/check-expiries.py --today 2026-12-10
"""

import argparse
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_day(value: str) -> date:
    """A calendar day from YYYY-MM-DD or a full ISO time (its UTC day)."""
    if len(value) == 10:
        return date.fromisoformat(value)
    moment = datetime.fromisoformat(value)
    return (moment.astimezone(UTC) if moment.tzinfo else moment).date()


def watched(sync: dict, extra: dict) -> list[dict]:
    items = []
    for target in sync.get("targets", []):
        for key, value in target.get("settings", {}).items():
            if key.endswith("_VALID_UNTIL"):
                items.append({"name": f"{key} on {target['name']}", "expires": value,
                              "renew": "Recheck the published price, then update the date in "
                                       "rockygpt-infra config/environment-sync.json and release "
                                       "to main."})
    items.extend(extra.get("items", []))
    return items


def check(items: list[dict], today: date, warn_days: int, urgent_days: int):
    lines, warn, urgent = [], [], []
    for item in sorted(items, key=lambda i: parse_day(i["expires"])):
        left = (parse_day(item["expires"]) - today).days
        line = f"{item['name']}: expires {item['expires']} ({left} days left). {item['renew']}"
        lines.append(line)
        if left <= urgent_days:
            urgent.append(line)
        elif left <= warn_days:
            warn.append(line)
    return lines, warn, urgent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sync", type=Path, default=ROOT / "config/environment-sync.json")
    parser.add_argument("--extra", type=Path, default=ROOT / "config/expiries.json")
    parser.add_argument("--today", type=date.fromisoformat, default=datetime.now(UTC).date())
    parser.add_argument("--warn-days", type=int, default=21)
    parser.add_argument("--urgent-days", type=int, default=7)
    args = parser.parse_args(argv)
    items = watched(json.loads(args.sync.read_text()), json.loads(args.extra.read_text()))
    lines, warn, urgent = check(items, args.today, args.warn_days, args.urgent_days)
    for line in lines:
        print(line)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as handle:
            for name, found in (("warn", warn), ("urgent", urgent)):
                handle.write(f"{name}<<EOF\n" + "".join(f"- {line}\n" for line in found) + "EOF\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
