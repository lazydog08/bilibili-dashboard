from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CONTACT_PATTERN = re.compile(
    r"((?:1[3-9]\d{9})|(?:https?://\S+)|(?:[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9-.]+)|(?:(?:QQ|qq)[:：]?\s*\d{5,11})|(?:(?:微信|VX|vx|WX|wx|V信|v信|薇)[:：]?\s*[A-Za-z0-9_.-]{4,}))"
)
NOISE_PATTERN = re.compile(r"^[\W_]{1,8}$")

EXACT_PHRASES = {
    "需要回复": ["怎么买", "哪里买", "求推荐", "有链接", "多少钱", "价格"],
    "纠错提醒": ["不对", "错了", "骗人", "没说清楚", "翻车"],
    "争议上升": ["智商税", "割韭菜", "吵起来", "离谱"],
    "选题机会": ["测一下", "想看", "能不能出", "对比一下"],
}
AMBIGUOUS_TERMS = {"吹", "黑"}
COMMENT_DISPLAY_TIMEZONE = timezone(timedelta(hours=8))
DEFAULT_COMMENT_TIMEZONE = "Asia/Shanghai"
COMMENT_PREVIEW_LIMIT = 60
COMMENT_FULL_LIMIT = 320
BILIBILI_BVID_PATTERN = re.compile(r"^BV[0-9A-Za-z]+$")
COMMENT_ID_PATTERN = re.compile(r"^\d+$")
# Comment radar is newest-first, so missing times should sort as oldest.
COMMENT_SORT_EPOCH = "1970-01-01T00:00:00+00:00"


def public_comment_hash(platform: str, comment_id: str | int) -> str:
    raw = f"{platform}:{comment_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def sanitize_comment_text(value: Any, limit: int = 60) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = CONTACT_PATTERN.sub("[已隐藏]", text)
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value)
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _comment_identity(comment: dict[str, Any]) -> tuple[str, str]:
    platform = str(comment.get("platform") or "bilibili")
    raw_id = str(comment.get("comment_id") or "").strip()
    if not raw_id:
        raw_id = f"{comment.get('video_title') or ''}:{comment.get('created_at') or ''}:{comment.get('message') or ''}"
    return platform, raw_id


def _display_timezone(timezone_name: str | None = None) -> timezone | ZoneInfo:
    try:
        return ZoneInfo(timezone_name or DEFAULT_COMMENT_TIMEZONE)
    except ZoneInfoNotFoundError:
        return COMMENT_DISPLAY_TIMEZONE


def _display_created(created: datetime | None, timezone_name: str | None = None) -> tuple[str, str]:
    if not created:
        return "", ""
    local_created = created.astimezone(_display_timezone(timezone_name))
    return local_created.date().isoformat(), local_created.strftime("%Y-%m-%d %H:%M")


def _created_sort_key(created: datetime | None) -> str:
    if not created:
        return COMMENT_SORT_EPOCH
    return created.astimezone(timezone.utc).isoformat()


def _comment_url(comment: dict[str, Any], platform: str, comment_id: str) -> str:
    if platform != "bilibili":
        return ""
    bvid = str(comment.get("bvid") or "").strip()
    if not BILIBILI_BVID_PATTERN.fullmatch(bvid) or not COMMENT_ID_PATTERN.fullmatch(comment_id):
        return ""
    return f"https://www.bilibili.com/video/{bvid}/#reply{comment_id}"


def _public_comment_time_key(item: dict[str, Any]) -> tuple[str, int]:
    return (
        str(item.get("created_sort_key") or COMMENT_SORT_EPOCH),
        _safe_int(item.get("like_count")),
    )


def _keyword_labels(message: str) -> list[str]:
    labels = []
    for label, phrases in EXACT_PHRASES.items():
        if any(phrase in message for phrase in phrases):
            labels.append(label)
    if any(term in message for term in AMBIGUOUS_TERMS) and (
        "吹牛" in message or "硬吹" in message or "黑稿" in message or "黑粉" in message
    ):
        if "争议上升" not in labels:
            labels.append("争议上升")
    return labels


def score_comment(comment: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    message = str(comment.get("message") or "")
    likes = _safe_int(comment.get("like_count"))
    replies = _safe_int(comment.get("reply_count"))
    score = 0.0
    labels = _keyword_labels(message)

    created = _parse_dt(comment.get("created_at"))
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    if created and (reference.astimezone(timezone.utc) - created.astimezone(timezone.utc)).total_seconds() <= 86400:
        score += 20
    score += math.log(likes + 1) * 8
    score += math.log(replies + 1) * 12
    if labels:
        score += 18
    if NOISE_PATTERN.match(message) or len(message) <= 2:
        score -= 20
    if not labels:
        labels.append("普通新增")
    return {"score": max(0, round(score, 1)), "labels": labels}


def public_comment_item(comment: dict[str, Any], timezone_name: str | None = None) -> dict[str, Any]:
    platform, comment_id = _comment_identity(comment)
    scored = score_comment(comment)
    labels = comment.get("labels") if isinstance(comment.get("labels"), list) else scored["labels"]
    source_rank = str(comment.get("source_rank") or "")
    source_label = "最新评论" if source_rank == "latest" else "高赞评论" if source_rank == "ranked" else "评论"
    created = _parse_dt(comment.get("created_at"))
    created_at, created_label = _display_created(created, timezone_name)
    created_sort_key = _created_sort_key(created)
    message = sanitize_comment_text(comment.get("message"), COMMENT_PREVIEW_LIMIT)
    message_full = sanitize_comment_text(comment.get("message"), COMMENT_FULL_LIMIT)
    return {
        "public_id": public_comment_hash(platform, comment_id),
        "platform": platform,
        "video_title": sanitize_comment_text(comment.get("video_title"), 24),
        "message": message,
        "message_full": message_full,
        "has_more": message_full != message,
        "comment_url": _comment_url(comment, platform, comment_id),
        "like_count": _safe_int(comment.get("like_count")),
        "reply_count": _safe_int(comment.get("reply_count")),
        "created_at": created_at,
        "created_label": created_label,
        "created_sort_key": created_sort_key,
        "score": _safe_int(comment.get("score"), int(scored["score"])),
        "labels": labels[:3],
        "source_label": source_label,
    }


def load_comment_cache(path: str | Path) -> dict[str, Any]:
    cache_path = Path(path)
    if not cache_path.exists():
        return {"schema_version": 1, "items": [], "fetch_logs": [], "pushed_comment_ids": []}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": 1,
            "items": [],
            "fetch_logs": [{"status": "failed", "message": "评论缓存不可读。"}],
            "pushed_comment_ids": [],
        }
    if not isinstance(data, dict):
        return {"schema_version": 1, "items": [], "fetch_logs": [], "pushed_comment_ids": []}
    data.setdefault("items", [])
    data.setdefault("fetch_logs", [])
    data.setdefault("pushed_comment_ids", [])
    return data


def build_comment_context(config: Any = None) -> dict[str, Any]:
    if not bool(getattr(config, "enable_comment_insights", False)):
        return {
            "enabled": False,
            "status": "disabled",
            "status_label": "未启用",
            "attention_threshold": 70,
            "summary": [
                {"label": "今日新增", "value": "--"},
                {"label": "需要关注", "value": "--"},
                {"label": "争议上升", "value": "--"},
            ],
            "items": [],
            "database_items": [],
            "message": "评论雷达已预留，当前未启用抓取。",
        }
    try:
        cache = load_comment_cache(getattr(config, "comment_private_path", Path("data/private/comments.json")))
        raw_items = [item for item in cache.get("items", []) if isinstance(item, dict)]
        timezone_name = str(getattr(config, "timezone", DEFAULT_COMMENT_TIMEZONE))
        public_items = [public_comment_item(item, timezone_name) for item in raw_items]
        public_by_id = {item["public_id"]: item for item in public_items}
        items = sorted(public_items, key=lambda item: item["score"], reverse=True)
        attention_threshold = int(getattr(config, "comment_score_push_threshold", 70))
        attention = [
            item
            for item in items
            if item["score"] >= attention_threshold
            or any(label in {"争议上升", "纠错提醒", "需要回复"} for label in item["labels"])
        ]
        latest_raw_items = sorted(
            raw_items,
            key=lambda item: _parse_dt(item.get("created_at")) or datetime.fromtimestamp(0, tz=timezone.utc),
            reverse=True,
        )
        latest_items = [
            public_by_id[public_comment_hash(*_comment_identity(item))]
            for item in latest_raw_items
            if public_comment_hash(*_comment_identity(item)) in public_by_id
        ]
        database_limit = max(20, min(_safe_int(getattr(config, "comment_database_limit", 120), 120), 200))
        display_items = []
        seen_ids: set[str] = set()
        for item in [*attention[:4], *latest_items[:3]]:
            public_id = str(item.get("public_id") or "")
            if public_id in seen_ids:
                continue
            seen_ids.add(public_id)
            display_items.append(item)
            if len(display_items) >= 6:
                break
        display_items.sort(key=_public_comment_time_key, reverse=True)
        return {
            "enabled": True,
            "status": "ready" if items else "empty",
            "status_label": "有需关注评论" if attention else "暂无高优先级评论",
            "attention_threshold": attention_threshold,
            "summary": [
                {"label": "近期缓存", "value": str(len(items))},
                {"label": "需要关注", "value": str(len(attention))},
                {"label": "争议上升", "value": str(sum("争议上升" in item["labels"] for item in items))},
            ],
            "items": display_items,
            "database_items": latest_items[:database_limit],
            "message": "完整私有缓存仍保存在本地，页面展示已隐藏联系方式的评论文本和公开评论链接。",
        }
    except Exception as exc:  # noqa: BLE001 - comments must never break dashboard rendering.
        return {
            "enabled": True,
            "status": "failed",
            "status_label": "抓取失败",
            "attention_threshold": int(getattr(config, "comment_score_push_threshold", 70)),
            "summary": [
                {"label": "今日新增", "value": "--"},
                {"label": "需要关注", "value": "--"},
                {"label": "争议上升", "value": "--"},
            ],
            "items": [],
            "database_items": [],
            "message": f"评论模块降级：{type(exc).__name__}",
        }
