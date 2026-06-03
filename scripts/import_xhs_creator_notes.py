#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANUAL_PATH = PROJECT_ROOT / "data" / "manual_platform_metrics.json"
DEFAULT_TIMEZONE = "Asia/Shanghai"


def _safe_int(value: Any) -> int | None:
    if value in (None, "", "--"):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _first_present(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("rows", "items", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def normalize_creator_note_rows(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _rows_from_payload(payload):
        title = str(row.get("title") or "").strip()
        publish_time = str(_first_present(row, ("published_at", "date", "publish_time", "publishedAt")) or "").strip()
        if not title or not publish_time:
            continue
        note_id = str(_first_present(row, ("id", "note_id", "noteId")) or "").strip()
        item = {
            "id": note_id,
            "note_id": note_id,
            "title": title,
            "publish_time": publish_time,
            "views": _safe_int(_first_present(row, ("views", "read_count", "观看数", "阅读量"))),
            "likes": _safe_int(_first_present(row, ("likes", "like_count", "点赞数"))),
            "favorites": _safe_int(_first_present(row, ("favorites", "collects", "collect_count", "fav_count", "收藏数"))),
            "comments": _safe_int(_first_present(row, ("comments", "comment_count", "评论数"))),
            "shares": _safe_int(_first_present(row, ("shares", "share_count", "分享数"))),
            "url": str(row.get("url") or ""),
            "data_source": "小红书创作者后台导入",
            "metric_scope": "当前后台作品列表",
        }
        avg_view_time = _first_present(row, ("avg_view_time", "avd", "平均观看时长"))
        if avg_view_time not in (None, ""):
            item["avd"] = str(avg_view_time)
        rows.append(item)
    return rows


def update_manual_payload(
    payload: dict[str, Any],
    creator_rows: Any,
    *,
    imported_at: str,
    captured_at: str,
) -> dict[str, Any]:
    normalized_rows = normalize_creator_note_rows(creator_rows)
    if not normalized_rows:
        raise ValueError("没有识别到可导入的小红书作品行。")

    updated = deepcopy(payload)
    platforms = updated.setdefault("platforms", {})
    if not isinstance(platforms, dict):
        raise ValueError("manual 平台数据格式不正确：platforms 不是对象。")
    xhs = platforms.setdefault("xiaohongshu", {})
    if not isinstance(xhs, dict):
        raise ValueError("manual 平台数据格式不正确：xiaohongshu 不是对象。")

    xhs.setdefault("source", "manual_import")
    xhs["capturedAt"] = captured_at
    xhs["importedAt"] = imported_at
    xhs["contentItems"] = normalized_rows
    source_status = xhs.get("sourceStatus") if isinstance(xhs.get("sourceStatus"), dict) else {}
    source_status["status"] = "manual"
    source_status["source"] = "manual_import"
    source_status["capturedAt"] = captured_at
    source_status["importedAt"] = imported_at
    source_status["message"] = f"已导入 {len(normalized_rows)} 条小红书创作者后台作品；汇总指标仍以当前授权后台为准。"
    xhs["sourceStatus"] = source_status
    return updated


def _load_json_input(path: str) -> Any:
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _iso_now(timezone_name: str) -> str:
    return datetime.now(ZoneInfo(timezone_name)).isoformat(timespec="seconds")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Xiaohongshu creator note rows into manual platform cache.")
    parser.add_argument("--input", "-i", required=True, help="OpenCLI creator-notes JSON file, or '-' for stdin.")
    parser.add_argument(
        "--manual-path",
        default=str(DEFAULT_MANUAL_PATH),
        help="Path to data/manual_platform_metrics.json.",
    )
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument("--captured-at", default="")
    parser.add_argument("--imported-at", default="")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing the manual cache file.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manual_path = Path(args.manual_path)
    payload = json.loads(manual_path.read_text(encoding="utf-8"))
    creator_payload = _load_json_input(args.input)
    imported_at = args.imported_at or _iso_now(args.timezone)
    captured_at = args.captured_at or imported_at
    updated = update_manual_payload(payload, creator_payload, imported_at=imported_at, captured_at=captured_at)
    count = len(updated["platforms"]["xiaohongshu"]["contentItems"])
    if args.dry_run:
        print(f"识别到 {count} 条小红书创作者后台作品，未写入。")
        return 0
    manual_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {count} 条小红书创作者后台作品：{manual_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
