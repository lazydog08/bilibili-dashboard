from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, select_autoescape

from analytics import (
    derive_dashboard_context,
    load_fixture_history,
    load_history,
    merge_today_snapshot,
    save_history,
)
from config import Settings, load_settings
from fetcher.bilibili_api import ANTI_RISK_MESSAGE, BilibiliAuthOrRiskError, BilibiliClient
from fetcher.feishu_api import is_configured as feishu_is_configured
from fetcher.feishu_api import upsert_daily_summary


def render_dashboard(context: dict[str, Any], settings: Settings) -> Path:
    template_text = settings.template_path.read_text(encoding="utf-8")
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    template = env.from_string(template_text)
    html = template.render(**context)
    settings.output_path.parent.mkdir(parents=True, exist_ok=True)
    settings.output_path.write_text(html, encoding="utf-8")
    return settings.output_path


def _latest_snapshot(history: dict[str, Any]) -> dict[str, Any] | None:
    snapshots = [item for item in history.get("snapshots", []) if isinstance(item, dict)]
    if not snapshots:
        return None
    snapshots.sort(key=lambda item: str(item.get("date", "")))
    return snapshots[-1]


def _resolve_snapshot_date(value: str | None, timezone_name: str) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    today = datetime.now(ZoneInfo(timezone_name)).date()
    if text == "today":
        return today.isoformat()
    if text == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("--snapshot-date must be today, yesterday, or YYYY-MM-DD") from exc


def _apply_snapshot_date(snapshot: dict[str, Any], snapshot_date: str | None) -> dict[str, Any]:
    if snapshot_date:
        snapshot["date"] = snapshot_date
    return snapshot


async def _try_live_snapshot(settings: Settings) -> tuple[dict[str, Any] | None, list[str]]:
    if not settings.enable_bilibili_fetch:
        return None, ["未设置 ENABLE_BILIBILI_FETCH=1，已跳过实时获取。"]
    if not settings.bilibili_cookie_present:
        return None, ["未配置 BILIBILI_COOKIE，已跳过实时获取。"]
    try:
        client = BilibiliClient()
        return await client.fetch_snapshot(), []
    except BilibiliAuthOrRiskError:
        print(ANTI_RISK_MESSAGE)
        return None, [ANTI_RISK_MESSAGE]
    except Exception as exc:  # noqa: BLE001 - live data must not prevent fixture/cache rendering.
        return None, [f"实时获取失败，已使用缓存或示例数据：{exc}"]


def _load_cache_or_fixture(settings: Settings, warnings: list[str]) -> tuple[dict[str, Any], str]:
    cache = load_history(settings.history_path)
    if cache.get("snapshots"):
        cache["source"] = "cache"
        cache["warnings"] = [*cache.get("warnings", []), *warnings]
        return cache, "cache"
    fixture = load_fixture_history(settings.fixture_path)
    fixture["source"] = "fixture"
    fixture["warnings"] = [*fixture.get("warnings", []), *warnings]
    return fixture, "fixture"


async def build_dashboard(args: argparse.Namespace, settings: Settings) -> dict[str, Any]:
    warnings: list[str] = []
    snapshot_date = _resolve_snapshot_date(args.snapshot_date, settings.timezone)

    if args.fixture:
        history = load_fixture_history(settings.fixture_path)
        history["source"] = "fixture"
    else:
        should_live = args.live or settings.enable_bilibili_fetch
        snapshot = None
        if should_live:
            snapshot, warnings = await _try_live_snapshot(settings)
        else:
            warnings.append("未启用实时获取，已使用缓存或示例数据。")

        if snapshot:
            snapshot = _apply_snapshot_date(snapshot, snapshot_date)
            history = load_history(settings.history_path)
            history = merge_today_snapshot(history, snapshot)
            history["source"] = "live"
        else:
            history, _ = _load_cache_or_fixture(settings, warnings)

    save_history(history, settings.history_path)
    context = derive_dashboard_context(history, settings)
    output_path = render_dashboard(context, settings)

    feishu_summary = "Feishu sync skipped: disabled by --no-feishu." if args.no_feishu else ""
    latest = _latest_snapshot(history)
    if not args.no_feishu:
        if settings.feishu_enabled and feishu_is_configured() and latest:
            feishu_summary = await upsert_daily_summary(latest, settings.feishu_date_format)
        else:
            feishu_summary = "Feishu sync skipped: missing configuration."

    return {
        "history": history,
        "context": context,
        "output_path": output_path,
        "feishu_summary": feishu_summary,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the Bilibili creator analytics dashboard.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fixture", action="store_true", help="Use bundled fixture data and never touch the network.")
    mode.add_argument("--live", action="store_true", help="Try live Bilibili Creator Center fetch when credentials allow.")
    parser.add_argument("--no-feishu", action="store_true", help="Skip optional Feishu Bitable sync.")
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="Override the live snapshot date: today, yesterday, or YYYY-MM-DD.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    result = asyncio.run(build_dashboard(args, settings))
    history = result["history"]
    context = result["context"]
    output_path = result["output_path"]
    warnings = context.get("warnings", [])

    print(f"source: {history.get('source')}")
    print(f"snapshots: {context.get('snapshot_count', 0)}")
    print(f"videos rendered: {len(context.get('recent_videos', []))}")
    print(f"output: {output_path}")
    print(result["feishu_summary"])
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"- {warning}")
    else:
        print("warnings: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
