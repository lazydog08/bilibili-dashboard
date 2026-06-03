#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_last_updated(history_path: Path) -> str | None:
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    if isinstance(payload.get("last_updated"), str):
        return payload["last_updated"]

    snapshots = payload.get("snapshots")
    if isinstance(snapshots, list) and snapshots:
        latest = snapshots[-1]
        if isinstance(latest, dict) and isinstance(latest.get("updated_at"), str):
            return latest["updated_at"]
    return None


def write_github_outputs(values: dict[str, str]) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Decide whether the cloud fallback should refresh the dashboard.")
    parser.add_argument(
        "--history-path",
        default=os.getenv("DASHBOARD_HISTORY_PATH", "data/history.json"),
        help="Dashboard history JSON path.",
    )
    parser.add_argument(
        "--stale-minutes",
        type=int,
        default=int(os.getenv("DASHBOARD_CLOUD_STALE_MINUTES", "60")),
        help="Refresh when the last dashboard update is at least this old.",
    )
    args = parser.parse_args()

    history_path = Path(args.history_path)
    now_text = os.getenv("DASHBOARD_FRESHNESS_NOW")
    now = parse_datetime(now_text) if now_text else datetime.now(timezone.utc)

    reason = ""
    should_refresh = False
    age_minutes = -1
    last_updated = ""

    if os.getenv("DASHBOARD_FORCE_REFRESH") == "1":
        should_refresh = True
        reason = "forced"
    elif not history_path.exists():
        should_refresh = True
        reason = "missing_history"
    else:
        try:
            loaded = load_last_updated(history_path)
            if not loaded:
                should_refresh = True
                reason = "missing_last_updated"
            else:
                last_updated = loaded
                updated_at = parse_datetime(loaded)
                age_minutes = max(0, int((now - updated_at).total_seconds() // 60))
                should_refresh = age_minutes >= args.stale_minutes
                reason = "stale" if should_refresh else "fresh"
        except Exception as exc:
            should_refresh = True
            reason = f"invalid_history_{exc.__class__.__name__}"

    write_github_outputs(
        {
            "should_refresh": "true" if should_refresh else "false",
            "age_minutes": str(age_minutes),
            "last_updated": last_updated,
            "reason": reason,
        }
    )
    print(
        f"{reason}: should_refresh={'true' if should_refresh else 'false'} "
        f"age_minutes={age_minutes} stale_minutes={args.stale_minutes} last_updated={last_updated}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
