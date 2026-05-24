from __future__ import annotations

import asyncio
import json
import os
from typing import Any

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
XHS_BASE_URL = "https://creator.xiaohongshu.com"
XHS_PERSONAL_INFO_URL = f"{XHS_BASE_URL}/api/galaxy/creator/home/personal_info"
XHS_ACCOUNT_BASE_URL = f"{XHS_BASE_URL}/api/galaxy/v2/creator/datacenter/account/base"


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


def _xhs_headers(cookie: str, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://creator.xiaohongshu.com/new/home",
        "Origin": XHS_BASE_URL,
        "Cookie": cookie,
        **(extra_headers or {}),
    }


async def _authorized_xhs_get_json(
    url: str,
    cookie: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    headers = _xhs_headers(cookie, extra_headers)
    async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=False) as client:
        response = await client.get(url)
    if response.status_code in {401, 403, 406, 412, 429}:
        raise RuntimeError(f"小红书授权后台数据源不可用或触发平台限制，HTTP {response.status_code}")
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(str(payload.get("msg") or payload.get("message") or "小红书接口返回失败"))
    return payload


def _payload_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _pick_personal_fans(personal: dict[str, Any]) -> int | None:
    grow_info = personal.get("grow_info") if isinstance(personal.get("grow_info"), dict) else {}
    return _safe_int(grow_info.get("fans_count")) or _safe_int(personal.get("fans_count"))


def _period_data(account_base: dict[str, Any], key: str) -> dict[str, Any]:
    value = account_base.get(key)
    return value if isinstance(value, dict) else {}


class XiaohongshuBaseSource:
    source = "unknown"

    def __init__(self, account_id: str = "", content_limit: int = 50) -> None:
        self.account_id = account_id
        self.content_limit = content_limit

    def configured(self) -> bool:
        return False

    async def fetch_snapshot(self, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        raise SourceUnavailableError("数据源未配置。")


class XiaohongshuOfficialApiSource(XiaohongshuBaseSource):
    source = "official_api"

    def __init__(self, account_id: str = "", data_url: str = "", content_limit: int = 50) -> None:
        super().__init__(account_id, content_limit)
        self.data_url = data_url

    def configured(self) -> bool:
        return bool(self.data_url and os.getenv("XIAOHONGSHU_ACCESS_TOKEN"))

    async def fetch_snapshot(self, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        token = os.getenv("XIAOHONGSHU_ACCESS_TOKEN", "").strip()
        if not self.data_url or not token:
            raise SourceUnavailableError("小红书官方 API 未配置完整。")
        params: dict[str, str] = {}
        open_id = os.getenv("XIAOHONGSHU_OPEN_ID", "").strip()
        if open_id:
            params["open_id"] = open_id
        payload = await fetch_official_json(self.data_url, token, params=params)
        return snapshot_from_payload(
            platform="xiaohongshu",
            account_id=self.account_id,
            payload=payload,
            timezone_name=timezone_name,
            key_map=XiaohongshuClient.KEY_MAP,
            custom_key_map=XiaohongshuClient.CUSTOM_KEY_MAP,
            source=self.source,
            message="来自小红书官方 / 蒲公英 / 创作者授权接口数据。",
            content_limit=self.content_limit,
        )


class XiaohongshuCookieSource(XiaohongshuBaseSource):
    source = "authorized_cookie"

    def __init__(self, account_id: str = "", data_url: str = "", content_limit: int = 50) -> None:
        super().__init__(account_id, content_limit)
        self.data_url = data_url

    def configured(self) -> bool:
        return bool(os.getenv("XIAOHONGSHU_COOKIE"))

    async def fetch_snapshot(self, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        cookie = os.getenv("XIAOHONGSHU_COOKIE", "").strip()
        if not cookie:
            raise SourceUnavailableError("小红书授权后台 Cookie 数据源未配置完整。")
        extra_headers = _extra_headers_from_env("XIAOHONGSHU_EXTRA_HEADERS_JSON")
        if not self.data_url or "creator.xiaohongshu.com" in self.data_url:
            try:
                return await self._fetch_creator_center_snapshot(cookie, timezone_name, extra_headers)
            except Exception as primary_exc:  # noqa: BLE001 - keep the older configurable URL fallback.
                if not self.data_url:
                    raise SourceUnavailableError(f"小红书数据中心接口失败：{type(primary_exc).__name__}") from primary_exc
        if extra_headers:
            payload = await fetch_authorized_json_with_headers(
                self.data_url,
                cookie,
                "https://creator.xiaohongshu.com/",
                extra_headers,
            )
        else:
            payload = await fetch_authorized_json(self.data_url, cookie, "https://creator.xiaohongshu.com/")
        return snapshot_from_payload(
            platform="xiaohongshu",
            account_id=self.account_id,
            payload=payload,
            timezone_name=timezone_name,
            key_map=XiaohongshuClient.KEY_MAP,
            custom_key_map=XiaohongshuClient.CUSTOM_KEY_MAP,
            source=self.source,
            message="来自小红书本人账号授权后台 Cookie 数据源。",
            content_limit=self.content_limit,
        )

    async def _fetch_creator_center_snapshot(
        self,
        cookie: str,
        timezone_name: str,
        extra_headers: dict[str, str],
    ) -> dict[str, Any]:
        personal_payload, account_payload = await asyncio.gather(
            _authorized_xhs_get_json(XHS_PERSONAL_INFO_URL, cookie, extra_headers=extra_headers),
            _authorized_xhs_get_json(XHS_ACCOUNT_BASE_URL, cookie, extra_headers=extra_headers),
        )
        personal = _payload_data(personal_payload)
        account_base = _payload_data(account_payload)
        seven = _period_data(account_base, "seven")
        thirty = _period_data(account_base, "thirty")
        account_id = self.account_id or str(personal.get("red_num") or "")
        metrics = {
            "views": _safe_int(seven.get("view_count")),
            "likes": _safe_int(seven.get("like_count")),
            "favorites": _safe_int(seven.get("collect_count")),
            "comments": _safe_int(seven.get("comment_count")),
            "shares": _safe_int(seven.get("share_count")),
        }
        custom_metrics = {
            "note_impressions": _safe_int(seven.get("impl_count")),
            "search_entries": None,
            "cover_click_rate": _safe_float(seven.get("cover_click_rate")),
            "avg_view_time": _safe_float(seven.get("avg_view_time")),
            "completion_rate": _safe_float(seven.get("video_full_view_rate")),
            "profile_visits": _safe_int(seven.get("home_view_count")),
        }
        return build_platform_snapshot(
            platform="xiaohongshu",
            account_id=account_id,
            timezone_name=timezone_name,
            fans=_pick_personal_fans(personal),
            metrics=metrics,
            custom_metrics=custom_metrics,
            daily_metrics={key: {"today": value, "yesterday": None} for key, value in metrics.items()},
            custom_daily_metrics={key: {"today": value, "yesterday": None} for key, value in custom_metrics.items()},
            manual_growth={
                "7d": _safe_int(seven.get("net_rise_fans_count")),
                "30d": _safe_int(thirty.get("net_rise_fans_count")),
            },
            metric_columns={"current": "近7日", "previous": "对比期"},
            content_items=[],
            status="success",
            message="来自小红书本人账号授权后台数据中心；作品列表接口需要额外平台签名，当前仅使用可安全读取的汇总指标。",
            source=self.source,
            raw={
                "summary": {
                    "source": self.source,
                    "personal_info": bool(personal),
                    "account_metrics": len(seven),
                    "content_count": 0,
                }
            },
        )


class XiaohongshuManualSource(XiaohongshuBaseSource):
    source = "manual_import"

    def __init__(self, manual_snapshot: dict[str, Any] | None = None) -> None:
        super().__init__(str((manual_snapshot or {}).get("accountId") or ""))
        self.manual_snapshot = manual_snapshot

    def configured(self) -> bool:
        return isinstance(self.manual_snapshot, dict)

    async def fetch_snapshot(self, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        if not isinstance(self.manual_snapshot, dict):
            raise SourceUnavailableError("小红书手动导入数据不存在。")
        snapshot = dict(self.manual_snapshot)
        source_status = snapshot.get("sourceStatus", {})
        if not isinstance(source_status, dict):
            source_status = {}
        source_status.setdefault("status", "manual")
        source_status.setdefault("source", self.source)
        source_status.setdefault("message", "来自手动导入真实后台数据。")
        snapshot["sourceStatus"] = source_status
        return snapshot


class XiaohongshuUnavailableSource(XiaohongshuBaseSource):
    source = "unavailable"

    def __init__(self, account_id: str = "", messages: list[str] | None = None) -> None:
        super().__init__(account_id)
        self.messages = messages or []

    def configured(self) -> bool:
        return True

    async def fetch_snapshot(self, timezone_name: str = "Asia/Shanghai") -> dict[str, Any]:
        message = "；".join(item for item in self.messages if item) or "缺少小红书官方 API、授权 Cookie 或手动导入数据。"
        return unavailable_platform_snapshot(
            "xiaohongshu",
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


class XiaohongshuClient:
    """Safe multi-source adapter for the owner's Xiaohongshu creator data."""

    KEY_MAP = {
        "fans": ["fans", "fan_count", "fans_count", "followers", "follower_count", "total_fans"],
        "views": ["views", "view_count", "read_count", "reads", "阅读量"],
        "likes": ["likes", "like_count", "点赞数"],
        "favorites": ["favorites", "favorite_count", "collect_count", "收藏数"],
        "comments": ["comments", "comment_count", "评论数"],
        "shares": ["shares", "share_count", "forward_count", "转发数", "分享数"],
    }
    CUSTOM_KEY_MAP = {
        "note_impressions": ["note_impressions", "impressions", "exposure_count", "expose_count", "笔记曝光量"],
        "search_entries": ["search_entries", "search_entry_count", "search_visit_count", "搜索进入量"],
        "cover_click_rate": ["cover_click_rate", "cover_click_ratio", "ctr", "点击率"],
        "avg_view_time": ["avg_view_time", "avg_view_duration", "average_view_time", "平均观看时长"],
        "completion_rate": ["completion_rate", "video_full_view_rate", "finish_rate", "完播率"],
        "profile_visits": ["profile_visits", "home_view_count", "主页访问量"],
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
        self.sources: list[XiaohongshuBaseSource] = []
        if allow_network:
            self.sources.extend(
                [
                    XiaohongshuOfficialApiSource(account_id, official_data_url, content_limit),
                    XiaohongshuCookieSource(account_id, data_url, content_limit),
                ]
            )
        self.sources.extend([XiaohongshuManualSource(manual_snapshot), XiaohongshuUnavailableSource(account_id)])
        self.config_notes = {
            "official": official_config_present,
            "cookie": cookie_present,
        }

    async def _fetch_source_with_retries(
        self,
        source: XiaohongshuBaseSource,
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
        return await XiaohongshuUnavailableSource(self.account_id, messages).fetch_snapshot(timezone_name)
