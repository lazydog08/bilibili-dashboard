from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import datetime, time, timedelta
from difflib import SequenceMatcher
from html import escape as escape_html
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from analytics import (
    DEFAULT_TIMEZONE,
    format_number,
    normalize_thumbnail_url,
    now_shanghai,
    parse_publish_time,
    safe_int,
    safe_minutes,
    safe_ratio,
)
from health import build_operational_status


PLATFORM_META = {
    "bilibili": {
        "name": "B 站",
        "logo": "B",
        "accent": "#00a1d6",
        "icon": "https://www.bilibili.com/favicon.ico",
    },
    "douyin": {
        "name": "抖音",
        "logo": "抖",
        "accent": "#31f5ff",
        "icon": "https://lf1-cdn-tos.bytegoofy.com/goofy/ies/douyin_web/public/favicon.ico",
    },
    "xiaohongshu": {
        "name": "小红书",
        "logo": "红",
        "accent": "#ff2442",
        "icon": "https://www.xiaohongshu.com/favicon.ico",
    },
}

COMMON_METRICS = [
    ("views", "播放量 / 阅读量"),
    ("likes", "点赞数"),
    ("favorites", "收藏数"),
    ("comments", "评论数"),
    ("shares", "分享 / 转发数"),
]

CUSTOM_METRICS = {
    "bilibili": [
        ("coins", "投币数"),
        ("danmaku", "弹幕数"),
    ],
    "douyin": [
        ("profile_visits", "主页访问量"),
        ("cover_click_ratio", "封面点击率（%）"),
        ("completion_rate", "完播率"),
    ],
    "xiaohongshu": [
        ("note_impressions", "笔记曝光量"),
        ("search_entries", "搜索进入量"),
        ("cover_click_rate", "封面点击率（%）"),
        ("avg_view_time", "平均观看时长（秒）"),
        ("completion_rate", "完播率（%）"),
        ("profile_visits", "主页访问量"),
    ],
}

SUCCESS_STATUSES = {"success", "partial", "manual", "fixture", "cache"}
FIELD_STATUSES = {"available", "unavailable", "missing", "failed"}
UNAVAILABLE = {
    "label": "--",
    "raw": None,
    "class": "is-unavailable",
    "available": False,
}


def _tz(name: str | None = None) -> ZoneInfo:
    return ZoneInfo(name or DEFAULT_TIMEZONE)


def _parse_dt(value: Any, timezone_name: str = DEFAULT_TIMEZONE) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return now_shanghai()
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            date_text = parse_publish_time(text)
            parsed = datetime.combine(datetime.fromisoformat(date_text).date(), time.min)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_tz(timezone_name))
    return parsed.astimezone(_tz(timezone_name))


def _iso_now(timezone_name: str = DEFAULT_TIMEZONE) -> str:
    return datetime.now(_tz(timezone_name)).isoformat(timespec="seconds")


def _raw_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value and (
        "status" in value or "source" in value
    ):
        return value.get("value")
    return value


def _field_source(value: Any, fallback: str = "") -> str:
    if isinstance(value, dict) and "value" in value and ("status" in value or "source" in value):
        return str(value.get("source") or fallback or "")
    return fallback


def _field_status_from_value(value: Any, fallback: str = "missing") -> str:
    if isinstance(value, dict) and "value" in value and ("status" in value or "source" in value):
        status = str(value.get("status") or fallback)
        return status if status in FIELD_STATUSES else fallback
    return fallback


def _optional_int(value: Any) -> int | None:
    value = _raw_value(value)
    if value in (None, "", "--"):
        return None
    try:
        number = int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_number(value: Any) -> int | float | None:
    value = _raw_value(value)
    if value in (None, "", "--"):
        return None
    try:
        number = float(str(value).replace(",", "").strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if abs(number - round(number)) < 0.000001:
        return int(round(number))
    return number


def _field(
    value: Any,
    *,
    source: str = "",
    status: str | None = None,
    missing_status: str = "missing",
) -> dict[str, Any]:
    source = _field_source(value, source)
    explicit_status = status or _field_status_from_value(value, "")
    number = _optional_number(value)
    if number is None:
        final_status = explicit_status or missing_status
    else:
        final_status = explicit_status or "available"
    if final_status not in FIELD_STATUSES:
        final_status = "missing" if number is None else "available"
    return {
        "value": number,
        "status": final_status,
        "source": source or "unknown",
    }


def _raw_summary(raw: dict[str, Any] | None, source: str = "") -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"summary": {"source": source}} if source else {}
    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else raw
    blocked = {
        "cookie",
        "cookies",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "app_secret",
        "device_key",
        "authorization",
        "headers",
        "response",
        "payload",
    }
    safe: dict[str, Any] = {}
    for key, value in summary.items():
        key_text = str(key)
        lowered = key_text.lower()
        if any(item in lowered for item in blocked):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key_text] = value
        elif isinstance(value, (list, tuple, set)):
            safe[key_text] = len(value)
        elif isinstance(value, dict):
            safe[key_text] = {"keys": len(value)}
    if source:
        safe.setdefault("source", source)
    return {"summary": safe}


def _metric_value(snapshot: dict[str, Any], key: str) -> int | float | None:
    metrics = snapshot.get("metrics", {})
    custom = snapshot.get("customMetrics", {})
    if isinstance(metrics, dict) and key in metrics:
        return _optional_number(metrics.get(key))
    if isinstance(custom, dict) and key in custom:
        return _optional_number(custom.get(key))
    return None


def _daily_metric_values(snapshot: dict[str, Any], key: str) -> tuple[int | None, int | None] | None:
    for container_name in ("dailyMetrics", "customDailyMetrics"):
        container = snapshot.get(container_name, {})
        if not isinstance(container, dict) or key not in container:
            continue
        item = container.get(key)
        if isinstance(item, dict):
            return _optional_number(item.get("today")), _optional_number(item.get("yesterday"))
    return None


def _status(snapshot: dict[str, Any]) -> str:
    source_status = snapshot.get("sourceStatus", {})
    if isinstance(source_status, dict):
        return str(source_status.get("status") or "success")
    return "success"


def _status_message(snapshot: dict[str, Any]) -> str:
    if not isinstance(snapshot, dict):
        return ""
    source_status = snapshot.get("sourceStatus", {})
    if isinstance(source_status, dict):
        return str(source_status.get("message") or "")
    return ""


def _is_success(snapshot: dict[str, Any]) -> bool:
    return _status(snapshot) in SUCCESS_STATUSES


def _fmt_value(value: Any) -> dict[str, Any]:
    number = _optional_number(value)
    if number is None:
        return deepcopy(UNAVAILABLE)
    return {
        "label": format_number(number),
        "raw": number,
        "class": "is-neutral",
        "available": True,
    }


def _fmt_delta(value: int | float | None) -> dict[str, Any]:
    if value is None:
        return deepcopy(UNAVAILABLE)
    number = float(value)
    class_name = "is-positive" if number > 0 else "is-negative" if number < 0 else "is-neutral"
    sign = "+" if number > 0 else ""
    return {
        "label": f"{sign}{format_number(number)}",
        "raw": number,
        "class": class_name,
        "available": True,
    }


def _fmt_percent(value: float | None) -> dict[str, Any]:
    if value is None or not math.isfinite(value):
        return deepcopy(UNAVAILABLE)
    class_name = "is-positive" if value > 0 else "is-negative" if value < 0 else "is-neutral"
    sign = "+" if value > 0 else ""
    return {
        "label": f"{sign}{value:.1f}%",
        "raw": value,
        "class": class_name,
        "available": True,
    }


def build_platform_snapshot(
    *,
    platform: str,
    account_id: str = "",
    captured_at: str | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    fans: Any = None,
    metrics: dict[str, Any] | None = None,
    custom_metrics: dict[str, Any] | None = None,
    daily_metrics: dict[str, Any] | None = None,
    custom_daily_metrics: dict[str, Any] | None = None,
    manual_growth: dict[str, Any] | None = None,
    metric_columns: dict[str, str] | None = None,
    content_items: list[dict[str, Any]] | None = None,
    status: str = "success",
    message: str = "",
    source: str = "unknown",
    imported_at: str | None = None,
    missing_status: str = "missing",
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    captured = captured_at or _iso_now(timezone_name)
    source_status = {
        "status": status,
        "message": message,
        "source": source,
    }
    if imported_at:
        source_status["importedAt"] = imported_at
    return {
        "platform": platform,
        "accountId": account_id,
        "capturedAt": captured,
        "date": _parse_dt(captured, timezone_name).date().isoformat(),
        "timezone": timezone_name,
        "fans": _field(fans, source=source, missing_status=missing_status),
        "metrics": {
            key: _field(value, source=source, missing_status=missing_status)
            for key, value in (metrics or {}).items()
        },
        "customMetrics": {
            key: _field(value, source=source, missing_status=missing_status)
            for key, value in (custom_metrics or {}).items()
        },
        "dailyMetrics": _normalize_daily_metrics(daily_metrics or {}, source, missing_status),
        "customDailyMetrics": _normalize_daily_metrics(custom_daily_metrics or {}, source, missing_status),
        "manualGrowth": {
            key: _field(value, source=source, missing_status=missing_status)
            for key, value in (manual_growth or {}).items()
        },
        "metricColumns": metric_columns or {"current": "今日", "previous": "昨日"},
        "contentItems": _normalize_content_items(content_items or []),
        "sourceStatus": source_status,
        "raw": _raw_summary(raw, source),
    }


def _normalize_daily_metrics(
    values: dict[str, Any],
    source: str = "unknown",
    missing_status: str = "missing",
) -> dict[str, dict[str, dict[str, Any]]]:
    normalized: dict[str, dict[str, dict[str, Any]]] = {}
    for key, value in values.items():
        if isinstance(value, dict):
            normalized[key] = {
                "today": _field(value.get("today"), source=source, missing_status=missing_status),
                "yesterday": _field(value.get("yesterday"), source=source, missing_status=missing_status),
            }
        else:
            normalized[key] = {
                "today": _field(value, source=source, missing_status=missing_status),
                "yesterday": _field(None, source=source, missing_status=missing_status),
            }
    return normalized


def _normalize_content_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return merge_content_items(items, content_limit=60)


def _has_any_number(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_any_number(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_any_number(item) for item in value)
    return _optional_int(value) is not None


def _daily_from_today_yesterday(today: dict[str, Any] | None, yesterday: dict[str, Any] | None) -> dict[str, Any]:
    today = today if isinstance(today, dict) else {}
    yesterday = yesterday if isinstance(yesterday, dict) else {}
    keys = set(today) | set(yesterday)
    return {
        key: {
            "today": today.get(key),
            "yesterday": yesterday.get(key),
        }
        for key in keys
    }


def _snapshot_from_manual_record(
    platform: str,
    record: dict[str, Any],
    default_captured_at: str | None,
    default_imported_at: str | None,
    timezone_name: str,
) -> dict[str, Any] | None:
    if platform not in PLATFORM_META or not isinstance(record, dict):
        return None
    payload = {
        "fans": record.get("fans"),
        "metrics": record.get("metrics") if isinstance(record.get("metrics"), dict) else {},
        "customMetrics": record.get("customMetrics") if isinstance(record.get("customMetrics"), dict) else {},
        "dailyMetrics": record.get("dailyMetrics") if isinstance(record.get("dailyMetrics"), dict) else {},
        "customDailyMetrics": record.get("customDailyMetrics") if isinstance(record.get("customDailyMetrics"), dict) else {},
        "manualGrowth": record.get("growth") if isinstance(record.get("growth"), dict) else {},
        "contentItems": record.get("contentItems") if isinstance(record.get("contentItems"), list) else [],
    }
    payload["dailyMetrics"] = {
        **payload["dailyMetrics"],
        **_daily_from_today_yesterday(
            record.get("today") if isinstance(record.get("today"), dict) else {},
            record.get("yesterday") if isinstance(record.get("yesterday"), dict) else {},
        ),
    }
    payload["customDailyMetrics"] = {
        **payload["customDailyMetrics"],
        **_daily_from_today_yesterday(
            record.get("customToday") if isinstance(record.get("customToday"), dict) else {},
            record.get("customYesterday") if isinstance(record.get("customYesterday"), dict) else {},
        ),
    }
    if not _has_any_number(payload):
        return None
    source_status = record.get("sourceStatus") if isinstance(record.get("sourceStatus"), dict) else {}
    metric_columns = record.get("metricColumns") if isinstance(record.get("metricColumns"), dict) else None
    imported_at = str(record.get("importedAt") or default_imported_at or _iso_now(timezone_name))
    source = str(record.get("source") or source_status.get("source") or "manual_import")
    return build_platform_snapshot(
        platform=platform,
        account_id=str(record.get("accountId") or ""),
        captured_at=str(record.get("capturedAt") or default_captured_at or _iso_now(timezone_name)),
        timezone_name=str(record.get("timezone") or timezone_name),
        fans=payload["fans"],
        metrics=payload["metrics"],
        custom_metrics=payload["customMetrics"],
        daily_metrics=payload["dailyMetrics"],
        custom_daily_metrics=payload["customDailyMetrics"],
        manual_growth=payload["manualGrowth"],
        metric_columns=metric_columns,
        content_items=payload["contentItems"],
        status=str(source_status.get("status") or record.get("status") or "manual"),
        message=str(source_status.get("message") or record.get("message") or "手动导入真实后台数据。"),
        source=source,
        imported_at=imported_at,
        raw={"summary": {"source": source, "content_count": len(payload["contentItems"])}},
    )


def load_manual_platform_snapshots(path: str | Path, timezone_name: str = DEFAULT_TIMEZONE) -> list[dict[str, Any]]:
    manual_path = Path(path)
    if not manual_path.exists():
        return []
    try:
        payload = json.loads(manual_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    default_captured_at = str(payload.get("capturedAt") or "") if isinstance(payload, dict) else ""
    default_imported_at = str(payload.get("importedAt") or "") if isinstance(payload, dict) else ""
    records: list[tuple[str, dict[str, Any]]] = []
    if isinstance(payload, dict) and isinstance(payload.get("platforms"), dict):
        for platform, record in payload["platforms"].items():
            if isinstance(record, dict):
                records.append((str(platform), record))
    if isinstance(payload, dict) and isinstance(payload.get("snapshots"), list):
        for record in payload["snapshots"]:
            if isinstance(record, dict):
                records.append((str(record.get("platform") or ""), record))
    result: list[dict[str, Any]] = []
    for platform, record in records:
        snapshot = _snapshot_from_manual_record(platform, record, default_captured_at, default_imported_at, timezone_name)
        if snapshot:
            result.append(snapshot)
    return result


def platform_snapshot_from_bilibili(
    snapshot: dict[str, Any],
    account_id: str = "",
    timezone_name: str = DEFAULT_TIMEZONE,
    status: str | None = None,
) -> dict[str, Any]:
    channel = snapshot.get("channel", {}) if isinstance(snapshot.get("channel"), dict) else {}
    warnings = snapshot.get("warnings", []) if isinstance(snapshot.get("warnings"), list) else []
    source_status = status or ("partial" if warnings else str(snapshot.get("source") or "success"))
    if source_status == "live":
        source_status = "success"
    if source_status == "manual":
        source_status = "manual"
    return build_platform_snapshot(
        platform="bilibili",
        account_id=account_id,
        captured_at=str(snapshot.get("updated_at") or snapshot.get("date") or _iso_now(timezone_name)),
        timezone_name=timezone_name,
        fans=channel.get("total_followers"),
        metrics={
            "views": channel.get("total_views"),
            "likes": channel.get("total_likes"),
            "favorites": channel.get("total_favorites"),
            "comments": channel.get("total_replies"),
            "shares": channel.get("total_shares"),
        },
        custom_metrics={
            "coins": channel.get("total_coins"),
            "danmaku": channel.get("total_danmaku"),
        },
        manual_growth={"7d": channel.get("follower_delta_7d")},
        metric_columns={"current": "当前累计", "previous": "上次累计"},
        status=source_status if source_status in SUCCESS_STATUSES else "success",
        message="; ".join(str(item) for item in warnings[:3]),
        source="bilibili_live" if str(snapshot.get("source")) == "live" else "bilibili_cache",
        raw={"summary": {"videos_count": len(snapshot.get("videos", []) or [])}},
    )


def _fmt_content_value(value: Any) -> str:
    value = _raw_value(value)
    if value in (None, "", "--"):
        return "--"
    if isinstance(value, str):
        return value.strip() or "--"
    return format_number(value)


def _optional_float(value: Any) -> float | None:
    value = _raw_value(value)
    if value in (None, "", "--"):
        return None
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        if text.endswith("%"):
            return float(text[:-1]) / 100.0
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ratio_percent(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    parsed = safe_ratio(value, 0.0)
    if parsed == 0.0 and str(value).strip() not in {"0", "0.0", "0%"}:
        return None
    return round(parsed * 100.0, 2)


def _duration_seconds(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    minutes = safe_minutes(value, 0.0)
    if minutes == 0.0 and str(value).strip() not in {"0", "0.0", "0秒"}:
        return None
    return round(minutes * 60.0, 2)


def _short_content_title(title: str, limit: int = 30) -> str:
    clean = " ".join(str(title or "未命名内容").split())
    return clean if len(clean) <= limit else f"{clean[:limit]}…"


def _clean_content_title(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = " ".join(text.split())
    if not text:
        return "未命名内容"
    without_tags = re.sub(r"\s*#[^\s#]+", "", text).strip()
    without_tags = re.sub(r"\s+", " ", without_tags)
    if len(without_tags) >= 6:
        return without_tags
    return text


def _matchable_title(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"#[^\s#]+", "", text)
    text = re.sub(r"[\s，。！？、,.!?：:；;【】\\[\\]（）()《》\"'“”‘’…·\\-_/|]+", "", text)
    return text


def _is_generated_thumbnail(value: Any) -> bool:
    return str(value or "").startswith("data:image/svg+xml")


def _svg_title_lines(title: str, *, max_lines: int = 6, max_units: float = 15.0) -> list[str]:
    text = " ".join(str(title or "未命名内容").split())
    lines: list[str] = []
    current = ""
    units = 0.0
    for char in text:
        char_units = 1.0 if ord(char) > 127 else 0.58
        if current and units + char_units > max_units:
            lines.append(current)
            current = ""
            units = 0.0
            if len(lines) >= max_lines:
                break
        current += char
        units += char_units
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len("".join(lines)) < len(text):
        lines[-1] = f"{lines[-1].rstrip('…')}…"
    return lines or ["未命名内容"]


def _generated_content_thumbnail(title: Any, platform: str) -> str:
    clean_title = _clean_content_title(title)
    meta = PLATFORM_META.get(platform, {})
    platform_name = str(meta.get("name") or platform or "内容")
    accent = str(meta.get("accent") or "#e45532")
    lines = _svg_title_lines(clean_title)
    tspans = []
    for index, line in enumerate(lines):
        dy = "0" if index == 0 else "1.28em"
        tspans.append(f'<tspan x="72" dy="{dy}">{escape_html(line)}</tspan>')
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 1200">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#2f2f2f"/>
      <stop offset="1" stop-color="#171717"/>
    </linearGradient>
    <radialGradient id="glow" cx="24%" cy="18%" r="62%">
      <stop offset="0" stop-color="{escape_html(accent)}" stop-opacity="0.42"/>
      <stop offset="1" stop-color="{escape_html(accent)}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="900" height="1200" rx="48" fill="url(#bg)"/>
  <rect width="900" height="1200" rx="48" fill="url(#glow)"/>
  <rect x="48" y="48" width="804" height="1104" rx="36" fill="none" stroke="{escape_html(accent)}" stroke-opacity="0.35" stroke-width="3"/>
  <text x="72" y="116" fill="{escape_html(accent)}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei,sans-serif" font-size="34" font-weight="800">{escape_html(platform_name)}作品封面</text>
  <text x="72" y="480" fill="#f5f5f5" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei,sans-serif" font-size="58" font-weight="900" line-height="1.28">{''.join(tspans)}</text>
  <text x="72" y="1092" fill="#b8b8b8" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei,sans-serif" font-size="28">后台未返回封面，已用本地占位保证可读</text>
</svg>"""
    return f"data:image/svg+xml;charset=UTF-8,{quote(svg, safe='')}"


def _content_identifier(item: dict[str, Any]) -> str:
    for key in ("id", "item_id", "aweme_id", "note_id", "bvid"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return ""


def _content_datetime(value: Any, timezone_name: str = DEFAULT_TIMEZONE) -> datetime | None:
    if value in (None, "", "--"):
        return None
    tz = _tz(timezone_name)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000.0
        parsed = datetime.fromtimestamp(timestamp, tz=tz)
    else:
        text = str(value or "").strip()
        if not text or text == "--":
            return None
        if re.fullmatch(r"\d{10,13}", text):
            timestamp = float(text)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000.0
            parsed = datetime.fromtimestamp(timestamp, tz=tz)
        else:
            normalized = (
                text.replace("年", "-")
                .replace("月", "-")
                .replace("日", " ")
                .replace("/", "-")
                .strip()
            )
            normalized = re.sub(r"\s+", " ", normalized)
            parsed = None
            for pattern in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d %H",
                "%Y-%m-%d",
            ):
                try:
                    parsed = datetime.strptime(normalized, pattern)
                    break
                except ValueError:
                    continue
            if parsed is None:
                if not re.search(r"\d{4}", text):
                    return None
                try:
                    parsed = datetime.fromisoformat(normalized)
                except ValueError:
                    try:
                        date_text = parse_publish_time(text)
                        parsed = datetime.combine(datetime.fromisoformat(date_text).date(), time.min)
                    except Exception:  # noqa: BLE001 - malformed publish time should not break rendering.
                        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _content_order_key(item: dict[str, Any]) -> tuple[int, float, int]:
    parsed = _content_datetime(item.get("publish_time") or item.get("publishedAt"))
    return (1 if parsed else 0, parsed.timestamp() if parsed else 0.0, _optional_int(item.get("views")) or 0)


def _content_value_present(value: Any) -> bool:
    value = _raw_value(value)
    if value in (None, "", "--"):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _content_metric_score(item: dict[str, Any]) -> int:
    keys = (
        "thumbnail",
        "views",
        "likes",
        "favorites",
        "comments",
        "shares",
        "ctr",
        "avd",
        "avp",
        "danmaku",
    )
    return sum(1 for key in keys if _content_value_present(item.get(key)))


def _publish_dates_close(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_dt = _content_datetime(left.get("publish_time"))
    right_dt = _content_datetime(right.get("publish_time"))
    if not left_dt or not right_dt:
        return True
    return abs((left_dt.date() - right_dt.date()).days) <= 2


def _same_content_item(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_id = _content_identifier(left)
    right_id = _content_identifier(right)
    if left_id and right_id:
        return left_id == right_id
    left_title = _matchable_title(left.get("title"))
    right_title = _matchable_title(right.get("title"))
    if len(left_title) < 8 or len(right_title) < 8:
        return False
    if not _publish_dates_close(left, right):
        return False
    shortest = min(len(left_title), len(right_title))
    if left_title[:shortest] in right_title or right_title[:shortest] in left_title:
        return True
    matcher = SequenceMatcher(None, left_title, right_title)
    longest = matcher.find_longest_match(0, len(left_title), 0, len(right_title)).size
    return longest >= 12 or matcher.ratio() >= 0.58


def _better_content_title(left: str, right: str) -> str:
    left_clean = _clean_content_title(left)
    right_clean = _clean_content_title(right)

    def score(title: str) -> tuple[int, int, int]:
        tag_penalty = title.count("#")
        truncated_penalty = 1 if "…" in title or "..." in title else 0
        length_penalty = abs(len(title) - 32)
        return (tag_penalty, truncated_penalty, length_penalty)

    return left_clean if score(left_clean) <= score(right_clean) else right_clean


def _more_precise_publish_time(left: Any, right: Any) -> str:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or left_text == "--":
        return right_text or "--"
    if not right_text or right_text == "--":
        return left_text
    left_has_time = bool(re.search(r"\d{1,2}:\d{2}", left_text))
    right_has_time = bool(re.search(r"\d{1,2}:\d{2}", right_text))
    if right_has_time and not left_has_time:
        return right_text
    return left_text


def _normalize_content_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "title": _clean_content_title(item.get("title") or "未命名内容"),
        "publish_time": str(item.get("publish_time") or item.get("publishedAt") or "--"),
        "thumbnail": normalize_thumbnail_url(item.get("thumbnail")),
        "views": item.get("views"),
        "likes": item.get("likes"),
        "favorites": item.get("favorites"),
        "comments": item.get("comments"),
        "shares": item.get("shares"),
        "ctr": item.get("ctr"),
        "avd": item.get("avd"),
        "avp": item.get("avp"),
        "danmaku": item.get("danmaku"),
        "thumbnail_note": item.get("thumbnail_note"),
    }
    for key in ("id", "item_id", "aweme_id", "note_id", "bvid"):
        if item.get(key):
            normalized[key] = item.get(key)
    return normalized


def _merge_content_pair(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    merged["title"] = _better_content_title(merged.get("title", ""), incoming.get("title", ""))
    merged["publish_time"] = _more_precise_publish_time(merged.get("publish_time"), incoming.get("publish_time"))
    if not merged.get("thumbnail") and incoming.get("thumbnail"):
        merged["thumbnail"] = incoming.get("thumbnail")
    elif (
        incoming.get("thumbnail")
        and _content_metric_score(incoming) > _content_metric_score(merged)
        and str(merged.get("thumbnail") or "").startswith("data:")
    ):
        merged["thumbnail"] = incoming.get("thumbnail")
    for key in (
        "views",
        "likes",
        "favorites",
        "comments",
        "shares",
        "ctr",
        "avd",
        "avp",
        "danmaku",
        "thumbnail_note",
        "id",
        "item_id",
        "aweme_id",
        "note_id",
        "bvid",
    ):
        if not _content_value_present(merged.get(key)) and _content_value_present(incoming.get(key)):
            merged[key] = incoming.get(key)
    return merged


def merge_content_items(
    primary_items: list[dict[str, Any]] | None,
    fallback_items: list[dict[str, Any]] | None = None,
    *,
    content_limit: int = 60,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    original_order: list[int] = []
    for item in list(primary_items or []) + list(fallback_items or []):
        if not isinstance(item, dict):
            continue
        normalized = _normalize_content_item(item)
        matched_index = None
        for index, existing in enumerate(merged):
            if _same_content_item(existing, normalized):
                matched_index = index
                break
        if matched_index is None:
            merged.append(normalized)
            original_order.append(len(original_order))
            continue
        merged[matched_index] = _merge_content_pair(merged[matched_index], normalized)

    indexed = list(enumerate(merged))
    indexed.sort(
        key=lambda pair: (
            _content_order_key(pair[1])[0],
            _content_order_key(pair[1])[1],
            -original_order[pair[0]],
        ),
        reverse=True,
    )
    return [item for _, item in indexed[:content_limit]]


def _thumbnail_candidates(history: dict[str, Any], current_platform: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    latest_content = history.get("latest_content", {})
    if isinstance(latest_content, dict):
        for platform, cache in latest_content.items():
            if not isinstance(cache, dict):
                continue
            items = cache.get("items", [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = _matchable_title(item.get("title"))
                thumbnail = normalize_thumbnail_url(item.get("thumbnail"))
                if title and thumbnail and not _is_generated_thumbnail(thumbnail):
                    candidates.append((title, thumbnail))
    snapshots = history.get("snapshots", [])
    if not isinstance(snapshots, list):
        return candidates
    for snapshot in snapshots[-3:]:
        videos = snapshot.get("videos", []) if isinstance(snapshot, dict) else []
        if not isinstance(videos, list):
            continue
        for video in videos:
            if not isinstance(video, dict):
                continue
            title = _matchable_title(video.get("title"))
            thumbnail = normalize_thumbnail_url(video.get("thumbnail"))
            if title and thumbnail and not _is_generated_thumbnail(thumbnail):
                candidates.append((title, thumbnail))
    return candidates


def _fill_missing_thumbnails_from_related_content(
    history: dict[str, Any],
    platform: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not items:
        return items
    candidates = _thumbnail_candidates(history, platform)
    filled: list[dict[str, Any]] = []
    for item in items:
        item_copy = deepcopy(item)
        needs_thumbnail = not item_copy.get("thumbnail") or _is_generated_thumbnail(item_copy.get("thumbnail"))
        if needs_thumbnail and candidates:
            title = _matchable_title(item_copy.get("title"))
            if len(title) >= 8:
                for candidate_title, thumbnail in candidates:
                    shortest = min(len(title), len(candidate_title))
                    similarity = SequenceMatcher(None, title, candidate_title).ratio()
                    longest = SequenceMatcher(None, title, candidate_title).find_longest_match(
                        0,
                        len(title),
                        0,
                        len(candidate_title),
                    ).size
                    if shortest >= 8 and (
                        title[:shortest] in candidate_title
                        or candidate_title[:shortest] in title
                        or (longest >= 8 and similarity >= 0.42)
                    ):
                        item_copy["thumbnail"] = thumbnail
                        item_copy["thumbnail_note"] = "同标题内容封面兜底"
                        break
        if (not item_copy.get("thumbnail") or _is_generated_thumbnail(item_copy.get("thumbnail"))) and platform in {
            "douyin",
            "xiaohongshu",
        }:
            item_copy["thumbnail"] = _generated_content_thumbnail(item_copy.get("title"), platform)
            item_copy["thumbnail_note"] = item_copy.get("thumbnail_note") or "自动生成封面占位"
        filled.append(item_copy)
    return filled


def _latest_content_items(history: dict[str, Any], platform: str, snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    items = []
    if snapshot and isinstance(snapshot.get("contentItems"), list):
        items = snapshot.get("contentItems", [])
    if not items:
        cache = history.get("latest_content", {})
        if isinstance(cache, dict):
            platform_cache = cache.get(platform, {})
            if isinstance(platform_cache, dict) and isinstance(platform_cache.get("items"), list):
                items = platform_cache.get("items", [])
    clean_items = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    filled = _fill_missing_thumbnails_from_related_content(history, platform, clean_items)
    return merge_content_items(filled, content_limit=60)


def _content_items(history: dict[str, Any], platform: str, snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    items = _latest_content_items(history, platform, snapshot)
    if not isinstance(items, list):
        return []
    prepared = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "未命名内容").strip()
        prepared.append(
            {
                "title": title,
                "short_title": _short_content_title(title),
                "publish_time": str(item.get("publish_time") or "--"),
                "thumbnail": str(item.get("thumbnail") or ""),
                "thumbnail_note": str(item.get("thumbnail_note") or ""),
                "metrics": [
                    {"label": "播放 / 阅读", "value": _fmt_content_value(item.get("views"))},
                    {"label": "点赞", "value": _fmt_content_value(item.get("likes"))},
                    {"label": "收藏", "value": _fmt_content_value(item.get("favorites"))},
                    {"label": "评论", "value": _fmt_content_value(item.get("comments"))},
                    {"label": "分享 / 转发", "value": _fmt_content_value(item.get("shares"))},
                    {"label": "CTR", "value": _fmt_content_value(item.get("ctr"))},
                    {"label": "平均播放时长", "value": _fmt_content_value(item.get("avd"))},
                    {"label": "完播率", "value": _fmt_content_value(item.get("avp"))},
                    {"label": "弹幕", "value": _fmt_content_value(item.get("danmaku"))},
                ],
            }
        )
    return prepared


def _content_chart(history: dict[str, Any], snapshot: dict[str, Any] | None, platform: str) -> dict[str, Any]:
    items = _latest_content_items(history, platform, snapshot)
    if not items:
        return {
            "labels": [],
            "full_titles": [],
            "views": [],
            "likes": [],
            "favorites": [],
            "comments": [],
            "shares": [],
            "ctr": [],
            "avd_seconds": [],
            "danmaku": [],
            "metric_label": "阅读量" if platform == "xiaohongshu" else "播放量",
            "accent": PLATFORM_META.get(platform, {}).get("accent", "#e45532"),
        }
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        views = _optional_int(item.get("views"))
        likes = _optional_int(item.get("likes"))
        favorites = _optional_int(item.get("favorites"))
        comments = _optional_int(item.get("comments"))
        shares = _optional_int(item.get("shares"))
        ctr = _ratio_percent(item.get("ctr"))
        avd_seconds = _duration_seconds(item.get("avd"))
        danmaku = _optional_int(item.get("danmaku"))
        if all(value is None for value in [views, likes, favorites, comments, shares, ctr, avd_seconds, danmaku]):
            continue
        engagement_rate = None
        if views and views > 0:
            engagement_total = sum(value or 0 for value in [likes, favorites, comments, shares])
            engagement_rate = round(engagement_total / views * 100.0, 2)
        title = str(item.get("title") or "未命名内容").strip()
        rows.append(
            {
                "title": title,
                "short_title": _short_content_title(title, 12),
                "views": views,
                "likes": likes,
                "favorites": favorites,
                "comments": comments,
                "shares": shares,
                "ctr": ctr,
                "avd_seconds": avd_seconds,
                "engagement_rate": engagement_rate,
                "danmaku": danmaku,
            }
        )
    rows.sort(key=lambda row: row["views"] or 0, reverse=True)
    rows = rows[:12]
    return {
        "labels": [row["short_title"] for row in rows],
        "full_titles": [row["title"] for row in rows],
        "views": [row["views"] for row in rows],
        "likes": [row["likes"] for row in rows],
        "favorites": [row["favorites"] for row in rows],
        "comments": [row["comments"] for row in rows],
        "shares": [row["shares"] for row in rows],
        "ctr": [row["ctr"] for row in rows],
        "avd_seconds": [row["avd_seconds"] for row in rows],
        "engagement_rate": [row["engagement_rate"] for row in rows],
        "danmaku": [row["danmaku"] for row in rows],
        "metric_label": "阅读量" if platform == "xiaohongshu" else "播放量",
        "depth_title": "互动率（按互动 / 阅读计算）" if platform == "xiaohongshu" else "点击率 / 平均播放",
        "accent": PLATFORM_META.get(platform, {}).get("accent", "#e45532"),
    }


def unavailable_platform_snapshot(
    platform: str,
    account_id: str = "",
    timezone_name: str = DEFAULT_TIMEZONE,
    message: str = "暂未配置可靠授权数据源，未发起抓取。",
) -> dict[str, Any]:
    return build_platform_snapshot(
        platform=platform,
        account_id=account_id,
        timezone_name=timezone_name,
        fans=None,
        metrics={key: None for key, _ in COMMON_METRICS},
        custom_metrics={key: None for key, _ in CUSTOM_METRICS.get(platform, [])},
        status="unavailable",
        message=message,
        source="unavailable",
        missing_status="unavailable",
    )


def failed_platform_snapshot(
    platform: str,
    account_id: str = "",
    timezone_name: str = DEFAULT_TIMEZONE,
    message: str = "抓取失败。",
) -> dict[str, Any]:
    return build_platform_snapshot(
        platform=platform,
        account_id=account_id,
        timezone_name=timezone_name,
        fans=None,
        metrics={key: None for key, _ in COMMON_METRICS},
        custom_metrics={key: None for key, _ in CUSTOM_METRICS.get(platform, [])},
        status="failed",
        message=sanitize_log_message(message),
        source="failed",
        missing_status="failed",
    )


def _legacy_bilibili_snapshots(history: dict[str, Any], account_id: str, timezone_name: str) -> list[dict[str, Any]]:
    snapshots = history.get("snapshots", [])
    if not isinstance(snapshots, list):
        return []
    result: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if isinstance(snapshot, dict):
            result.append(platform_snapshot_from_bilibili(snapshot, account_id, timezone_name))
    return result


def all_platform_snapshots(history: dict[str, Any], platform: str, config: Any = None) -> list[dict[str, Any]]:
    timezone_name = getattr(config, "timezone", DEFAULT_TIMEZONE)
    account_id = getattr(config, "bilibili_account_id", "") if platform == "bilibili" else ""
    result = _legacy_bilibili_snapshots(history, account_id, timezone_name) if platform == "bilibili" else []
    platform_snapshots = history.get("platform_snapshots", [])
    if isinstance(platform_snapshots, list):
        result.extend(
            snapshot
            for snapshot in platform_snapshots
            if isinstance(snapshot, dict) and snapshot.get("platform") == platform
        )
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in result:
        key = (str(snapshot.get("platform")), str(snapshot.get("capturedAt")))
        deduped[key] = snapshot
    return sorted(deduped.values(), key=lambda item: str(item.get("capturedAt", "")))


def successful_snapshots(history: dict[str, Any], platform: str, config: Any = None) -> list[dict[str, Any]]:
    return [snapshot for snapshot in all_platform_snapshots(history, platform, config) if _is_success(snapshot)]


def _snapshot_source(snapshot: dict[str, Any]) -> str:
    source_status = snapshot.get("sourceStatus", {})
    if isinstance(source_status, dict):
        return str(source_status.get("source") or "")
    return ""


def _snapshot_imported_at(snapshot: dict[str, Any]) -> str:
    source_status = snapshot.get("sourceStatus", {})
    if isinstance(source_status, dict):
        return str(source_status.get("importedAt") or "")
    return ""


def _strip_content_for_history(
    snapshot: dict[str, Any],
    content_limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    snapshot_copy = deepcopy(snapshot)
    content_items = snapshot_copy.pop("contentItems", [])
    if not isinstance(content_items, list):
        content_items = []
    normalized = _normalize_content_items(content_items)[:content_limit]
    snapshot_copy["raw"] = _raw_summary(
        snapshot_copy.get("raw") if isinstance(snapshot_copy.get("raw"), dict) else {},
        _snapshot_source(snapshot_copy),
    )
    return snapshot_copy, normalized


def _remember_latest_content(
    history: dict[str, Any],
    snapshot: dict[str, Any],
    items: list[dict[str, Any]],
    content_limit: int,
) -> None:
    if not items:
        return
    platform = str(snapshot.get("platform") or "")
    if not platform:
        return
    history.setdefault("latest_content", {})
    latest_content = history.get("latest_content")
    if not isinstance(latest_content, dict):
        latest_content = {}
        history["latest_content"] = latest_content
    current = latest_content.get(platform)
    captured_at = str(snapshot.get("capturedAt") or _iso_now(str(snapshot.get("timezone") or DEFAULT_TIMEZONE)))
    if isinstance(current, dict):
        current_captured = str(current.get("capturedAt") or "")
        if current_captured and current_captured > captured_at:
            return
    prepared_items = _fill_missing_thumbnails_from_related_content(history, platform, items)
    clean_items = merge_content_items(prepared_items, content_limit=content_limit)
    latest_content[platform] = {
        "capturedAt": captured_at,
        "timezone": str(snapshot.get("timezone") or DEFAULT_TIMEZONE),
        "source": _snapshot_source(snapshot) or "unknown",
        "importedAt": _snapshot_imported_at(snapshot),
        "items": clean_items,
    }


def merge_platform_snapshot(
    history: dict[str, Any],
    snapshot: dict[str, Any],
    keep_days: int = 90,
    content_limit: int = 50,
) -> dict[str, Any]:
    merged = history if isinstance(history, dict) else {}
    merged.setdefault("platform_snapshots", [])
    snapshots = [item for item in merged.get("platform_snapshots", []) if isinstance(item, dict)]
    platform = str(snapshot.get("platform") or "")
    captured_at = str(snapshot.get("capturedAt") or _iso_now(str(snapshot.get("timezone") or DEFAULT_TIMEZONE)))
    snapshot["capturedAt"] = captured_at
    cutoff = _parse_dt(captured_at, str(snapshot.get("timezone") or DEFAULT_TIMEZONE)) - timedelta(days=keep_days)
    filtered = []
    for item in snapshots:
        item, items = _strip_content_for_history(item, content_limit)
        _remember_latest_content(merged, item, items, content_limit)
        try:
            item_time = _parse_dt(item.get("capturedAt"), str(item.get("timezone") or DEFAULT_TIMEZONE))
        except Exception:  # noqa: BLE001 - malformed old rows should not break updates.
            continue
        same = item.get("platform") == platform and item.get("capturedAt") == captured_at
        if item_time >= cutoff and not same:
            filtered.append(item)
    clean_snapshot, new_items = _strip_content_for_history(snapshot, content_limit)
    _remember_latest_content(merged, clean_snapshot, new_items, content_limit)
    filtered.append(clean_snapshot)
    filtered.sort(key=lambda item: str(item.get("capturedAt", "")))
    merged["platform_snapshots"] = filtered
    return merged


def repair_latest_content_thumbnails(history: dict[str, Any], content_limit: int = 50) -> dict[str, Any]:
    """Repair cached platform content covers before saving or rendering."""
    if not isinstance(history, dict):
        return history
    latest_content = history.get("latest_content")
    if not isinstance(latest_content, dict):
        return history
    for platform, cache in list(latest_content.items()):
        if not isinstance(cache, dict):
            continue
        items = cache.get("items")
        if not isinstance(items, list):
            continue
        clean_items = [item for item in items if isinstance(item, dict)]
        repaired = _fill_missing_thumbnails_from_related_content(history, str(platform), clean_items)
        cache["items"] = merge_content_items(repaired, content_limit=content_limit)
    return history


def append_fetch_log(
    history: dict[str, Any],
    *,
    platform: str,
    status: str,
    message: str,
    timezone_name: str = DEFAULT_TIMEZONE,
    retention_days: int = 30,
) -> dict[str, Any]:
    history.setdefault("fetch_logs", [])
    now = _iso_now(timezone_name)
    clean_message = sanitize_log_message(message)
    logs = [item for item in history.get("fetch_logs", []) if isinstance(item, dict)]
    logs.append(
        {
            "capturedAt": now,
            "platform": platform,
            "status": status,
            "message": clean_message,
        }
    )
    cutoff = _parse_dt(now, timezone_name) - timedelta(days=retention_days)
    history["fetch_logs"] = [
        item
        for item in logs
        if _parse_dt(item.get("capturedAt"), timezone_name) >= cutoff
    ]
    return history


def write_update_log(log_path: str | Path, event: dict[str, Any]) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8") if not path.exists() else None
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def sanitize_log_message(message: Any) -> str:
    text = str(message or "")
    text = re.sub(r"(SESSDATA|bili_jct|BILIBILI_COOKIE|DOUYIN_COOKIE|XIAOHONGSHU_COOKIE|BARK_DEVICE_KEY)=([^;\\s]+)", r"\1=[redacted]", text)
    text = re.sub(r"([A-Za-z0-9_]*token[A-Za-z0-9_]*|secret|device_key)=([^;\\s]+)", r"\1=[redacted]", text, flags=re.I)
    return text[:800]


def _latest_before(snapshots: list[dict[str, Any]], before: datetime) -> dict[str, Any] | None:
    candidates = [
        snapshot
        for snapshot in snapshots
        if _parse_dt(snapshot.get("capturedAt"), str(snapshot.get("timezone") or DEFAULT_TIMEZONE)) < before
    ]
    return candidates[-1] if candidates else None


def _latest_at_or_before(snapshots: list[dict[str, Any]], target: datetime) -> dict[str, Any] | None:
    candidates = [
        snapshot
        for snapshot in snapshots
        if _parse_dt(snapshot.get("capturedAt"), str(snapshot.get("timezone") or DEFAULT_TIMEZONE)) <= target
    ]
    return candidates[-1] if candidates else None


def _period_delta(snapshots: list[dict[str, Any]], latest: dict[str, Any], days: int) -> int | None:
    latest_fans = _optional_int(latest.get("fans"))
    if latest_fans is None:
        return None
    latest_time = _parse_dt(latest.get("capturedAt"), str(latest.get("timezone") or DEFAULT_TIMEZONE))
    baseline = _latest_at_or_before(snapshots, latest_time - timedelta(days=days))
    if not baseline:
        return None
    baseline_fans = _optional_int(baseline.get("fans"))
    if baseline_fans is None:
        return None
    return latest_fans - baseline_fans


def _yesterday_delta(snapshots: list[dict[str, Any]], latest: dict[str, Any]) -> int | None:
    latest_fans = _optional_int(latest.get("fans"))
    if latest_fans is None:
        return None
    timezone_name = str(latest.get("timezone") or DEFAULT_TIMEZONE)
    latest_time = _parse_dt(latest.get("capturedAt"), timezone_name)
    today_start = latest_time.replace(hour=0, minute=0, second=0, microsecond=0)
    baseline = _latest_before(snapshots, today_start)
    if not baseline:
        return None
    baseline_fans = _optional_int(baseline.get("fans"))
    if baseline_fans is None:
        return None
    return latest_fans - baseline_fans


def _manual_growth_value(latest: dict[str, Any] | None, key: str) -> int | None:
    if not latest:
        return None
    growth = latest.get("manualGrowth", {})
    if not isinstance(growth, dict):
        return None
    return _optional_int(growth.get(key))


def _daily_cumulative_delta(
    snapshots: list[dict[str, Any]],
    latest: dict[str, Any],
    key: str,
) -> tuple[int | None, int | None]:
    manual_daily = _daily_metric_values(latest, key)
    if manual_daily is not None:
        return manual_daily
    latest_time = _parse_dt(latest.get("capturedAt"), str(latest.get("timezone") or DEFAULT_TIMEZONE))
    day_start = datetime.combine(latest_time.date(), time.min, tzinfo=latest_time.tzinfo)
    prev_start = day_start - timedelta(days=1)
    today_baseline = _latest_before(snapshots, day_start)
    yesterday_latest = today_baseline
    yesterday_baseline = _latest_before(snapshots, prev_start)
    latest_value = _metric_value(latest, key)
    today_base_value = _metric_value(today_baseline, key) if today_baseline else None
    yesterday_latest_value = _metric_value(yesterday_latest, key) if yesterday_latest else None
    yesterday_base_value = _metric_value(yesterday_baseline, key) if yesterday_baseline else None
    today = latest_value - today_base_value if latest_value is not None and today_base_value is not None else None
    yesterday = (
        yesterday_latest_value - yesterday_base_value
        if yesterday_latest_value is not None and yesterday_base_value is not None
        else None
    )
    if today is not None and today < 0:
        today = None
    if yesterday is not None and yesterday < 0:
        yesterday = None
    return today, yesterday


def _previous_metric_value(snapshots: list[dict[str, Any]], latest: dict[str, Any], key: str) -> int | float | None:
    if len(snapshots) < 2:
        return None
    for snapshot in reversed(snapshots[:-1]):
        value = _metric_value(snapshot, key)
        if value is not None:
            return value
    return None


def _metric_row(snapshots: list[dict[str, Any]], latest: dict[str, Any] | None, key: str, label: str) -> dict[str, Any]:
    if latest is None:
        return {
            "label": label,
            "today": deepcopy(UNAVAILABLE),
            "yesterday": deepcopy(UNAVAILABLE),
            "delta": deepcopy(UNAVAILABLE),
            "delta_percent": deepcopy(UNAVAILABLE),
            "note": "暂不可用",
        }
    metric_columns = latest.get("metricColumns", {}) if isinstance(latest.get("metricColumns"), dict) else {}
    use_cumulative_values = str(metric_columns.get("current") or "") == "当前累计"
    today, yesterday = (None, None) if use_cumulative_values else _daily_cumulative_delta(snapshots, latest, key)
    note = "" if today is not None or yesterday is not None else "暂不可用"
    if today is None and yesterday is None:
        current_value = _metric_value(latest, key)
        if current_value is not None:
            today = current_value
            yesterday = _previous_metric_value(snapshots, latest, key)
            note = "当前累计" if yesterday is None else "当前累计 / 上次累计"
    delta = today - yesterday if today is not None and yesterday is not None else None
    percent = (delta / yesterday * 100.0) if delta is not None and yesterday not in (None, 0) else None
    return {
        "label": label,
        "today": _fmt_value(today),
        "yesterday": _fmt_value(yesterday),
        "delta": _fmt_delta(delta),
        "delta_percent": _fmt_percent(percent),
        "note": note,
    }


def _platform_enabled(config: Any, platform: str) -> bool:
    if platform == "bilibili":
        return bool(getattr(config, "bilibili_enabled", True))
    if platform == "douyin":
        return bool(getattr(config, "douyin_enabled", True))
    if platform == "xiaohongshu":
        return bool(getattr(config, "xiaohongshu_enabled", True))
    return True


def _account_id(config: Any, platform: str) -> str:
    return str(getattr(config, f"{platform}_account_id", "") or "")


def _status_label(snapshot: dict[str, Any] | None, enabled: bool, has_success: bool) -> tuple[str, str]:
    if not enabled:
        return "未启用", "is-unavailable"
    if snapshot is None:
        return "暂不可用", "is-unavailable"
    status = _status(snapshot)
    if status in SUCCESS_STATUSES:
        return "成功" if status == "success" else "部分可用", "is-positive" if status == "success" else "is-warning"
    if has_success:
        return "本次失败，显示最近成功", "is-warning"
    return "暂不可用", "is-unavailable"


def _last_success_label(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return "--"
    captured = _parse_dt(snapshot.get("capturedAt"), str(snapshot.get("timezone") or DEFAULT_TIMEZONE))
    return captured.strftime("%Y-%m-%d %H:%M")


def _source_label(source: str) -> str:
    labels = {
        "official_api": "官方 API",
        "authorized_cookie": "授权后台 Cookie",
        "manual_import": "手动导入",
        "bilibili_live": "B 站创作中心",
        "bilibili_cache": "缓存数据",
        "unavailable": "暂不可用",
        "failed": "抓取失败",
    }
    return labels.get(source, source or "--")


def _comparable_successes(snapshots: list[dict[str, Any]], latest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not latest:
        return []
    source = _snapshot_source(latest)
    if not source:
        return snapshots
    comparable = [snapshot for snapshot in snapshots if _snapshot_source(snapshot) == source]
    return comparable or [latest]


def _latest_success_with_fans(snapshots: list[dict[str, Any]]) -> dict[str, Any] | None:
    for snapshot in reversed(snapshots):
        if _optional_int(snapshot.get("fans")) is not None:
            return snapshot
    return None


def _freshness_message(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return ""
    source = _snapshot_source(snapshot)
    if source != "manual_import":
        return ""
    source_status = snapshot.get("sourceStatus", {})
    imported_at = ""
    if isinstance(source_status, dict):
        imported_at = str(source_status.get("importedAt") or "")
    captured = _last_success_label(snapshot)
    if imported_at:
        try:
            imported = _parse_dt(imported_at, str(snapshot.get("timezone") or DEFAULT_TIMEZONE)).strftime("%Y-%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            imported = imported_at
        return f"最近手动更新时间：{imported}；数据采集时间：{captured}，可能不是实时数据。"
    return f"手动导入数据；数据采集时间：{captured}，可能不是实时数据。"


def _build_platform_card(history: dict[str, Any], platform: str, config: Any = None) -> dict[str, Any]:
    enabled = _platform_enabled(config, platform)
    all_rows = all_platform_snapshots(history, platform, config)
    successes = [snapshot for snapshot in all_rows if _is_success(snapshot)]
    latest_success = successes[-1] if successes else None
    fans_snapshot = latest_success if _optional_int((latest_success or {}).get("fans")) is not None else _latest_success_with_fans(successes)
    growth_snapshot = fans_snapshot or latest_success
    comparable_successes = _comparable_successes(successes, growth_snapshot)
    latest_any = all_rows[-1] if all_rows else None
    display_snapshot = latest_success or latest_any
    meta = PLATFORM_META[platform]
    status_label, status_class = _status_label(latest_any, enabled, latest_success is not None)
    metric_columns = (latest_success or {}).get("metricColumns", {})
    if not isinstance(metric_columns, dict):
        metric_columns = {}
    source = _snapshot_source(latest_success or latest_any or {})
    status_message = _status_message(latest_any) or ("暂不可用" if not latest_success else "")
    freshness_message = _freshness_message(latest_success)
    if freshness_message:
        status_message = f"{status_message} {freshness_message}".strip()
    if latest_success and fans_snapshot and fans_snapshot is not latest_success:
        status_message = (
            f"{status_message} 当前授权接口未返回粉丝总量，已沿用最近一次有粉丝值的快照。"
        ).strip()
    growth = [
        {
            "title": "相比昨日的涨粉",
            "value": _fmt_delta(
                _manual_growth_value(growth_snapshot, "cycle")
                if _manual_growth_value(growth_snapshot, "cycle") is not None
                else (_yesterday_delta(comparable_successes, growth_snapshot) if growth_snapshot else None)
            ),
        },
        {
            "title": "7日涨粉",
            "value": _fmt_delta(
                _manual_growth_value(growth_snapshot, "7d")
                if _manual_growth_value(growth_snapshot, "7d") is not None
                else (_period_delta(comparable_successes, growth_snapshot, 7) if growth_snapshot else None)
            ),
        },
        {
            "title": "30日涨粉",
            "value": _fmt_delta(
                _manual_growth_value(growth_snapshot, "30d")
                if _manual_growth_value(growth_snapshot, "30d") is not None
                else (_period_delta(comparable_successes, growth_snapshot, 30) if growth_snapshot else None)
            ),
        },
    ]
    return {
        "key": platform,
        "name": meta["name"],
        "logo": meta["logo"],
        "accent": meta["accent"],
        "icon": meta.get("icon", ""),
        "account_id": _account_id(config, platform) or str((display_snapshot or {}).get("accountId") or ""),
        "fans": _fmt_value((fans_snapshot or {}).get("fans") if latest_success else None),
        "status_label": status_label,
        "status_class": status_class,
        "status_message": status_message,
        "source": source,
        "source_label": _source_label(source),
        "last_success_label": _last_success_label(latest_success),
        "metric_columns": {
            "current": str(metric_columns.get("current") or "今日"),
            "previous": str(metric_columns.get("previous") or "昨日"),
        },
        "growth": growth,
        "common_metrics": [
            _metric_row(successes, latest_success, key, label)
            for key, label in COMMON_METRICS
        ],
        "custom_metrics": [
            _metric_row(successes, latest_success, key, label)
            for key, label in CUSTOM_METRICS.get(platform, [])
        ],
        "content_items": _content_items(history, platform, latest_success),
        "content_chart": _content_chart(history, latest_success, platform),
    }


def _trend_snapshots(history: dict[str, Any], platform: str, config: Any = None) -> list[dict[str, Any]]:
    rows = [
        snapshot
        for snapshot in successful_snapshots(history, platform, config)
        if _optional_int(snapshot.get("fans")) is not None
    ]
    if not rows:
        return []
    latest_source = _snapshot_source(rows[-1])
    if latest_source:
        rows = [snapshot for snapshot in rows if _snapshot_source(snapshot) == latest_source]
    return rows[-30:]


def next_update_label(
    update_times: list[str],
    timezone_name: str = DEFAULT_TIMEZONE,
    update_interval_minutes: int | None = None,
) -> str:
    now = datetime.now(_tz(timezone_name))
    if update_interval_minutes:
        interval = max(1, min(int(update_interval_minutes), 1440))
        day_start = datetime.combine(now.date(), time(0, 0), tzinfo=now.tzinfo)
        elapsed_minutes = int((now - day_start).total_seconds() // 60)
        next_slot_minutes = ((elapsed_minutes // interval) + 1) * interval
        target = day_start + timedelta(minutes=next_slot_minutes)
        delta = target - now
        hours = int(delta.total_seconds() // 3600)
        minutes = int(delta.total_seconds() % 3600 // 60)
        day_label = "今天" if target.date() == now.date() else "明天"
        return f"下次更新：{day_label} {target:%H:%M}（约 {hours}小时{minutes}分钟）"

    candidates: list[datetime] = []
    for item in update_times:
        try:
            hour, minute = [int(part) for part in item.split(":", 1)]
        except ValueError:
            continue
        run_at = datetime.combine(now.date(), time(hour, minute), tzinfo=now.tzinfo)
        if run_at <= now:
            run_at += timedelta(days=1)
        candidates.append(run_at)
    if not candidates:
        return "下次更新：--"
    target = min(candidates)
    delta = target - now
    hours = int(delta.total_seconds() // 3600)
    minutes = int(delta.total_seconds() % 3600 // 60)
    day_label = "今天" if target.date() == now.date() else "明天"
    return f"下次更新：{day_label} {target:%H:%M}（约 {hours}小时{minutes}分钟）"


def derive_platform_context(history: dict[str, Any], config: Any = None) -> dict[str, Any]:
    platforms = ["bilibili", "douyin", "xiaohongshu"]
    cards = [_build_platform_card(history, platform, config) for platform in platforms]
    update_interval_minutes = getattr(config, "update_interval_minutes", None)
    page_refresh_seconds = int(getattr(config, "page_refresh_seconds", 0) or 0)
    next_update = next_update_label(
        list(getattr(config, "update_times", []) or ["12:30", "20:00"]),
        str(getattr(config, "timezone", DEFAULT_TIMEZONE)),
        update_interval_minutes,
    )
    labels: list[str] = []
    series: list[dict[str, Any]] = []
    trend_rows = {platform: _trend_snapshots(history, platform, config) for platform in platforms}
    unique_dates = {
        _parse_dt(snapshot.get("capturedAt"), str(snapshot.get("timezone") or DEFAULT_TIMEZONE)).date().isoformat()
        for snapshots in trend_rows.values()
        for snapshot in snapshots
    }
    use_time_labels = len(unique_dates) <= 1
    for platform in platforms:
        platform_labels = [
            _parse_dt(snapshot.get("capturedAt"), str(snapshot.get("timezone") or DEFAULT_TIMEZONE)).strftime(
                "%H:%M" if use_time_labels else "%m-%d"
            )
            for snapshot in trend_rows[platform]
        ]
        for label in platform_labels:
            if label not in labels:
                labels.append(label)
    for platform in platforms:
        by_label = {
            _parse_dt(snapshot.get("capturedAt"), str(snapshot.get("timezone") or DEFAULT_TIMEZONE)).strftime(
                "%H:%M" if use_time_labels else "%m-%d"
            ): snapshot
            for snapshot in trend_rows[platform]
        }
        series.append(
            {
                "name": PLATFORM_META[platform]["name"],
                "data": [
                    _optional_int(by_label[label].get("fans")) if label in by_label else None
                    for label in labels
                ],
            }
        )
    return {
        "platform_cards": cards,
        "follower_trend_chart": {"labels": labels, "series": series},
        "platform_content_charts": {
            card["key"]: card["content_chart"]
            for card in cards
            if card["key"] != "bilibili"
        },
        "next_update_label": next_update,
        "page_refresh_seconds": page_refresh_seconds,
        "operational_status": build_operational_status(
            cards,
            next_update_label=next_update,
            update_interval_minutes=update_interval_minutes,
            page_refresh_seconds=page_refresh_seconds,
        ),
    }
