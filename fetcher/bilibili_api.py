from __future__ import annotations

import asyncio
import os
import random
import time
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx

from analytics import (
    DEFAULT_TIMEZONE,
    normalize_thumbnail_url,
    parse_publish_time,
    safe_int,
    safe_minutes,
    safe_ratio,
)


ANTI_RISK_MESSAGE = "Cookie可能已过期或触发风控，请更新 BILIBILI_COOKIE 或改为手动/低频运行"

OVERVIEW_URL = "https://member.bilibili.com/x/h5/data/overview?period=7&s_locale=zh_CN&t={timestamp}"
VIDEO_LIST_URL = "https://member.bilibili.com/x/h5/data/article?pn=1&ps=30&ctype=0&sort=publish_time&order=desc"
VIDEO_DETAIL_URL = "https://member.bilibili.com/x/h5/data/article/detail?bvid={bvid}&period=7"
FAN_DETAIL_URL = "https://member.bilibili.com/x/h5/data/fan/detail?period=7"


class BilibiliAPIError(RuntimeError):
    """Base error for live Bilibili fetch failures."""


class BilibiliAuthOrRiskError(BilibiliAPIError):
    """Raised when the cookie is expired or platform risk controls respond."""


def _contains_auth_or_risk_message(message: Any) -> bool:
    text = str(message or "").lower()
    return any(
        keyword in text
        for keyword in [
            "cookie",
            "csrf",
            "登录",
            "未登录",
            "账号",
            "过期",
            "风控",
            "风险",
            "验证码",
            "频繁",
        ]
    )


def _unwrap_json(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if payload.get("code") in {0, "0", None} and "data" in payload:
        return payload.get("data")
    return payload.get("data", payload)


def _iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def pick_value(obj: Any, keys: list[str], default: Any = None) -> Any:
    for item in _iter_dicts(obj):
        for key in keys:
            if key in item and item.get(key) not in (None, ""):
                return item.get(key)
    return default


def pick_number(obj: Any, keys: list[str], default: int = 0) -> int:
    return safe_int(pick_value(obj, keys, default), default)


def pick_ratio(obj: Any, keys: list[str], default: float = 0.0) -> float:
    return safe_ratio(pick_value(obj, keys, default), default)


def pick_minutes(obj: Any, keys: list[str], default: float = 0.0) -> float:
    return safe_minutes(pick_value(obj, keys, default), default)


def _find_video_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    candidate_keys = [
        "articles",
        "archives",
        "arc_list",
        "list",
        "items",
        "videos",
        "result",
        "data",
    ]
    for item in _iter_dicts(payload):
        for key in candidate_keys:
            candidate = item.get(key)
            if isinstance(candidate, list) and candidate and all(isinstance(row, dict) for row in candidate):
                return candidate
    return []


def _cookie_value(cookie: str, name: str) -> str:
    for part in cookie.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value.strip()
    return ""


def _with_query_params(url: str, params: dict[str, Any]) -> str:
    clean_params = {key: str(value) for key, value in params.items() if value not in (None, "")}
    if not clean_params:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(clean_params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class BilibiliClient:
    def __init__(
        self,
        cookie: str | None = None,
        timeout: float = 20.0,
        max_retries: int = 3,
    ) -> None:
        self._cookie = cookie if cookie is not None else os.getenv("BILIBILI_COOKIE", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.warnings: list[str] = []
        self._mid = _cookie_value(self._cookie, "DedeUserID")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://member.bilibili.com/",
            "Cookie": self._cookie,
        }

    async def _request_json(self, client: httpx.AsyncClient, url: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await client.get(url)
                if response.status_code == 412:
                    raise BilibiliAuthOrRiskError(ANTI_RISK_MESSAGE)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    code = payload.get("code")
                    message = payload.get("message") or payload.get("msg")
                    if code in {-352, "-352"}:
                        raise BilibiliAuthOrRiskError(ANTI_RISK_MESSAGE)
                    if code not in {0, "0", None} and _contains_auth_or_risk_message(message):
                        raise BilibiliAuthOrRiskError(ANTI_RISK_MESSAGE)
                    if code not in {0, "0", None}:
                        raise BilibiliAPIError(f"Bilibili API returned code {code}: {message}")
                return _unwrap_json(payload)
            except BilibiliAuthOrRiskError:
                raise
            except (httpx.HTTPError, ValueError, BilibiliAPIError) as exc:
                last_error = exc
                if attempt >= self.max_retries - 1:
                    break
                await asyncio.sleep((2**attempt) + random.uniform(0.4, 1.2))
        raise BilibiliAPIError(f"Bilibili request failed: {last_error}")

    def _creator_url(self, url: str, **params: Any) -> str:
        if self._mid:
            params.setdefault("mid", self._mid)
        return _with_query_params(url, params)

    async def fetch_overview(self) -> Any:
        if not self._cookie:
            raise BilibiliAuthOrRiskError(ANTI_RISK_MESSAGE)
        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            timestamp = int(time.time() * 1000)
            return await self._request_json(client, self._creator_url(OVERVIEW_URL.format(timestamp=timestamp)))

    async def fetch_video_list(self) -> list[dict[str, Any]]:
        if not self._cookie:
            raise BilibiliAuthOrRiskError(ANTI_RISK_MESSAGE)
        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            payload = await self._request_json(client, self._creator_url(VIDEO_LIST_URL))
        return _find_video_items(payload)[:30]

    async def fetch_video_detail(self, bvid: str) -> Any:
        if not self._cookie:
            raise BilibiliAuthOrRiskError(ANTI_RISK_MESSAGE)
        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            return await self._request_json(client, self._creator_url(VIDEO_DETAIL_URL.format(bvid=bvid)))

    async def fetch_fan_detail(self) -> Any:
        if not self._cookie:
            raise BilibiliAuthOrRiskError(ANTI_RISK_MESSAGE)
        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            return await self._request_json(client, self._creator_url(FAN_DETAIL_URL))

    def _parse_channel(self, overview: Any, fan_detail: Any) -> dict[str, int]:
        return {
            "total_followers": pick_number(overview, ["fans", "fan", "follower", "followers", "total_followers"], 0),
            "follower_delta_7d": pick_number(fan_detail, ["follower_delta_7d", "increase", "incr", "fan_add", "fans_add"], 0),
            "total_views": pick_number(overview, ["view", "views", "play", "total_view", "total_views"], 0),
            "total_likes": pick_number(overview, ["like", "likes", "total_like", "total_likes"], 0),
            "total_coins": pick_number(overview, ["coin", "coins", "total_coin", "total_coins"], 0),
            "total_favorites": pick_number(overview, ["favorite", "favorites", "fav", "total_favorite"], 0),
        }

    def _parse_video(self, item: dict[str, Any], detail: Any | None = None) -> dict[str, Any]:
        detail = detail or {}
        bvid = str(pick_value(item, ["bvid", "bv_id", "bvid_str"], "") or "")
        title = str(pick_value(item, ["title", "name"], "未命名视频") or "未命名视频")
        publish_time = parse_publish_time(
            pick_value(item, ["publish_time", "pubtime", "ctime", "created_at", "created"], None)
        )
        ctr = pick_ratio(detail, ["ctr", "click_rate", "show_click_rate", "impression_ctr"], 0.0)
        avd = pick_minutes(detail, ["avd", "avg_view_duration", "avg_play_duration", "avg_play_time"], 0.0)
        avp = pick_ratio(detail, ["avp", "completion_rate", "avg_view_percent", "avg_play_percent"], 0.0)
        if not ctr:
            self.warnings.append(f"视频 {bvid or title[:12]} 缺少 CTR 字段，已使用 0。")
        if not avd:
            self.warnings.append(f"视频 {bvid or title[:12]} 缺少 AVD 字段，已使用 0。")
        if not avp:
            self.warnings.append(f"视频 {bvid or title[:12]} 缺少 AVP 字段，已使用 0。")

        return {
            "bvid": bvid,
            "title": title,
            "thumbnail": normalize_thumbnail_url(pick_value(item, ["pic", "cover", "thumbnail"], "")),
            "publish_time": publish_time,
            "views": pick_number(item, ["view", "views", "play"], 0),
            "likes": pick_number(item, ["like", "likes"], 0),
            "coins": pick_number(item, ["coin", "coins"], 0),
            "favorites": pick_number(item, ["favorite", "favorites", "fav"], 0),
            "shares": pick_number(item, ["share", "shares"], 0),
            "replies": pick_number(item, ["reply", "replies", "comment", "comments"], 0),
            "ctr": ctr,
            "avd_minutes": avd,
            "avp_percent": avp,
            "follower_gain": pick_number(detail, ["follower_gain", "fans_gain", "fan_gain"], 0),
            "impressions": pick_number(detail, ["impression", "impressions", "show", "shows"], 0),
        }

    async def fetch_snapshot(self) -> dict[str, Any]:
        if not self._cookie:
            raise BilibiliAuthOrRiskError(ANTI_RISK_MESSAGE)

        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            timestamp = int(time.time() * 1000)
            overview = await self._request_json(client, self._creator_url(OVERVIEW_URL.format(timestamp=timestamp)))
            await asyncio.sleep(random.uniform(0.8, 1.8))
            video_payload = await self._request_json(client, self._creator_url(VIDEO_LIST_URL))
            await asyncio.sleep(random.uniform(0.8, 1.8))
            fan_detail = await self._request_json(client, self._creator_url(FAN_DETAIL_URL))
            videos = _find_video_items(video_payload)[:30]

            details: dict[str, Any] = {}
            for index in range(0, len(videos), 2):
                group = videos[index : index + 2]

                async def fetch_one(item: dict[str, Any]) -> tuple[str, Any | None]:
                    bvid = str(pick_value(item, ["bvid", "bv_id", "bvid_str"], "") or "")
                    if not bvid:
                        return "", None
                    try:
                        detail = await self._request_json(
                            client,
                            self._creator_url(VIDEO_DETAIL_URL.format(bvid=bvid)),
                        )
                    except BilibiliAuthOrRiskError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - keep one failed detail from breaking rendering.
                        self.warnings.append(f"视频 {bvid} 明细获取失败，已使用列表数据回退：{exc}")
                        return bvid, None
                    return bvid, detail

                for bvid, detail in await asyncio.gather(*(fetch_one(item) for item in group)):
                    if bvid:
                        details[bvid] = detail
                if index + 2 < len(videos):
                    await asyncio.sleep(random.uniform(0.8, 1.8))

        now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
        parsed_videos = []
        for item in videos:
            bvid = str(pick_value(item, ["bvid", "bv_id", "bvid_str"], "") or "")
            parsed_videos.append(self._parse_video(item, details.get(bvid)))

        return {
            "date": now.date().isoformat(),
            "updated_at": now.isoformat(timespec="seconds"),
            "channel": self._parse_channel(overview, fan_detail),
            "videos": parsed_videos,
            "warnings": sorted(set(self.warnings)),
        }
