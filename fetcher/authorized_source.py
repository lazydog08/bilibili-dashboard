from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from platforms import build_platform_snapshot, unavailable_platform_snapshot


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def safe_number(value: Any) -> int | None:
    if isinstance(value, dict) and "value" in value:
        value = value.get("value")
    if value in (None, "", "--"):
        return None
    text = str(value).strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1]
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def pick_number_deep(payload: Any, keys: list[str]) -> int | None:
    normalized = {key.lower() for key in keys}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in normalized:
                parsed = safe_number(value)
                if parsed is not None:
                    return parsed
        for value in payload.values():
            parsed = pick_number_deep(value, keys)
            if parsed is not None:
                return parsed
    elif isinstance(payload, list):
        for item in payload:
            parsed = pick_number_deep(item, keys)
            if parsed is not None:
                return parsed
    return None


def pick_dict(payload: Any, key: str) -> dict[str, Any]:
    if isinstance(payload, dict):
        value = payload.get(key)
        return value if isinstance(value, dict) else {}
    return {}


def _safe_url_origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/"
    return ""


async def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    safe_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        **(headers or {}),
    }
    async with httpx.AsyncClient(headers=safe_headers, timeout=20.0, follow_redirects=False) as client:
        response = await client.get(url, params=params)
    if response.status_code in {401, 403, 412, 429}:
        raise RuntimeError(f"授权数据源不可用或触发平台限制，HTTP {response.status_code}")
    response.raise_for_status()
    return response.json()


async def fetch_authorized_json(url: str, cookie: str, referer: str) -> Any:
    headers = {
        "Referer": referer,
        "Cookie": cookie,
    }
    return await fetch_json(url, headers=headers)


async def fetch_authorized_json_with_headers(
    url: str,
    cookie: str,
    referer: str,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    headers = {
        "Referer": referer,
        "Cookie": cookie,
        **(extra_headers or {}),
    }
    return await fetch_json(url, headers=headers)


async def fetch_official_json(url: str, access_token: str, params: dict[str, str] | None = None) -> Any:
    headers = {"Authorization": f"Bearer {access_token}"}
    return await fetch_json(url, headers=headers, params=params)


def pick_first(payload: Any, keys: list[str]) -> Any:
    normalized = {key.lower() for key in keys}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in normalized and value not in (None, ""):
                return value
        for value in payload.values():
            found = pick_first(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = pick_first(item, keys)
            if found not in (None, ""):
                return found
    return None


def _pick_content_list(payload: Any) -> list[dict[str, Any]]:
    candidate_keys = [
        "contentItems",
        "contents",
        "videos",
        "works",
        "aweme_list",
        "notes",
        "items",
        "list",
    ]
    if isinstance(payload, dict):
        for key in candidate_keys:
            value = payload.get(key)
            if isinstance(value, list) and any(isinstance(item, dict) for item in value):
                return [item for item in value if isinstance(item, dict)]
        for value in payload.values():
            found = _pick_content_list(value)
            if found:
                return found
    elif isinstance(payload, list) and any(isinstance(item, dict) for item in payload):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _pick_thumbnail(item: dict[str, Any]) -> Any:
    value = pick_first(
        item,
        [
            "thumbnail",
            "cover",
            "Cover",
            "cover_url",
            "coverUrl",
            "pic",
            "image",
            "image_url",
            "imageUrl",
            "url",
            "url_list",
            "urlList",
            "image_list",
            "imageList",
        ],
    )
    if isinstance(value, dict):
        nested = pick_first(
            value,
            [
                "url",
                "uri",
                "src",
                "display_url",
                "origin_url",
                "url_list",
                "urlList",
                "image_list",
                "imageList",
            ],
        )
        if isinstance(nested, list):
            for entry in nested:
                if isinstance(entry, str) and entry:
                    return entry
                if isinstance(entry, dict):
                    found = pick_first(entry, ["url", "src", "display_url", "origin_url"])
                    if found:
                        return found
        return nested
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, str) and entry:
                return entry
            if isinstance(entry, dict):
                found = pick_first(entry, ["url", "uri", "src", "display_url", "origin_url"])
                if found:
                    return found
    return value


def _normalize_content_from_payload(payload: Any, content_limit: int = 50) -> list[dict[str, Any]]:
    rows = []
    for item in _pick_content_list(payload):
        title = pick_first(item, ["title", "desc", "description", "name", "display_title", "note_title"])
        rows.append(
            {
                "id": pick_first(item, ["id", "item_id", "note_id", "aweme_id", "oid"]),
                "item_id": pick_first(item, ["item_id", "aweme_id"]),
                "note_id": pick_first(item, ["note_id", "id", "oid"]),
                "title": title or "未命名内容",
                "publish_time": pick_first(
                    item,
                    [
                        "publish_time",
                        "publishTime",
                        "publishedAt",
                        "publish_time_str",
                        "create_time",
                        "createTime",
                        "ctime",
                        "time",
                    ],
                ),
                "thumbnail": _pick_thumbnail(item),
                "views": pick_number_deep(item, ["views", "view_count", "play_count", "read_count", "播放量", "阅读量"]),
                "likes": pick_number_deep(item, ["likes", "like_count", "digg_count", "点赞数"]),
                "favorites": pick_number_deep(item, ["favorites", "favorite_count", "collect_count", "收藏数"]),
                "comments": pick_number_deep(item, ["comments", "comment_count", "评论数"]),
                "shares": pick_number_deep(item, ["shares", "share_count", "forward_count", "转发数", "分享数"]),
                "ctr": pick_first(item, ["ctr", "click_rate", "show_click_rate", "点击率"]),
                "avd": pick_first(item, ["avd", "avg_view_duration", "avg_play_duration", "average_play_time", "平均播放时长"]),
                "avp": pick_first(item, ["avp", "completion_rate", "finish_rate", "avg_view_percent", "完播率"]),
                "danmaku": pick_number_deep(item, ["danmaku", "弹幕数"]),
            }
        )
    return rows[:content_limit]


def snapshot_from_payload(
    *,
    platform: str,
    account_id: str,
    payload: Any,
    timezone_name: str,
    key_map: dict[str, list[str]],
    custom_key_map: dict[str, list[str]],
    source: str = "authorized_cookie",
    message: str = "来自已授权后台数据源。",
    content_limit: int = 50,
) -> dict[str, Any]:
    top_level = payload if isinstance(payload, dict) else {}
    manual_like = pick_dict(top_level, "platforms").get(platform)
    if isinstance(manual_like, dict):
        top_level = manual_like

    daily_metrics = pick_dict(top_level, "dailyMetrics")
    custom_daily_metrics = pick_dict(top_level, "customDailyMetrics")
    today = pick_dict(top_level, "today")
    yesterday = pick_dict(top_level, "yesterday")
    custom_today = pick_dict(top_level, "customToday")
    custom_yesterday = pick_dict(top_level, "customYesterday")
    if today or yesterday:
        daily_metrics = {
            **daily_metrics,
            **{
                key: {"today": today.get(key), "yesterday": yesterday.get(key)}
                for key in set(today) | set(yesterday)
            },
        }
    if custom_today or custom_yesterday:
        custom_daily_metrics = {
            **custom_daily_metrics,
            **{
                key: {"today": custom_today.get(key), "yesterday": custom_yesterday.get(key)}
                for key in set(custom_today) | set(custom_yesterday)
            },
        }

    fans = top_level.get("fans") if isinstance(top_level, dict) else None
    if fans is None:
        fans = pick_number_deep(payload, key_map["fans"])

    metrics = {
        key: pick_number_deep(payload, aliases)
        for key, aliases in key_map.items()
        if key != "fans"
    }
    custom_metrics = {
        key: pick_number_deep(payload, aliases)
        for key, aliases in custom_key_map.items()
    }
    content_items = _normalize_content_from_payload(top_level or payload, content_limit)
    has_data = safe_number(fans) is not None or any(value is not None for value in metrics.values()) or any(
        value is not None for value in custom_metrics.values()
    ) or bool(daily_metrics) or bool(custom_daily_metrics) or bool(content_items)
    if not has_data:
        return unavailable_platform_snapshot(
            platform,
            account_id=account_id,
            timezone_name=timezone_name,
            message="已获取授权响应，但未识别到可用数据字段。",
        )
    missing_status = "missing"
    return build_platform_snapshot(
        platform=platform,
        account_id=account_id,
        timezone_name=timezone_name,
        fans=fans,
        metrics=metrics,
        custom_metrics=custom_metrics,
        daily_metrics=daily_metrics,
        custom_daily_metrics=custom_daily_metrics,
        manual_growth=pick_dict(top_level, "growth"),
        status="success",
        message=message,
        source=source,
        missing_status=missing_status,
        content_items=content_items,
        raw={
            "summary": {
                "source": source,
                "origin": _safe_url_origin(str(top_level.get("source_url") or "")),
                "content_count": len(content_items),
            }
        },
    )
