from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser


DEFAULT_TIMEZONE = "Asia/Shanghai"
FIXTURE_KPI_LABELS = [
    "风暴传媒粉丝数",
    "科普分析粉丝数",
    "趣味困难粉丝数",
    "纪录短片粉丝数",
]
LIVE_KPI_KEYS = [
    "total_followers",
    "follower_delta_7d",
    "total_views",
    "total_likes",
]


def now_shanghai() -> datetime:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))


def empty_history(source: str = "fixture") -> dict[str, Any]:
    now = now_shanghai().isoformat(timespec="seconds")
    return {
        "schema_version": 1,
        "last_updated": now,
        "source": source,
        "warnings": [],
        "snapshots": [],
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if not text:
                return default
            if text.endswith("%"):
                return float(text[:-1].strip()) / 100.0
            return float(text)
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(str(value).strip().replace(",", "").rstrip("%")))
    except (TypeError, ValueError):
        return default


def safe_ratio(value: Any, default: float = 0.0) -> float:
    ratio = safe_float(value, default)
    if isinstance(value, str) and value.strip().endswith("%"):
        return ratio
    if 1 < ratio <= 100:
        return ratio / 100.0
    return ratio


def safe_minutes(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        if "秒" in text:
            text = re.sub(r"[^\d.\-]", "", text)
            return safe_float(text, default) / 60.0
        if ":" in text:
            parts = [safe_float(part, 0.0) for part in text.split(":")]
            if len(parts) == 2:
                return parts[0] + parts[1] / 60.0
            if len(parts) == 3:
                return parts[0] * 60.0 + parts[1] + parts[2] / 60.0
        text = re.sub(r"[^\d.\-]", "", text)
        value = text
    minutes = safe_float(value, default)
    if minutes > 60:
        return minutes / 60.0
    return minutes


def format_number(value: Any) -> str:
    number = safe_float(value, 0.0)
    if math.isfinite(number) and abs(number - round(number)) < 0.000001:
        return f"{int(round(number)):,}"
    return f"{number:,.1f}"


def safe_percent_change(current: Any, previous: Any) -> float:
    current_value = safe_float(current, 0.0)
    previous_value = safe_float(previous, 0.0)
    if previous_value == 0:
        return 0.0
    return (current_value - previous_value) / abs(previous_value)


def normalize_thumbnail_url(pic: Any) -> str:
    if pic is None:
        return ""
    text = str(pic).strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("/"):
        return f"https://i0.hdslb.com{text}"
    if text.startswith("bfs/"):
        return f"https://i0.hdslb.com/{text}"
    return text


def parse_publish_time(value: Any) -> str:
    if value is None or value == "":
        return now_shanghai().date().isoformat()

    tz = ZoneInfo(DEFAULT_TIMEZONE)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000.0
        return datetime.fromtimestamp(timestamp, tz=tz).date().isoformat()

    text = str(value).strip()
    if not text:
        return now_shanghai().date().isoformat()
    if re.fullmatch(r"\d{10,13}", text):
        timestamp = float(text)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000.0
        return datetime.fromtimestamp(timestamp, tz=tz).date().isoformat()

    try:
        parsed = date_parser.parse(text)
    except (ValueError, TypeError, OverflowError):
        return now_shanghai().date().isoformat()

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    return parsed.date().isoformat()


def _parse_date(value: Any) -> datetime:
    date_text = parse_publish_time(value)
    return datetime.fromisoformat(date_text).replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))


def load_history(path: str | Path) -> dict[str, Any]:
    history_path = Path(path)
    if not history_path.exists():
        return empty_history("cache")
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_history("cache")
    if not isinstance(data, dict):
        return empty_history("cache")
    data.setdefault("schema_version", 1)
    data.setdefault("last_updated", now_shanghai().isoformat(timespec="seconds"))
    data.setdefault("source", "cache")
    data.setdefault("warnings", [])
    data["warnings"] = _unique_strings(data.get("warnings", []))
    if not isinstance(data.get("snapshots"), list):
        data["snapshots"] = []
    return data


def save_history(history: dict[str, Any], path: str | Path) -> None:
    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps(history, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_fixture_history(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Fixture history must be a JSON object.")
    data.setdefault("schema_version", 1)
    data.setdefault("source", "fixture")
    data.setdefault("warnings", [])
    if not isinstance(data.get("snapshots"), list):
        raise ValueError("Fixture history must contain snapshots.")
    return data


def merge_today_snapshot(
    history: dict[str, Any],
    snapshot: dict[str, Any],
    keep_days: int = 90,
) -> dict[str, Any]:
    merged = deepcopy(history) if isinstance(history, dict) else empty_history("cache")
    merged.setdefault("schema_version", 1)
    merged.setdefault("warnings", [])
    merged.setdefault("snapshots", [])

    snapshot_copy = deepcopy(snapshot)
    snapshot_date = snapshot_copy.get("date") or parse_publish_time(snapshot_copy.get("updated_at"))
    snapshot_copy["date"] = snapshot_date
    snapshot_copy.setdefault("updated_at", now_shanghai().isoformat(timespec="seconds"))
    snapshot_copy.setdefault("channel", {})
    snapshot_copy.setdefault("videos", [])

    snapshots = [
        item for item in merged.get("snapshots", []) if isinstance(item, dict) and item.get("date") != snapshot_date
    ]
    snapshots.append(snapshot_copy)
    snapshots.sort(key=lambda item: str(item.get("date", "")))
    merged["snapshots"] = snapshots[-keep_days:]
    merged["last_updated"] = snapshot_copy.get("updated_at", now_shanghai().isoformat(timespec="seconds"))

    snapshot_warnings = snapshot_copy.get("warnings", [])
    if isinstance(snapshot_warnings, list):
        merged["warnings"] = _unique_strings([*merged.get("warnings", []), *snapshot_warnings])
    return merged


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _display_warnings(values: list[Any], max_items: int = 5) -> list[str]:
    raw = _unique_strings(values)
    detail_failure_count = 0
    cleaned: list[str] = []
    for value in raw:
        text = str(value).replace("\r", " ").strip()
        if not text:
            continue
        if "明细获取失败" in text and "已使用列表数据回退" in text:
            detail_failure_count += 1
            continue
        if "\nFor more information check:" in text:
            text = text.split("\nFor more information check:", 1)[0].strip()
        text = re.sub(r"\s+", " ", text)
        cleaned.append(text)

    if detail_failure_count:
        cleaned.append(f"{detail_failure_count} 个视频明细接口不可用，已使用列表数据回退。")

    cleaned = _unique_strings(cleaned)
    if len(cleaned) <= max_items:
        return cleaned
    hidden_count = len(cleaned) - max_items
    return [*cleaned[:max_items], f"还有 {hidden_count} 条警告已折叠，详见 data/history.json。"]


def _short_title(title: Any, limit: int = 18) -> str:
    text = str(title or "未命名视频").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit - 1]}…"


def _percent_label(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def _kpi_change_label(change: float) -> str:
    return f"{change * 100:+.2f}%"


def _sparkline_points(values: list[float], width: int = 112, height: int = 42) -> str:
    clean = [safe_float(value, 0.0) for value in values if math.isfinite(safe_float(value, 0.0))]
    if not clean:
        clean = [0.0, 0.0]
    if len(clean) == 1:
        clean = [clean[0], clean[0]]
    min_value = min(clean)
    max_value = max(clean)
    span = max(max_value - min_value, 1.0)
    step = width / (len(clean) - 1)
    points = []
    for index, value in enumerate(clean):
        x = index * step
        y = height - ((value - min_value) / span * (height - 8)) - 4
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _channel(snapshot: dict[str, Any]) -> dict[str, Any]:
    channel = snapshot.get("channel", {})
    return channel if isinstance(channel, dict) else {}


def _videos(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    videos = snapshot.get("videos", [])
    return [video for video in videos if isinstance(video, dict)] if isinstance(videos, list) else []


def _has_video_data(snapshot: dict[str, Any]) -> bool:
    return bool(_videos(snapshot))


def _is_fixture_snapshot(snapshot: dict[str, Any]) -> bool:
    return isinstance(_channel(snapshot).get("category_totals"), dict)


def _derive_kpis(
    snapshots: list[dict[str, Any]],
    latest: dict[str, Any],
    previous: dict[str, Any] | None,
    config: Any = None,
) -> list[dict[str, Any]]:
    latest_channel = _channel(latest)
    previous_channel = _channel(previous or {})
    category_totals = latest_channel.get("category_totals")
    previous_category_totals = previous_channel.get("category_totals", {})

    if isinstance(category_totals, dict) and category_totals:
        labels = [label for label in FIXTURE_KPI_LABELS if label in category_totals]
        labels.extend([label for label in category_totals.keys() if label not in labels])
        labels = labels[:4]

        kpis = []
        for label in labels:
            current = safe_float(category_totals.get(label), 0.0)
            previous_value = (
                previous_category_totals.get(label)
                if isinstance(previous_category_totals, dict)
                else None
            )
            series = []
            for snapshot in snapshots[-18:]:
                values = _channel(snapshot).get("category_totals", {})
                if isinstance(values, dict):
                    series.append(safe_float(values.get(label), current))
            kpis.append(_build_kpi(label, current, previous_value, series))
        return kpis

    labels = list(getattr(config, "kpi_labels", []) or ["总粉丝数", "7日涨粉", "总播放量", "总点赞数"])
    labels = (labels + ["总粉丝数", "7日涨粉", "总播放量", "总点赞数"])[:4]
    kpis = []
    for label, key in zip(labels, LIVE_KPI_KEYS):
        current = safe_float(latest_channel.get(key), 0.0)
        previous_value = previous_channel.get(key)
        series = [safe_float(_channel(snapshot).get(key), current) for snapshot in snapshots[-18:]]
        kpis.append(_build_kpi(label, current, previous_value, series))
    return kpis


def _build_kpi(label: str, current: float, previous: Any, series: list[float]) -> dict[str, Any]:
    change = safe_percent_change(current, previous)
    return {
        "label": label,
        "value": current,
        "formatted_value": format_number(current),
        "change": change,
        "change_label": _kpi_change_label(change),
        "change_icon": "▲" if change >= 0 else "▼",
        "sparkline_points": _sparkline_points(series or [current]),
    }


def _prepare_video(video: dict[str, Any]) -> dict[str, Any]:
    title = str(video.get("title") or "未命名视频").replace("\r", " ").strip()
    publish_time = parse_publish_time(video.get("publish_time"))
    ctr = safe_ratio(video.get("ctr"), 0.0)
    avd = safe_minutes(video.get("avd_minutes"), 0.0)
    avp = safe_ratio(video.get("avp_percent"), 0.0)
    return {
        "bvid": str(video.get("bvid") or ""),
        "title": title,
        "short_title": _short_title(title, 20),
        "axis_title": _short_title(title, 12),
        "thumbnail": normalize_thumbnail_url(video.get("thumbnail")),
        "publish_time": publish_time,
        "views": safe_int(video.get("views"), 0),
        "likes": safe_int(video.get("likes"), 0),
        "coins": safe_int(video.get("coins"), 0),
        "favorites": safe_int(video.get("favorites"), 0),
        "shares": safe_int(video.get("shares"), 0),
        "replies": safe_int(video.get("replies"), 0),
        "ctr": ctr,
        "ctr_percent": round(ctr * 100, 2),
        "ctr_label": _percent_label(ctr, 2),
        "avd_minutes": round(avd, 2),
        "avd_label": f"{avd:.1f}",
        "avp_percent": avp,
        "avp_value": round(avp * 100, 2),
        "avp_label": _percent_label(avp, 1),
        "follower_gain": safe_int(video.get("follower_gain"), 0),
        "impressions": safe_int(video.get("impressions"), 0),
    }


def derive_dashboard_context(
    history: dict[str, Any],
    config: Any = None,
    display_warnings: list[Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(history, dict):
        history = empty_history("fixture")
    snapshots = [
        snapshot for snapshot in history.get("snapshots", []) if isinstance(snapshot, dict)
    ]
    snapshots.sort(key=lambda item: str(item.get("date", "")))

    display_snapshots = snapshots
    live_snapshots = [
        snapshot
        for snapshot in snapshots
        if snapshot.get("source") == "live" and _has_video_data(snapshot)
    ]
    if live_snapshots:
        display_snapshots = live_snapshots
    elif str(history.get("source") or "") in {"live", "live_partial", "cache"}:
        live_like_snapshots = [
            snapshot
            for snapshot in snapshots
            if _has_video_data(snapshot) and not _is_fixture_snapshot(snapshot)
        ]
        if live_like_snapshots:
            display_snapshots = live_like_snapshots

    latest = (
        display_snapshots[-1]
        if display_snapshots
        else {"date": now_shanghai().date().isoformat(), "channel": {}, "videos": []}
    )
    previous = display_snapshots[-2] if len(display_snapshots) >= 2 else None
    latest_videos = [_prepare_video(video) for video in _videos(latest)]
    latest_date = _parse_date(latest.get("date"))

    recent_videos = [
        video
        for video in latest_videos
        if (latest_date - _parse_date(video.get("publish_time"))).days <= 30
    ]
    minimum_grid_count = min(9, len(latest_videos))
    if len(recent_videos) < minimum_grid_count:
        recent_videos = latest_videos
    recent_videos.sort(key=lambda item: item["publish_time"], reverse=True)

    ctr_videos = sorted(latest_videos, key=lambda item: item["ctr_percent"], reverse=True)[:30]
    views_videos = sorted(recent_videos, key=lambda item: item["publish_time"])[-18:]
    avd_videos = views_videos

    warning_values = (
        display_warnings
        if display_warnings is not None
        else [*history.get("warnings", []), *latest.get("warnings", [])]
    )
    warnings = _display_warnings(warning_values)
    feishu_enabled = bool(getattr(config, "feishu_enabled", False))
    source = str(history.get("source") or "fixture")
    if feishu_enabled:
        badge_text = "飞书多维表格 提供技术支持"
    elif source == "fixture":
        badge_text = "本地示例模板"
    elif source == "manual":
        badge_text = "真实数据预填"
    elif source == "cache":
        badge_text = "缓存数据"
    elif source == "live_partial":
        badge_text = "B站创作中心数据（部分明细回退）"
    else:
        badge_text = "B站创作中心数据"

    context = {
        "page_title": "【懒狗小黑】频道数据看板",
        "section_title": "频道数据情况",
        "last_updated": str(history.get("last_updated") or latest.get("updated_at") or ""),
        "source": source,
        "warnings": warnings,
        "badge_text": badge_text,
        "kpis": _derive_kpis(display_snapshots, latest, previous, config),
        "ctr_chart": {
            "labels": [video["axis_title"] for video in ctr_videos],
            "values": [video["ctr_percent"] for video in ctr_videos],
            "full_titles": [video["title"] for video in ctr_videos],
        },
        "recent_videos": recent_videos[:30],
        "views_followers_chart": {
            "labels": [video["axis_title"] for video in views_videos],
            "full_titles": [video["title"] for video in views_videos],
            "views": [video["views"] for video in views_videos],
            "follower_gain": [video["follower_gain"] for video in views_videos],
        },
        "avd_avp_chart": {
            "labels": [video["axis_title"] for video in avd_videos],
            "full_titles": [video["title"] for video in avd_videos],
            "avd": [video["avd_minutes"] for video in avd_videos],
            "avp": [video["avp_value"] for video in avd_videos],
        },
        "snapshot_count": len(snapshots),
        "video_count": len(latest_videos),
    }
    from platforms import derive_platform_context

    context.update(derive_platform_context(history, config))
    return context
