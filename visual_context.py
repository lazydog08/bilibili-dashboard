from __future__ import annotations

import math
from typing import Any

from analytics import format_number


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("raw")
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _label(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "--"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{format_number(value)}"


def rolling_average(values: list[Any], window: int = 7) -> list[float | None]:
    if window <= 0:
        raise ValueError("window must be positive")
    result: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            result.append(None)
            continue
        window_values = [_number(item) for item in values[index + 1 - window : index + 1]]
        valid = [item for item in window_values if item is not None]
        result.append(round(sum(valid) / len(valid), 2) if len(valid) == window else None)
    return result


def percentile(values: list[Any], pct: float) -> float | None:
    valid = sorted(item for item in (_number(value) for value in values) if item is not None)
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0]
    rank = (len(valid) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return valid[low]
    weight = rank - low
    return valid[low] * (1 - weight) + valid[high] * weight


def indexed_series(labels: list[str], series: list[dict[str, Any]]) -> dict[str, Any]:
    first_valid_indexes = []
    for item in series:
        data = item.get("data", [])
        for index, value in enumerate(data):
            if _number(value) not in {None, 0}:
                first_valid_indexes.append(index)
                break
    if not first_valid_indexes:
        return {"labels": [], "series": [], "empty": "历史数据不足"}

    start = max(first_valid_indexes)
    chart_labels = labels[start:]
    indexed = []
    for item in series:
        data = item.get("data", [])[start:]
        base = next((_number(value) for value in data if _number(value) not in {None, 0}), None)
        indexed.append(
            {
                "name": item.get("name", ""),
                "data": [
                    round((_number(value) or 0) / base * 100, 2) if base and _number(value) is not None else None
                    for value in data
                ],
            }
        )
    return {
        "labels": chart_labels,
        "series": indexed,
        "empty": "" if len(chart_labels) >= 2 else "历史数据不足",
    }


def _platform_growth(card: dict[str, Any], index: int) -> float | None:
    growth = card.get("growth") if isinstance(card.get("growth"), list) else []
    if index >= len(growth):
        return None
    return _number((growth[index] or {}).get("value"))


def build_growth_contribution_chart(platform_cards: list[dict[str, Any]], growth_index: int = 1) -> dict[str, Any]:
    rows = []
    for card in platform_cards:
        value = _platform_growth(card, growth_index)
        if value is None:
            continue
        rows.append(
            {
                "name": str(card.get("name") or ""),
                "value": value,
                "accent": str(card.get("accent") or "#cccccc"),
            }
        )
    total = sum(abs(row["value"]) for row in rows)
    if not rows or total == 0:
        return {"labels": [], "values": [], "colors": [], "empty": "暂无可比增长数据"}
    return {
        "labels": [row["name"] for row in rows],
        "values": [row["value"] for row in rows],
        "colors": [row["accent"] for row in rows],
        "empty": "",
    }


def _engagement_rate(item: dict[str, Any]) -> float | None:
    views = _number(item.get("views"))
    if not views or views <= 0:
        return None
    interactions = sum(
        _number(item.get(key)) or 0
        for key in ["likes", "coins", "favorites", "shares", "replies", "comments"]
    )
    return round(interactions / views * 100, 2)


def _interaction_count(item: dict[str, Any]) -> float:
    return sum(
        _number(item.get(key)) or 0
        for key in ["likes", "coins", "favorites", "shares", "replies", "comments"]
    )


def build_content_engagement_chart(
    recent_videos: list[dict[str, Any]], limit: int = 30, top_n: int = 10
) -> dict[str, Any]:
    rows = []
    for video in recent_videos[:limit]:
        views = _number(video.get("views"))
        engagement = _engagement_rate(video)
        if views is None or engagement is None:
            continue
        interactions = _interaction_count(video)
        title = str(video.get("title") or video.get("short_title") or "未命名内容")
        rows.append(
            {
                "label": str(video.get("axis_title") or video.get("short_title") or title),
                "title": title,
                "views": views,
                "interactions": interactions,
                "engagement_rate": engagement,
            }
        )
    if len(rows) < 3:
        return {
            "labels": [],
            "values": [],
            "items": [],
            "median_engagement": None,
            "empty": "样本不足",
        }
    rows.sort(key=lambda row: (row["engagement_rate"], row["interactions"]), reverse=True)
    items = rows[:top_n]
    return {
        "labels": [row["label"] for row in items],
        "values": [row["engagement_rate"] for row in items],
        "items": items,
        "median_engagement": round(percentile([row["engagement_rate"] for row in rows], 0.5) or 0, 2),
        "empty": "",
    }


def build_summary_cards(platform_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total_followers = sum(_number((card.get("fans") or {}).get("raw")) or 0 for card in platform_cards)
    yesterday = sum(_platform_growth(card, 0) or 0 for card in platform_cards)
    seven_day = sum(_platform_growth(card, 1) or 0 for card in platform_cards)
    partial = sum(1 for card in platform_cards if card.get("status_label") == "部分可用")
    success = sum(1 for card in platform_cards if card.get("status_label") == "成功")
    return [
        {
            "label": "三平台总粉丝",
            "value": _label(total_followers),
            "meta": f"昨日 {_label(yesterday, signed=True)} / 7日 {_label(seven_day, signed=True)}",
            "class": "is-neutral",
        },
        {
            "label": "7日净增长",
            "value": _label(seven_day, signed=True),
            "meta": "按各平台最近成功快照汇总",
            "class": "is-positive" if seven_day > 0 else "is-negative" if seven_day < 0 else "is-neutral",
        },
        {
            "label": "数据质量",
            "value": f"{success} 成功 / {partial} 部分" if partial else f"{success} 个平台正常",
            "meta": "页面发布和平台抓取状态分开判断",
            "class": "is-warning" if partial else "is-positive",
        },
    ]


def build_follower_reference_chart(follower_trend_chart: dict[str, Any]) -> dict[str, Any]:
    labels = list(follower_trend_chart.get("labels") or [])
    series = []
    for item in follower_trend_chart.get("series") or []:
        data = item.get("data") or []
        series.append({"name": item.get("name"), "type": "raw", "data": data})
        if len([value for value in data if _number(value) is not None]) >= 7:
            series.append({"name": f"{item.get('name')} 7日均线", "type": "average", "data": rolling_average(data, 7)})
    return {"labels": labels, "series": series, "empty": "" if len(labels) >= 2 else "历史数据不足"}


def build_visual_context(
    platform_cards: list[dict[str, Any]],
    follower_trend_chart: dict[str, Any],
    recent_videos: list[dict[str, Any]],
) -> dict[str, Any]:
    labels = list(follower_trend_chart.get("labels") or [])
    series = list(follower_trend_chart.get("series") or [])
    return {
        "summary_cards": build_summary_cards(platform_cards),
        "growth_contribution_chart": build_growth_contribution_chart(platform_cards),
        "follower_reference_chart": build_follower_reference_chart(follower_trend_chart),
        "indexed_follower_chart": indexed_series(labels, series),
        "content_engagement_chart": build_content_engagement_chart(recent_videos),
    }


def empty_visual_context(message: str = "历史数据不足") -> dict[str, Any]:
    return {
        "summary_cards": [],
        "growth_contribution_chart": {"labels": [], "values": [], "colors": [], "empty": message},
        "follower_reference_chart": {"labels": [], "series": [], "empty": message},
        "indexed_follower_chart": {"labels": [], "series": [], "empty": message},
        "content_engagement_chart": {
            "labels": [],
            "values": [],
            "items": [],
            "median_engagement": None,
            "empty": "样本不足",
        },
    }
