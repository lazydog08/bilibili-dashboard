from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Asia/Shanghai"


def _timezone(name: str | None) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _parse_dt(value: Any, timezone_name: str = DEFAULT_TIMEZONE) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    zone = _timezone(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _format_local_minute(value: datetime | None) -> str:
    if value is None:
        return "--"
    return value.strftime("%Y-%m-%d %H:%M")


def _format_age(total_minutes: int) -> str:
    minutes = max(0, int(total_minutes))
    days, remainder = divmod(minutes, 1440)
    hours, minute = divmod(remainder, 60)
    if days:
        return f"{days}天{hours}小时"
    if hours:
        return f"{hours}小时{minute}分钟"
    return f"{minute}分钟"


def _status_bucket(label: Any) -> str:
    text = str(label or "")
    if text == "成功":
        return "success"
    if text in {"部分可用", "本次失败，显示最近成功"}:
        return "partial"
    if text == "未启用":
        return "disabled"
    return "unavailable"


def _status_class(bucket: str) -> str:
    if bucket == "success":
        return "is-positive"
    if bucket == "partial":
        return "is-warning"
    return "is-unavailable"


def _stale_after_minutes(update_interval_minutes: int | None) -> int:
    interval = int(update_interval_minutes or 0)
    if interval > 0:
        return max(90, min(interval, 1440) * 3)
    return 24 * 60


def build_dashboard_freshness(
    last_updated_at: Any,
    *,
    update_interval_minutes: int | None,
    timezone_name: str = DEFAULT_TIMEZONE,
    now: datetime | None = None,
) -> dict[str, Any]:
    interval = int(update_interval_minutes or 0)
    stale_after = _stale_after_minutes(update_interval_minutes)
    parsed = _parse_dt(last_updated_at, timezone_name)
    zone = _timezone(timezone_name)
    current = (now or datetime.now(zone)).astimezone(zone)
    last_label = _format_local_minute(parsed)
    base = {
        "last_updated_iso": str(last_updated_at or ""),
        "last_updated_label": last_label,
        "update_interval_minutes": interval,
        "stale_after_minutes": stale_after,
    }
    if parsed is None:
        return {
            **base,
            "status": "unknown",
            "age_minutes": None,
            "age_label": "--",
            "value": "更新时间缺失",
            "meta": "无法判断 NAS 是否还在运行，请检查公开心跳。",
            "class": "is-warning",
        }

    age_minutes = max(0, int((current - parsed).total_seconds() // 60))
    age_label = _format_age(age_minutes)
    if age_minutes < stale_after:
        return {
            **base,
            "status": "fresh",
            "age_minutes": age_minutes,
            "age_label": age_label,
            "value": f"{interval} 分钟" if interval else "按固定时刻",
            "meta": "",
            "class": "is-positive",
        }

    severe_after = max(stale_after * 4, 24 * 60)
    status_class = "is-unavailable" if age_minutes >= severe_after else "is-warning"
    cadence = f"计划每 {interval} 分钟更新" if interval else "按固定时刻更新"
    return {
        **base,
        "status": "stale",
        "age_minutes": age_minutes,
        "age_label": age_label,
        "value": f"已停更 {age_label}",
        "meta": f"最近成功：{last_label}；{cadence}",
        "class": status_class,
    }


def _platform_quality(platform_cards: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"success": 0, "partial": 0, "disabled": 0, "unavailable": 0}
    problems: list[str] = []
    for card in platform_cards:
        bucket = _status_bucket(card.get("status_label"))
        counts[bucket] = counts.get(bucket, 0) + 1
        if bucket != "success":
            name = str(card.get("name") or "未知平台")
            status = str(card.get("status_label") or "暂不可用")
            problems.append(f"{name}：{status}")

    if counts["unavailable"]:
        bucket = "unavailable"
        value = f"{counts['success']} 成功 / {counts['partial']} 部分 / {counts['unavailable']} 异常"
    elif counts["partial"]:
        bucket = "partial"
        value = f"{counts['success']} 成功 / {counts['partial']} 部分可用"
    else:
        bucket = "success"
        value = f"{counts['success']} 个平台正常"

    return {
        "label": "平台数据质量",
        "value": value,
        "meta": "；".join(problems[:2]) if problems else "三平台数据源均返回可展示结果",
        "class": _status_class(bucket),
    }


def build_operational_status(
    platform_cards: list[dict[str, Any]],
    *,
    next_update_label: str,
    update_interval_minutes: int | None,
    freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    interval = int(update_interval_minutes or 0)
    cadence_value = f"{interval} 分钟" if interval else "按固定时刻"
    cadence_card = {
        "key": "nas_cadence",
        "label": "NAS 更新节奏",
        "value": cadence_value,
        "meta": next_update_label or "下次更新时间未配置",
        "class": "is-positive",
    }
    if freshness and freshness.get("status") in {"stale", "unknown"}:
        cadence_card.update(
            {
                "value": freshness.get("value") or cadence_value,
                "meta": freshness.get("meta") or cadence_card["meta"],
                "class": freshness.get("class") or "is-warning",
            }
        )

    return {
        "cards": [
            cadence_card,
            _platform_quality(platform_cards),
        ]
    }
