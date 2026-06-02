from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.import_xhs_creator_notes import (
    DEFAULT_MANUAL_PATH,
    DEFAULT_TIMEZONE,
    update_manual_payload,
)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def build_opencli_command(opencli_cmd: str, limit: int) -> list[str]:
    return [
        *shlex.split(opencli_cmd),
        "xiaohongshu",
        "creator-notes",
        "--limit",
        str(limit),
        "-f",
        "json",
    ]


def capture_creator_notes(
    *,
    opencli_cmd: str,
    limit: int,
    runner: Runner = subprocess.run,
) -> Any:
    command = build_opencli_command(opencli_cmd, limit)
    try:
        result = runner(command, text=True, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 opencli 命令；请先安装 opencli，或用 --input 导入已采集 JSON。") from exc
    if result.returncode != 0:
        raise RuntimeError("小红书作品采集失败；请确认 Chrome 已运行、已登录小红书创作者后台，并启用了浏览器采集扩展。")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("小红书作品采集输出不是有效 JSON。") from exc


def refresh_manual_payload(
    manual_payload: dict[str, Any],
    creator_payload: Any,
    *,
    imported_at: str,
    captured_at: str,
) -> tuple[dict[str, Any], int]:
    updated = update_manual_payload(
        manual_payload,
        creator_payload,
        imported_at=imported_at,
        captured_at=captured_at,
    )
    count = len(updated["platforms"]["xiaohongshu"]["contentItems"])
    return updated, count


async def render_xhs_dashboard(platform_fetch_timeout_seconds: float | None = None) -> Path:
    from analytics import derive_dashboard_context, load_history, save_history
    from config import load_env_files, load_settings
    from main import _collect_platform_snapshots, render_dashboard
    from platforms import repair_latest_content_thumbnails

    load_env_files()
    settings = load_settings()
    history = load_history(settings.history_path)
    history = await _collect_platform_snapshots(
        history,
        settings,
        latest_bilibili_snapshot=None,
        live_warnings=[],
        allow_platform_network=True,
        platforms_to_update={"xiaohongshu"},
        platform_fetch_timeout_seconds=platform_fetch_timeout_seconds or settings.platform_fetch_timeout_seconds,
    )
    history = repair_latest_content_thumbnails(history, settings.platform_content_limit)
    history["last_updated"] = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    save_history(history, settings.history_path)
    context = derive_dashboard_context(history, settings, display_warnings=[])
    return render_dashboard(context, settings)


def _load_json(path: str) -> Any:
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _iso_now(timezone_name: str) -> str:
    return datetime.now(ZoneInfo(timezone_name)).isoformat(timespec="seconds")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture, import, and render Xiaohongshu creator note metrics.")
    parser.add_argument("--input", "-i", default="", help="Use an existing creator-notes JSON file, or '-' for stdin.")
    parser.add_argument("--opencli-cmd", default=os.getenv("OPENCLI_CMD", "opencli"))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--manual-path", default=str(DEFAULT_MANUAL_PATH))
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument("--captured-at", default="")
    parser.add_argument("--imported-at", default="")
    parser.add_argument("--skip-render", action="store_true", help="Only update manual cache; do not render dashboard.")
    parser.add_argument("--dry-run", action="store_true", help="Capture/parse and report count without writing files.")
    parser.add_argument("--platform-fetch-timeout", type=float, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    creator_payload = _load_json(args.input) if args.input else capture_creator_notes(opencli_cmd=args.opencli_cmd, limit=args.limit)
    manual_path = Path(args.manual_path)
    manual_payload = json.loads(manual_path.read_text(encoding="utf-8"))
    imported_at = args.imported_at or _iso_now(args.timezone)
    captured_at = args.captured_at or imported_at
    updated, count = refresh_manual_payload(
        manual_payload,
        creator_payload,
        imported_at=imported_at,
        captured_at=captured_at,
    )
    if args.dry_run:
        print(f"识别到 {count} 条小红书创作者后台作品，未写入。")
        return 0
    manual_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已导入 {count} 条小红书创作者后台作品：{manual_path}")
    if not args.skip_render:
        output = asyncio.run(render_xhs_dashboard(args.platform_fetch_timeout))
        print(f"已刷新看板输出：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
