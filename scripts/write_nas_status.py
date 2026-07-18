#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("bilibili", "douyin", "xiaohongshu")
SUCCESS_STATUSES = {"success", "partial", "manual"}
NON_NETWORK_SOURCES = {"", "unknown", "manual_import", "bilibili_cache", "fixture", "unavailable", "failed"}


def _repo_dir() -> Path:
    value = os.getenv("DASHBOARD_REPO_DIR")
    return Path(value).resolve() if value else PROJECT_ROOT


def _repo_path(value: str, repo_dir: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_dir / path


def _relative_repo_path(path: Path, repo_dir: Path) -> str:
    try:
        return path.resolve().relative_to(repo_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def _is_inside_repo(path: Path, repo_dir: Path) -> bool:
    try:
        path.resolve().relative_to(repo_dir.resolve())
    except ValueError:
        return False
    return True


def _git_value(repo_dir: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _file_mtime(path: Path) -> str:
    if not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def _local_time(now_utc: datetime, timezone_name: str) -> str:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return now_utc.astimezone(zone).isoformat(timespec="seconds")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _required_platforms() -> set[str]:
    configured = os.getenv("DASHBOARD_REQUIRED_FRESH_PLATFORMS")
    if configured is not None:
        return {item.strip() for item in configured.split(",") if item.strip() in PLATFORMS}
    required: set[str] = set()
    if _env_flag("BILIBILI_ENABLED", True) and _env_flag("ENABLE_BILIBILI_FETCH", False):
        required.add("bilibili")
    if _env_flag("DOUYIN_ENABLED", True) and any(
        os.getenv(name) for name in ("DOUYIN_DATA_URL", "DOUYIN_OFFICIAL_DATA_URL", "DOUYIN_COOKIE")
    ):
        required.add("douyin")
    if _env_flag("XHS_CREATOR_NOTES_REQUIRED", False):
        required.add("xiaohongshu")
    return required


def _enabled_platforms() -> set[str]:
    names = {
        "bilibili": "BILIBILI_ENABLED",
        "douyin": "DOUYIN_ENABLED",
        "xiaohongshu": "XIAOHONGSHU_ENABLED",
    }
    return {platform for platform, env_name in names.items() if _env_flag(env_name, True)}


def _parse_datetime(value: object, timezone_name: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc)


def _load_history(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _platform_freshness(
    history: dict[str, object],
    *,
    now_utc: datetime,
    timezone_name: str,
) -> tuple[dict[str, dict[str, object]], list[str], str]:
    stale_minutes = max(1, _env_int("DASHBOARD_PLATFORM_STALE_MINUTES", 90))
    required = _required_platforms()
    enabled = _enabled_platforms()
    snapshots = history.get("platform_snapshots")
    rows = snapshots if isinstance(snapshots, list) else []
    result: dict[str, dict[str, object]] = {}
    required_stale: list[str] = []
    any_degraded = False

    for platform in PLATFORMS:
        candidates: list[tuple[datetime, dict[str, object]]] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("platform") != platform:
                continue
            source_status = row.get("sourceStatus")
            status_data = source_status if isinstance(source_status, dict) else {}
            if str(status_data.get("status") or "") not in SUCCESS_STATUSES:
                continue
            captured = _parse_datetime(row.get("capturedAt"), str(row.get("timezone") or timezone_name))
            if captured is not None:
                candidates.append((captured, row))

        latest_time: datetime | None = None
        latest_row: dict[str, object] = {}
        if candidates:
            latest_time, latest_row = max(candidates, key=lambda item: item[0])
        source_status = latest_row.get("sourceStatus")
        status_data = source_status if isinstance(source_status, dict) else {}
        age = max(0, int((now_utc - latest_time).total_seconds() // 60)) if latest_time else -1
        is_enabled = platform in enabled
        is_required = platform in required
        status = str(status_data.get("status") or "missing")
        source = str(status_data.get("source") or "")
        fresh = bool(latest_time and age <= stale_minutes and source not in NON_NETWORK_SOURCES)
        if is_required and not fresh:
            required_stale.append(platform)
        if is_enabled and (not fresh or status != "success"):
            any_degraded = True
        result[platform] = {
            "enabled": is_enabled,
            "required": is_required,
            "fresh": fresh,
            "stale_after_minutes": stale_minutes,
            "age_minutes": age,
            "captured_at": str(latest_row.get("capturedAt") or ""),
            "status": status,
            "source": source,
        }

    quality = "failed" if required_stale else "degraded" if any_degraded else "healthy"
    return result, required_stale, quality


def build_payload(args: argparse.Namespace, repo_dir: Path) -> dict[str, object]:
    now_utc = datetime.now(timezone.utc)
    status_path = _repo_path(os.getenv("DASHBOARD_NAS_STATUS_PATH", "data/nas_status.json"), repo_dir)
    history_path = repo_dir / "data" / "history.json"
    output_path = repo_dir / "dashboard" / "output" / "index.html"
    platform_freshness, required_stale, data_quality_status = _platform_freshness(
        _load_history(history_path),
        now_utc=now_utc,
        timezone_name=args.timezone,
    )
    if args.dashboard_exit_code != 0 or data_quality_status == "failed":
        dashboard_status = "failed"
    elif data_quality_status == "degraded":
        dashboard_status = "degraded"
    else:
        dashboard_status = "success"
    return {
        "schema_version": 1,
        "runner_id": os.getenv("DASHBOARD_NAS_RUNNER_ID", "nas"),
        "source_version": os.getenv("DASHBOARD_SOURCE_VERSION", ""),
        "last_run_at": now_utc.isoformat(timespec="seconds"),
        "last_run_local": _local_time(now_utc, args.timezone),
        "timezone": args.timezone,
        "mode": args.mode,
        "dashboard_status": dashboard_status,
        "dashboard_exit_code": args.dashboard_exit_code,
        "data_quality_status": data_quality_status,
        "required_stale_platforms": required_stale,
        "platform_freshness": platform_freshness,
        "comment_fetch_status": args.comment_fetch_status,
        "comment_render_status": args.comment_render_status,
        "xhs_creator_notes_status": args.xhs_creator_notes_status,
        "tests_status": args.tests_status,
        "publish_status": args.publish_status,
        "git_branch": _git_value(repo_dir, "branch", "--show-current"),
        "git_head": _git_value(repo_dir, "rev-parse", "--short", "HEAD"),
        "history_mtime": _file_mtime(history_path),
        "output_mtime": _file_mtime(output_path),
        "history_path": "data/history.json",
        "output_path": "dashboard/output/index.html",
        "status_path": _relative_repo_path(status_path, repo_dir),
        "public_note": "No credentials, private comment cache, or raw platform responses are stored here.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write the public NAS dashboard heartbeat file.")
    parser.add_argument("--mode", default="live")
    parser.add_argument("--dashboard-exit-code", type=int, default=0)
    parser.add_argument("--timezone", default=os.getenv("DASHBOARD_TIMEZONE", "Asia/Shanghai"))
    parser.add_argument(
        "--comment-fetch-status",
        default="skipped",
        choices=["success", "failed", "skipped"],
    )
    parser.add_argument(
        "--comment-render-status",
        default="skipped",
        choices=["success", "failed", "skipped"],
    )
    parser.add_argument(
        "--xhs-creator-notes-status",
        default="skipped",
        choices=["success", "failed", "skipped"],
    )
    parser.add_argument("--tests-status", default="skipped", choices=["success", "failed", "skipped"])
    parser.add_argument("--publish-status", default="skipped", choices=["success", "failed", "skipped"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_dir = _repo_dir()
    status_path = _repo_path(os.getenv("DASHBOARD_NAS_STATUS_PATH", "data/nas_status.json"), repo_dir)
    if not _is_inside_repo(status_path, repo_dir):
        print("DASHBOARD_NAS_STATUS_PATH must stay inside the repository.", file=sys.stderr)
        return 2
    payload = build_payload(args, repo_dir)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = status_path.with_name(f".{status_path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(status_path)
    print(f"status: {payload['status_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
