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


def build_payload(args: argparse.Namespace, repo_dir: Path) -> dict[str, object]:
    now_utc = datetime.now(timezone.utc)
    status_path = _repo_path(os.getenv("DASHBOARD_NAS_STATUS_PATH", "data/nas_status.json"), repo_dir)
    history_path = repo_dir / "data" / "history.json"
    output_path = repo_dir / "dashboard" / "output" / "index.html"
    dashboard_status = "success" if args.dashboard_exit_code == 0 else "failed"
    return {
        "schema_version": 1,
        "runner_id": os.getenv("DASHBOARD_NAS_RUNNER_ID", "nas"),
        "last_run_at": now_utc.isoformat(timespec="seconds"),
        "last_run_local": _local_time(now_utc, args.timezone),
        "timezone": args.timezone,
        "mode": args.mode,
        "dashboard_status": dashboard_status,
        "dashboard_exit_code": args.dashboard_exit_code,
        "comment_fetch_status": args.comment_fetch_status,
        "comment_render_status": args.comment_render_status,
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
