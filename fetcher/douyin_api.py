from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from fetcher.authorized_source import (
    fetch_authorized_json,
    fetch_authorized_json_with_headers,
    fetch_official_json,
    snapshot_from_payload,
)
from platforms import build_platform_snapshot, unavailable_platform_snapshot


class SourceUnavailableError(RuntimeError):
    """A configured source cannot be used safely; try the next source."""


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DOUYIN_DASHBOARD_URL = "https://creator.douyin.com/janus/douyin/creator/data/overview/dashboard"
DOUYIN_ITEM_LIST_URL = "https://creator.douyin.com/web/api/creator/item/list"
DOUYIN_ITEM_DETAIL_URL = "https://creator.douyin.com/web/api/creator/data/item/summarize/"


def _safe_int(value: Any) -> int | None:
    if value in (None, "", "--"):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", "").strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _ratio_to_percent(value: Any) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    if 0 <= number <= 1:
        return round(number * 100.0, 2)
    return round(number, 2)


def _percent_label(value: Any) -> str | None:
    percent = _ratio_to_percent(value)
    if percent is None:
        return None
    return f"{percent:.2f}%"


def _seconds_label(value: Any) -> str | None:
    seconds = _safe_float(value)
    if seconds is None:
        return None
    return f"{seconds:.1f}秒"


def _metric_map(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("metrics")
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("english_metric_name") or "").strip()
        if key:
            result[key] = row
    return result


def _metric_value(metrics: dict[str, dict[str, Any]], key: str) -> int | None:
    return _safe_int((metrics.get(key) or {}).get("metric_value"))


def _metric_percent(metrics: dict[str, dict[str, Any]], key: str) -> float | None:
    return _ratio_to_percent((metrics.get(key) or {}).get("metric_value"))


def _previous_period_value(
    current_metrics: dict[str, dict[str, Any]],
    extended_metrics: dict[str, dict[str, Any]],
    key: str,
) -> int | None:
    current = _metric_value(current_metrics, key)
    extended = _metric_value(extended_metrics, key)
    if current is None or extended is None:
        return None
    previous = extended - current
    return previous if previous >= 0 else None


def _previous_period_percent(
    current_metrics: dict[str, dict[str, Any]],
    extended_metrics: dict[str, dict[str, Any]],
    key: str,
) -> float | None:
    current_row = current_metrics.get(key) or {}
    extended_row = extended_metrics.get(key) or {}
    current_trends = current_row.get("trends") if isinstance(current_row.get("trends"), list) else []
    extended_trends = extended_row.get("trends") if isinstance(extended_row.get("trends"), list) else []
    previous_trends = extended_trends[: max(0, len(extended_trends) - len(current_trends))]
    values = [_ratio_to_percent(item.get("value")) for item in previous_trends if isinstance(item, dict)]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _douyin_headers(cookie: str, referer: str, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": referer,
        "Origin": "https://creator.douyin.com",
        "Cookie": cookie,
        **(extra_headers or {}),
    }


async def _authorized_get_json(
    url: str,
    cookie: str,
    referer: str,
    *,
    params: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    headers = _douyin_headers(cookie, referer, extra_headers)
    async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=False) as client:
        response = await client.get(url, params=params)
    if response.status_code in {401, 403, 412, 429}:
        raise RuntimeError(f"抖音授权后台数据源不可用或触发平台限制，HTTP {response.status_code}")
    response.raise_for_status()
    return response.json()


async def _authorized_post_json(
    url: str,
    cookie: str,
    referer: str,
    *,
    json_body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    headers = _douyin_headers(cookie, referer, extra_headers)
    async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=False) as client:
        response = await client.post(url, json=json_body or {})
    if response.status_code in {401, 403, 412, 429}:
        raise RuntimeError(f"抖音授权后台数据源不可用或触发平台限制，HTTP {response.status_code}")
    response.raise_for_status()
    return response.json()


def _first_url(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            found = _first_url(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("url", "src", "display_url", "origin_url", "url_list", "urlList"):
            found = _first_url(value.get(key))
            if found:
                return found
    return ""


def _content_from_detail(list_item: dict[str, Any], detail_payload: Any) -> dict[str, Any]:
    detail_item = {}
    if isinstance(detail_payload, dict) and isinstance(detail_payload.get("item_list"), list) and detail_payload["item_list"]:
        first = detail_payload["item_list"][0]
        if isinstance(first, dict):
            detail_item = first
    item = detail_item or list_item
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    summary = item.get("summarize_data") if isinstance(item.get("summarize_data"), dict) else {}
    cover = item.get("Cover") or item.get("cover") or list_item.get("cover")
    timestamp = item.get("create_time") or list_item.get("create_time")
    publish_time = "--"
    if timestamp:
        try:
            publish_time = datetime.fromtimestamp(float(timestamp), tz=ZoneInfo("Asia/Shanghai")).date().isoformat()
        except (TypeError, ValueError, OSError):
            publish_time = str(timestamp)
    return {
        "title": item.get("desc") or list_item.get("description") or list_item.get("title") or "未命名内容",
        "user_id": item.get("author_user_id") or list_item.get("user_id"),
        "publish_time": publish_time,
        "thumbnail": _first_url(cover),
        "views": _safe_int(stats.get("play_count")),
        "likes": _safe_int(stats.get("digg_count")),
        "favorites": _safe_int(stats.get("collect_count")),
        "comments": _safe_int(stats.get("comment_count")),
        "shares": _safe_int(stats.get("share_count")),
        "ctr": _percent_label(summary.get("cover_click_ratio")),
        "avd": _seconds_label(summary.get("play_avg_time")),
        "avp": _percent_label(summary.get("play_finish_ratio")),
    }


async def _fetch_douyin_content_items(
    cookie: str,
    timezone_name: str,
    *,
    content_limit: int,
    extra_headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    now = datetime.now(ZoneInfo(timezone_name))
    start = datetime.combine((now - timedelta(days=90)).date(), time.min, tzinfo=now.tzinfo)
    params = {
        "min_cursor": str(int(start.timestamp() * 1000)),
        "max_cursor": str(int(now.timestamp() * 1000)),
        "count": str(min(max(content_limit, 1), 30)),
        "order_by": "2",
    }
    payload = await _authorized_get_json(
        DOUYIN_ITEM_LIST_URL,
        cookie,
        "https://creator.douyin.com/creator-micro/data-center/content",
        params=params,
        extra_headers=extra_headers,
    )
    rows = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    semaphore = asyncio.Semaphore(2)

    async def detail_for(item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            item_id = str(item.get("id") or "")
            if not item_id:
                return _content_from_detail(item, {})
            try:
                detail = await _authorized_get_json(
                    DOUYIN_ITEM_DETAIL_URL,
                    cookie,
                    "https://creator.douyin.com/creator-micro/data-center/content",
                    params={"item_id": item_id, "time_range": "7"},
                    extra_headers=extra_headers,
                )
            except Exception:  # noqa: BLE001 - detail failure should not drop the work row.
                detail = {}
            return _content_from_detail(item, detail)

    clean_rows = [item for item in rows if isinstance(item, dict)][: min(content_limit, 30)]
    return await asyncio.gather(*(detail_for(item) for item in clean_rows))


class DouyinBaseSource:
    source = "unknown"

    def __init__(self, account_id: str = "", content_limit: int = 50) -> None:
        self.account_id = account_id
        self.content_limit = content_limit

    def configured(self) -> bool:
        return False

    async def fetch_snapshot(self, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        raise SourceUnavailableError("数据源未配置。")


class DouyinOfficialApiSource(DouyinBaseSource):
    source = "official_api"

    def __init__(self, account_id: str = "", data_url: str = "", content_limit: int = 50) -> None:
        super().__init__(account_id, content_limit)
        self.data_url = data_url

    def configured(self) -> bool:
        return bool(self.data_url and os.getenv("DOUYIN_ACCESS_TOKEN"))

    async def fetch_snapshot(self, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        token = os.getenv("DOUYIN_ACCESS_TOKEN", "").strip()
        if not self.data_url or not token:
            raise SourceUnavailableError("抖音官方 API 未配置完整。")
        params: dict[str, str] = {}
        open_id = os.getenv("DOUYIN_OPEN_ID", "").strip()
        if open_id:
            params["open_id"] = open_id
        payload = await fetch_official_json(self.data_url, token, params=params)
        return snapshot_from_payload(
            platform="douyin",
            account_id=self.account_id,
            payload=payload,
            timezone_name=timezone_name,
            key_map=DouyinClient.KEY_MAP,
            custom_key_map=DouyinClient.CUSTOM_KEY_MAP,
            source=self.source,
            message="来自抖音官方 API / OpenAPI 授权数据。",
            content_limit=self.content_limit,
        )


class DouyinCookieSource(DouyinBaseSource):
    source = "authorized_cookie"

    def __init__(self, account_id: str = "", data_url: str = "", content_limit: int = 50) -> None:
        super().__init__(account_id, content_limit)
        self.data_url = data_url

    def configured(self) -> bool:
        return bool(os.getenv("DOUYIN_COOKIE"))

    async def fetch_snapshot(self, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        cookie = os.getenv("DOUYIN_COOKIE", "").strip()
        if not cookie:
            raise SourceUnavailableError("抖音授权后台 Cookie 数据源未配置完整。")
        extra_headers = _extra_headers_from_env("DOUYIN_EXTRA_HEADERS_JSON")
        try:
            return await self._fetch_creator_center_snapshot(cookie, timezone_name, extra_headers)
        except Exception as primary_exc:  # noqa: BLE001 - keep the older authorized JSON fallback.
            fallback_message = f"抖音数据中心接口失败，改用配置 URL：{type(primary_exc).__name__}"
            if not self.data_url:
                raise SourceUnavailableError(fallback_message) from primary_exc
        if extra_headers:
            payload = await fetch_authorized_json_with_headers(
                self.data_url,
                cookie,
                "https://creator.douyin.com/",
                extra_headers,
            )
        else:
            payload = await fetch_authorized_json(self.data_url, cookie, "https://creator.douyin.com/")
        return snapshot_from_payload(
            platform="douyin",
            account_id=self.account_id,
            payload=payload,
            timezone_name=timezone_name,
            key_map=DouyinClient.KEY_MAP,
            custom_key_map=DouyinClient.CUSTOM_KEY_MAP,
            source=self.source,
            message="来自抖音本人账号授权后台 Cookie 数据源。",
            content_limit=self.content_limit,
        )

    async def _fetch_creator_center_snapshot(
        self,
        cookie: str,
        timezone_name: str,
        extra_headers: dict[str, str],
    ) -> dict[str, Any]:
        referer = "https://creator.douyin.com/creator-micro/data-center/operation"
        dashboard_7 = await _authorized_post_json(
            DOUYIN_DASHBOARD_URL,
            cookie,
            referer,
            json_body={"recent_days": 7},
            extra_headers=extra_headers,
        )
        if not isinstance(dashboard_7, dict) or str(dashboard_7.get("status_code")) != "0":
            raise RuntimeError(str((dashboard_7 or {}).get("status_msg") or "抖音数据中心返回异常"))
        dashboard_14 = await _authorized_post_json(
            DOUYIN_DASHBOARD_URL,
            cookie,
            referer,
            json_body={"recent_days": 14},
            extra_headers=extra_headers,
        )
        dashboard_30 = await _authorized_post_json(
            DOUYIN_DASHBOARD_URL,
            cookie,
            referer,
            json_body={"recent_days": 30},
            extra_headers=extra_headers,
        )
        metrics_7 = _metric_map(dashboard_7)
        metrics_14 = _metric_map(dashboard_14)
        metrics_30 = _metric_map(dashboard_30)
        metric_aliases = {
            "views": "play_cnt",
            "likes": "digg_cnt",
            "comments": "comment_cnt",
            "shares": "share_count",
        }
        daily_metrics = {
            key: {
                "today": _metric_value(metrics_7, metric_key),
                "yesterday": _previous_period_value(metrics_7, metrics_14, metric_key),
            }
            for key, metric_key in metric_aliases.items()
        }
        daily_metrics["favorites"] = {"today": None, "yesterday": None}
        custom_daily_metrics = {
            "profile_visits": {
                "today": _metric_value(metrics_7, "homepage_view_cnt"),
                "yesterday": _previous_period_value(metrics_7, metrics_14, "homepage_view_cnt"),
            },
            "cover_click_ratio": {
                "today": _metric_percent(metrics_7, "cover_click_ratio"),
                "yesterday": _previous_period_percent(metrics_7, metrics_14, "cover_click_ratio"),
            },
            "completion_rate": {"today": None, "yesterday": None},
        }
        try:
            content_items = await _fetch_douyin_content_items(
                cookie,
                timezone_name,
                content_limit=self.content_limit,
                extra_headers=extra_headers,
            )
        except Exception:  # noqa: BLE001 - summary data is still usable.
            content_items = []
        account_id = self.account_id
        if not account_id and content_items:
            account_id = str(content_items[0].get("user_id") or "")
        return build_platform_snapshot(
            platform="douyin",
            account_id=account_id,
            timezone_name=timezone_name,
            fans=_metric_value(metrics_7, "total_fans_cnt"),
            metrics={
                "views": _metric_value(metrics_7, "play_cnt"),
                "likes": _metric_value(metrics_7, "digg_cnt"),
                "favorites": None,
                "comments": _metric_value(metrics_7, "comment_cnt"),
                "shares": _metric_value(metrics_7, "share_count"),
            },
            custom_metrics={
                "profile_visits": _metric_value(metrics_7, "homepage_view_cnt"),
                "cover_click_ratio": _metric_percent(metrics_7, "cover_click_ratio"),
                "completion_rate": None,
            },
            daily_metrics=daily_metrics,
            custom_daily_metrics=custom_daily_metrics,
            manual_growth={
                "7d": _metric_value(metrics_7, "net_fans_cnt"),
                "30d": _metric_value(metrics_30, "net_fans_cnt"),
            },
            metric_columns={"current": "近7日", "previous": "前7日"},
            content_items=content_items,
            status="success",
            message="来自抖音本人账号授权后台数据中心。",
            source=self.source,
            raw={
                "summary": {
                    "source": self.source,
                    "dashboard_metrics": len(metrics_7),
                    "content_count": len(content_items),
                }
            },
        )


class DouyinManualSource(DouyinBaseSource):
    source = "manual_import"

    def __init__(self, manual_snapshot: dict[str, Any] | None = None) -> None:
        super().__init__(str((manual_snapshot or {}).get("accountId") or ""))
        self.manual_snapshot = manual_snapshot

    def configured(self) -> bool:
        return isinstance(self.manual_snapshot, dict)

    async def fetch_snapshot(self, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        if not isinstance(self.manual_snapshot, dict):
            raise SourceUnavailableError("抖音手动导入数据不存在。")
        snapshot = dict(self.manual_snapshot)
        source_status = snapshot.get("sourceStatus", {})
        if not isinstance(source_status, dict):
            source_status = {}
        source_status.setdefault("status", "manual")
        source_status.setdefault("source", self.source)
        source_status.setdefault("message", "来自手动导入真实后台数据。")
        snapshot["sourceStatus"] = source_status
        return snapshot


class DouyinUnavailableSource(DouyinBaseSource):
    source = "unavailable"

    def __init__(self, account_id: str = "", messages: list[str] | None = None) -> None:
        super().__init__(account_id)
        self.messages = messages or []

    def configured(self) -> bool:
        return True

    async def fetch_snapshot(self, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        message = "；".join(item for item in self.messages if item) or "缺少抖音官方 API、授权 Cookie 或手动导入数据。"
        return unavailable_platform_snapshot(
            "douyin",
            account_id=self.account_id,
            timezone_name=timezone_name,
            message=message,
        )


def _extra_headers_from_env(name: str) -> dict[str, str]:
    value = os.getenv(name, "").strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    blocked = {"cookie", "authorization"}
    return {
        str(key): str(item)
        for key, item in parsed.items()
        if item not in (None, "") and str(key).lower() not in blocked
    }


class DouyinClient:
    """Safe multi-source adapter for the owner's Douyin creator data."""

    KEY_MAP = {
        "fans": ["fans", "fan_count", "fans_count", "followers", "follower_count", "total_fans"],
        "views": ["views", "view_count", "play_count", "video_views", "播放量"],
        "likes": ["likes", "like_count", "digg_count", "点赞数"],
        "favorites": ["favorites", "favorite_count", "collect_count", "收藏数"],
        "comments": ["comments", "comment_count", "评论数"],
        "shares": ["shares", "share_count", "forward_count", "转发数", "分享数"],
    }
    CUSTOM_KEY_MAP = {
        "profile_visits": ["profile_visits", "homepage_visits", "home_page_visits", "profile_view_count"],
        "cover_click_ratio": ["cover_click_ratio", "cover_click_rate", "ctr", "点击率"],
        "completion_rate": ["completion_rate", "finish_rate", "complete_rate", "完播率"],
    }

    def __init__(
        self,
        account_id: str = "",
        cookie_present: bool = False,
        data_url: str = "",
        *,
        official_data_url: str = "",
        official_config_present: bool = False,
        manual_snapshot: dict[str, Any] | None = None,
        allow_network: bool = True,
        content_limit: int = 50,
    ) -> None:
        self.account_id = account_id
        self.allow_network = allow_network
        self.sources: list[DouyinBaseSource] = []
        if allow_network:
            self.sources.extend(
                [
                    DouyinOfficialApiSource(account_id, official_data_url, content_limit),
                    DouyinCookieSource(account_id, data_url, content_limit),
                ]
            )
        self.sources.extend([DouyinManualSource(manual_snapshot), DouyinUnavailableSource(account_id)])
        self.config_notes = {
            "official": official_config_present,
            "cookie": cookie_present,
        }

    async def _fetch_source_with_retries(
        self,
        source: DouyinBaseSource,
        timezone_name: str,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await source.fetch_snapshot(timezone_name)
            except SourceUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001 - try next safe source after bounded retries.
                last_error = exc
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (2**attempt))
        raise SourceUnavailableError(f"{source.source} 失败：{type(last_error).__name__}")

    async def fetch_snapshot(self, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        messages: list[str] = []
        for source in self.sources:
            if not source.configured():
                messages.append(f"{source.source} 未配置")
                continue
            if source.source == "unavailable":
                source.messages = messages
            try:
                snapshot = await self._fetch_source_with_retries(source, timezone_name)
                source_status = snapshot.get("sourceStatus")
                if (
                    source.source != "unavailable"
                    and isinstance(source_status, dict)
                    and source_status.get("status") == "unavailable"
                ):
                    messages.append(str(source_status.get("message") or f"{source.source} 未识别到可用数据"))
                    continue
                return snapshot
            except SourceUnavailableError as exc:
                messages.append(str(exc))
                continue
        return await DouyinUnavailableSource(self.account_id, messages).fetch_snapshot(timezone_name)
