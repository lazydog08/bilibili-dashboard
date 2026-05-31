from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_DASHBOARD_URL = "https://lazydog08.github.io/bilibili-dashboard/"
DEFAULT_NAS_STATUS_URL = "https://lazydog08.github.io/bilibili-dashboard/nas_status.json"
DEFAULT_REPAIR_COMMAND = "DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH=1 ./scripts/nas_update_and_push_cloud.sh"
BARK_API_BASE = "https://api.day.app"


@dataclass(frozen=True)
class WatchdogResult:
    ok: bool
    page_updated_at: str = ""
    heartbeat_updated_at: str = ""
    page_age_minutes: int = -1
    heartbeat_age_minutes: int = -1
    reasons: list[str] = field(default_factory=list)


def parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_minutes(updated_at: str, now: datetime) -> int:
    now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    updated = parse_datetime(updated_at)
    return max(0, int((now_aware - updated).total_seconds() // 60))


def extract_page_updated_at(page_html: str) -> str:
    match = re.search(r'data-dashboard-updated="([^"]+)"', page_html)
    return match.group(1) if match else ""


def extract_heartbeat_updated_at(nas_status: dict[str, object]) -> str:
    for key in ("last_run_at", "last_run_local"):
        value = nas_status.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def assess_freshness(
    *,
    page_html: str,
    nas_status: dict[str, object],
    now: datetime,
    max_age_minutes: int,
) -> WatchdogResult:
    reasons: list[str] = []
    page_updated_at = extract_page_updated_at(page_html)
    heartbeat_updated_at = extract_heartbeat_updated_at(nas_status)
    page_age = -1
    heartbeat_age = -1

    if not page_updated_at:
        reasons.append("page_missing_updated_at")
    else:
        try:
            page_age = age_minutes(page_updated_at, now)
            if page_age > max_age_minutes:
                reasons.append("page_stale")
        except ValueError:
            reasons.append("page_invalid_updated_at")

    if not heartbeat_updated_at:
        reasons.append("heartbeat_missing_updated_at")
    else:
        try:
            heartbeat_age = age_minutes(heartbeat_updated_at, now)
            if heartbeat_age > max_age_minutes:
                reasons.append("heartbeat_stale")
        except ValueError:
            reasons.append("heartbeat_invalid_updated_at")

    return WatchdogResult(
        ok=not reasons,
        page_updated_at=page_updated_at,
        heartbeat_updated_at=heartbeat_updated_at,
        page_age_minutes=page_age,
        heartbeat_age_minutes=heartbeat_age,
        reasons=reasons,
    )


def _clean_env_value(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def load_env_files(repo_dir: Path) -> None:
    paths = [
        repo_dir / "data" / "secrets" / "dashboard.env",
        Path.home() / ".config" / "bilibili-dashboard" / "dashboard.env",
    ]
    if os.getenv("DASHBOARD_ENV_FILE"):
        paths.insert(0, Path(os.environ["DASHBOARD_ENV_FILE"]))

    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            if key:
                os.environ.setdefault(key, _clean_env_value(value))


def fetch_text(url: str, timeout_seconds: float) -> str:
    request = Request(url, headers={"User-Agent": "bilibili-dashboard-watchdog/1.0"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8")


def fetch_status(dashboard_url: str, nas_status_url: str, timeout_seconds: float) -> tuple[str, dict[str, object]]:
    page_html = fetch_text(dashboard_url, timeout_seconds)
    status_text = fetch_text(nas_status_url, timeout_seconds)
    payload = json.loads(status_text)
    if not isinstance(payload, dict):
        raise ValueError("nas_status_json_is_not_object")
    return page_html, payload


def build_bark_message(status: str, result: WatchdogResult, repair_summary: str = "") -> tuple[str, str]:
    titles = {
        "normal": "Bilibili 看板更新正常",
        "repaired": "Bilibili 看板已自动修复",
        "failed": "Bilibili 看板自动修复失败",
    }
    title = titles.get(status, "Bilibili 看板自检结果")
    lines = [
        f"状态：{status}",
        f"网页数据：{result.page_updated_at or '未知'}（{result.page_age_minutes} 分钟前）",
        f"NAS 心跳：{result.heartbeat_updated_at or '未知'}（{result.heartbeat_age_minutes} 分钟前）",
    ]
    if result.reasons:
        lines.append(f"异常：{', '.join(result.reasons)}")
    if repair_summary:
        lines.append(f"修复：{repair_summary}")
    return title, "\n".join(lines)


def send_bark(title: str, body: str, timeout_seconds: float) -> str:
    device_key = os.getenv("BARK_DEVICE_KEY", "").strip()
    if not device_key:
        return "Bark skipped: BARK_DEVICE_KEY is not configured."
    group = os.getenv("BARK_GROUP", "数据看板") or "数据看板"
    sound = os.getenv("BARK_SOUND", "minuet") or "minuet"
    url = f"{BARK_API_BASE}/{quote(device_key, safe='')}/{quote(title, safe='')}/{quote(body, safe='')}"
    query = urlencode({"group": group, "sound": sound})
    with urlopen(f"{url}?{query}", timeout=timeout_seconds) as response:
        return f"Bark sent: HTTP {response.status}"


def run_repair(repo_dir: Path, command: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH", "1")
    return subprocess.run(
        command,
        cwd=repo_dir,
        env=env,
        shell=True,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily noon watchdog for the public dashboard and NAS updater.")
    parser.add_argument("--dashboard-url", default=os.getenv("DASHBOARD_WATCHDOG_URL", DEFAULT_DASHBOARD_URL))
    parser.add_argument("--nas-status-url", default=os.getenv("DASHBOARD_WATCHDOG_STATUS_URL", DEFAULT_NAS_STATUS_URL))
    parser.add_argument("--max-age-minutes", type=int, default=int(os.getenv("DASHBOARD_WATCHDOG_MAX_AGE_MINUTES", "90")))
    parser.add_argument("--request-timeout", type=float, default=float(os.getenv("DASHBOARD_WATCHDOG_REQUEST_TIMEOUT", "20")))
    parser.add_argument("--repair-timeout", type=int, default=int(os.getenv("DASHBOARD_WATCHDOG_REPAIR_TIMEOUT", "900")))
    parser.add_argument("--verify-delay", type=int, default=int(os.getenv("DASHBOARD_WATCHDOG_VERIFY_DELAY", "30")))
    parser.add_argument("--repair-command", default=os.getenv("DASHBOARD_WATCHDOG_REPAIR_COMMAND", DEFAULT_REPAIR_COMMAND))
    parser.add_argument("--no-repair", action="store_true")
    parser.add_argument("--no-bark", action="store_true")
    args = parser.parse_args()

    repo_dir = Path(__file__).resolve().parents[1]
    load_env_files(repo_dir)
    now = datetime.now(timezone.utc)

    try:
        page_html, nas_status = fetch_status(args.dashboard_url, args.nas_status_url, args.request_timeout)
        result = assess_freshness(
            page_html=page_html,
            nas_status=nas_status,
            now=now,
            max_age_minutes=args.max_age_minutes,
        )
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        result = WatchdogResult(ok=False, reasons=[f"fetch_failed_{exc.__class__.__name__}"])

    status = "normal"
    repair_summary = ""
    exit_code = 0

    if not result.ok:
        status = "failed"
        if args.no_repair:
            repair_summary = "skipped"
            exit_code = 1
        else:
            repair = run_repair(repo_dir, args.repair_command, args.repair_timeout)
            if repair.returncode != 0:
                repair_summary = f"command_failed_exit_{repair.returncode}"
                exit_code = repair.returncode or 1
            else:
                repair_summary = "repair_command_succeeded"
                time.sleep(max(0, args.verify_delay))
                try:
                    page_html, nas_status = fetch_status(args.dashboard_url, args.nas_status_url, args.request_timeout)
                    result = assess_freshness(
                        page_html=page_html,
                        nas_status=nas_status,
                        now=datetime.now(timezone.utc),
                        max_age_minutes=args.max_age_minutes,
                    )
                    status = "repaired" if result.ok else "failed"
                    exit_code = 0 if result.ok else 1
                except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
                    repair_summary = f"{repair_summary}; verify_failed_{exc.__class__.__name__}"
                    exit_code = 1

    title, body = build_bark_message(status, result, repair_summary)
    print(title)
    print(body)
    if not args.no_bark:
        print(send_bark(title, body, args.request_timeout))
    else:
        print("Bark skipped: disabled by --no-bark.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
