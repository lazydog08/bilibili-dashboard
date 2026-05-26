from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx


class BilibiliCommentError(RuntimeError):
    def __init__(self, message: str, *, code: int | str | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


RISK_OR_AUTH_CODES = {-352, "-352", -401, "-401", -799, "-799"}
COMMENT_CLOSED_CODES = {12002, "12002"}
VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
REPLY_URL = "https://api.bilibili.com/x/v2/reply"


def comment_headers(*, cookie: str = "", referer: str = "https://www.bilibili.com/") -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.bilibili.com",
        "Referer": referer,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def ensure_comment_payload_success(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BilibiliCommentError("Bilibili comment payload is not JSON object")
    code = payload.get("code")
    if code in {0, "0", None}:
        data = payload.get("data")
        return data if isinstance(data, dict) else {}
    message = str(payload.get("message") or payload.get("msg") or "Bilibili comment API returned non-zero code")
    if code in RISK_OR_AUTH_CODES:
        raise BilibiliCommentError(message, code=code, retryable=False)
    if code in COMMENT_CLOSED_CODES:
        raise BilibiliCommentError("评论区关闭或不可读取。", code=code, retryable=False)
    raise BilibiliCommentError(message, code=code, retryable=False)


async def _request_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = await client.get(url, params=params, headers=headers)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise BilibiliCommentError("Bilibili API returned non-object JSON")
    return payload


async def resolve_video_aid(client: httpx.AsyncClient, video: dict[str, Any]) -> int:
    existing = video.get("aid")
    try:
        aid = int(existing)
    except (TypeError, ValueError):
        aid = 0
    if aid > 0:
        return aid

    bvid = str(video.get("bvid") or "")
    if not bvid:
        raise BilibiliCommentError("缺少 BV 号，无法抓取评论。")
    payload = await _request_json(
        client,
        VIEW_URL,
        params={"bvid": bvid},
        headers=comment_headers(referer=f"https://www.bilibili.com/video/{bvid}/"),
    )
    data = ensure_comment_payload_success(payload)
    try:
        resolved = int(data.get("aid"))
    except (TypeError, ValueError) as exc:
        raise BilibiliCommentError("公开视频信息未返回 aid。") from exc
    if resolved <= 0:
        raise BilibiliCommentError("公开视频信息未返回有效 aid。")
    return resolved


def extract_reply_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("replies", "hots", "top_replies"):
        value = data.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            comment_id = str(item.get("rpid") or item.get("rpid_str") or "")
            if not comment_id or comment_id in seen:
                continue
            seen.add(comment_id)
            items.append(item)
    return items


async def fetch_comment_page(
    client: httpx.AsyncClient,
    video: dict[str, Any],
    *,
    sort: int,
    page_size: int,
    source_rank: str,
    cookie: str = "",
) -> list[dict[str, Any]]:
    aid = await resolve_video_aid(client, video)
    bvid = str(video.get("bvid") or "")
    video_with_aid = {**video, "aid": aid}
    payload = await _request_json(
        client,
        REPLY_URL,
        params={"type": 1, "oid": aid, "sort": sort, "ps": page_size, "pn": 1, "nohot": 0},
        headers=comment_headers(cookie=cookie, referer=f"https://www.bilibili.com/video/{bvid}/" if bvid else "https://www.bilibili.com/"),
    )
    data = ensure_comment_payload_success(payload)
    return [
        normalize_comment_item(item, video=video_with_aid, source_rank=source_rank)
        for item in extract_reply_items(data)
    ]


def _created_at(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def normalize_comment_item(item: dict[str, Any], *, video: dict[str, Any], source_rank: str) -> dict[str, Any]:
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    return {
        "platform": "bilibili",
        "video_id": str(video.get("bvid") or video.get("aid") or ""),
        "bvid": str(video.get("bvid") or ""),
        "aid": str(video.get("aid") or ""),
        "video_title": str(video.get("title") or ""),
        "comment_id": str(item.get("rpid") or item.get("rpid_str") or ""),
        "created_at": _created_at(item.get("ctime")),
        "message": str(content.get("message") or ""),
        "like_count": item.get("like", 0),
        "reply_count": item.get("rcount") or item.get("count") or 0,
        "source_rank": source_rank,
        "is_up_like": bool(item.get("up_action", {}).get("like")) if isinstance(item.get("up_action"), dict) else False,
    }
