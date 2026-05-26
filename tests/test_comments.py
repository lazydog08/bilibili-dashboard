from __future__ import annotations

from datetime import datetime, timedelta
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from comments import build_comment_context, public_comment_hash, public_comment_item, sanitize_comment_text, score_comment
from config import PROJECT_ROOT
from fetcher.bilibili_comments import (
    BilibiliCommentError,
    ensure_comment_payload_success,
    extract_reply_items,
    normalize_comment_item,
)


def test_public_comment_hash_is_stable_short_public_id() -> None:
    assert public_comment_hash("bilibili", "123") == public_comment_hash("bilibili", "123")
    assert len(public_comment_hash("bilibili", "123")) == 16


def test_sanitize_comment_text_redacts_contact_and_limits_length() -> None:
    text = sanitize_comment_text("电话 13800138000 wx lazy.dog QQ 123456 user@example.com https://example.com/path", limit=80)

    assert "13800138000" not in text
    assert "lazy.dog" not in text
    assert "123456" not in text
    assert "user@example.com" not in text
    assert "https://example.com" not in text
    assert len(text) <= 83


def test_comment_scoring_avoids_ambiguous_false_positives() -> None:
    now = datetime.now()
    for message in ["韭菜炒蛋很好吃", "这个吹风机不错", "黑色机身挺好看"]:
        scored = score_comment(
            {"message": message, "like_count": 0, "reply_count": 0, "created_at": now.isoformat()},
            now=now + timedelta(minutes=10),
        )
        assert "争议上升" not in scored["labels"]

    controversial = score_comment({"message": "这里是不是硬吹了", "like_count": 10, "reply_count": 3})
    assert "争议上升" in controversial["labels"]

    duplicate = score_comment({"message": "智商税 硬吹", "like_count": 0, "reply_count": 0})
    assert duplicate["labels"].count("争议上升") == 1


def test_comment_scoring_accepts_unix_timestamp_created_at() -> None:
    now = datetime.fromisoformat("2026-02-03T02:00:00+00:00")
    scored = score_comment(
        {"message": "想看对比一下", "like_count": 1, "reply_count": 0, "created_at": 1770080400},
        now=now,
    )

    assert scored["score"] >= 20
    assert "选题机会" in scored["labels"]


def test_comment_context_is_default_off() -> None:
    context = build_comment_context(SimpleNamespace(enable_comment_insights=False))

    assert context["enabled"] is False
    assert context["status"] == "disabled"
    assert context["items"] == []


def test_comment_context_handles_malformed_private_cache(tmp_path) -> None:
    cache_path = tmp_path / "comments.json"
    cache_path.write_text("{not-json", encoding="utf-8")
    context = build_comment_context(
        SimpleNamespace(
            enable_comment_insights=True,
            comment_private_path=cache_path,
            comment_score_push_threshold=66,
        )
    )

    assert context["enabled"] is True
    assert context["status"] == "empty"
    assert context["items"] == []


def test_comment_context_exposes_comment_source_label(tmp_path) -> None:
    cache_path = tmp_path / "comments.json"
    cache_path.write_text(
        """
        {
          "schema_version": 1,
          "items": [
            {
              "platform": "bilibili",
              "comment_id": "1",
              "video_title": "测试视频",
              "message": "想看对比一下",
              "like_count": 2,
              "reply_count": 1,
              "source_rank": "latest"
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    context = build_comment_context(
        SimpleNamespace(
            enable_comment_insights=True,
            comment_private_path=cache_path,
            comment_score_push_threshold=1,
        )
    )

    assert context["items"][0]["source_label"] == "最新评论"
    assert context["items"][0]["created_at"] == ""
    assert context["items"][0]["created_sort_key"] == "1970-01-01T00:00:00+00:00"


def test_public_comment_item_exposes_expandable_text_and_direct_link() -> None:
    long_message = "这是一条很长的评论，" * 20

    item = public_comment_item(
        {
            "platform": "bilibili",
            "comment_id": "123",
            "bvid": "BV1ABC123DEF",
            "video_title": "测试视频",
            "message": long_message,
            "like_count": 2,
            "reply_count": 1,
            "created_at": "2026-05-02T01:00:00+00:00",
            "source_rank": "latest",
        },
        "Asia/Shanghai",
    )

    assert item["message"].endswith("...")
    assert item["message_full"].startswith("这是一条很长的评论")
    assert len(item["message_full"]) > len(item["message"])
    assert item["has_more"] is True
    assert item["comment_url"] == "https://www.bilibili.com/video/BV1ABC123DEF/#reply123"
    assert item["created_sort_key"] == "2026-05-02T01:00:00+00:00"
    assert "comment_id" not in item


def test_comment_context_mixes_attention_and_latest_items(tmp_path) -> None:
    cache_path = tmp_path / "comments.json"
    cache_path.write_text(
        """
        {
          "schema_version": 1,
          "items": [
            {
              "platform": "bilibili",
              "comment_id": "hot",
              "video_title": "测试视频",
              "message": "这里是不是硬吹了",
              "like_count": 10,
              "reply_count": 3,
              "created_at": "2026-05-01T00:00:00+00:00",
              "source_rank": "ranked"
            },
            {
              "platform": "bilibili",
              "comment_id": "new",
              "video_title": "测试视频",
              "message": "刚刚看到",
              "like_count": 0,
              "reply_count": 0,
              "created_at": "2026-05-02T00:00:00+00:00",
              "source_rank": "latest"
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    context = build_comment_context(
        SimpleNamespace(
            enable_comment_insights=True,
            comment_private_path=cache_path,
            comment_score_push_threshold=66,
        )
    )

    messages = [item["message"] for item in context["items"]]
    assert "这里是不是硬吹了" in messages
    assert "刚刚看到" in messages
    assert messages[:2] == ["刚刚看到", "这里是不是硬吹了"]


def test_comment_context_keeps_latest_order_within_same_day(tmp_path) -> None:
    cache_path = tmp_path / "comments.json"
    cache_path.write_text(
        """
        {
          "schema_version": 1,
          "items": [
            {
              "platform": "bilibili",
              "comment_id": "early",
              "video_title": "测试视频",
              "message": "早一点",
              "like_count": 0,
              "reply_count": 0,
              "created_at": "2026-05-02T01:00:00+00:00",
              "source_rank": "latest"
            },
            {
              "platform": "bilibili",
              "comment_id": "late",
              "video_title": "测试视频",
              "message": "晚一点",
              "like_count": 0,
              "reply_count": 0,
              "created_at": "2026-05-02T23:00:00+00:00",
              "source_rank": "latest"
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    context = build_comment_context(
        SimpleNamespace(
            enable_comment_insights=True,
            comment_private_path=cache_path,
            comment_score_push_threshold=70,
        )
    )

    assert [item["message"] for item in context["items"][:2]] == ["晚一点", "早一点"]
    assert context["items"][0]["created_at"] == "2026-05-03"


def test_comment_context_uses_likes_as_time_tiebreaker(tmp_path) -> None:
    cache_path = tmp_path / "comments.json"
    cache_path.write_text(
        """
        {
          "schema_version": 1,
          "items": [
            {
              "platform": "bilibili",
              "comment_id": "low",
              "video_title": "测试视频",
              "message": "同一时间低赞",
              "like_count": 1,
              "reply_count": 0,
              "created_at": "2026-05-02T01:00:00+00:00",
              "source_rank": "latest"
            },
            {
              "platform": "bilibili",
              "comment_id": "high",
              "video_title": "测试视频",
              "message": "同一时间高赞",
              "like_count": 9,
              "reply_count": 0,
              "created_at": "2026-05-02T01:00:00+00:00",
              "source_rank": "latest"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    context = build_comment_context(
        SimpleNamespace(
            enable_comment_insights=True,
            comment_private_path=cache_path,
            comment_score_push_threshold=70,
        )
    )

    assert [item["message"] for item in context["items"][:2]] == ["同一时间高赞", "同一时间低赞"]


def test_comment_context_exposes_database_items_with_display_time(tmp_path) -> None:
    cache_path = tmp_path / "comments.json"
    cache_path.write_text(
        """
        {
          "schema_version": 1,
          "items": [
            {
              "platform": "bilibili",
              "comment_id": "early",
              "video_title": "测试视频",
              "message": "早一点",
              "like_count": 0,
              "reply_count": 0,
              "created_at": "2026-05-02T01:00:00+00:00",
              "source_rank": "latest"
            },
            {
              "platform": "bilibili",
              "comment_id": "late",
              "video_title": "测试视频",
              "message": "晚一点",
              "like_count": 5,
              "reply_count": 2,
              "created_at": "2026-05-02T23:00:00+00:00",
              "source_rank": "ranked"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    context = build_comment_context(
        SimpleNamespace(
            enable_comment_insights=True,
            comment_private_path=cache_path,
            comment_score_push_threshold=66,
        )
    )

    assert [item["message"] for item in context["database_items"]] == ["晚一点", "早一点"]
    assert context["database_items"][0]["created_label"] == "2026-05-03 07:00"
    assert context["database_items"][0]["created_at"] == "2026-05-03"
    assert context["attention_threshold"] == 66
    assert "comment_id" not in context["database_items"][0]


def test_comment_context_uses_configured_timezone_for_display_time(tmp_path) -> None:
    cache_path = tmp_path / "comments.json"
    cache_path.write_text(
        """
        {
          "schema_version": 1,
          "items": [
            {
              "platform": "bilibili",
              "comment_id": "utc",
              "video_title": "测试视频",
              "message": "晚一点",
              "like_count": 0,
              "reply_count": 0,
              "created_at": "2026-05-02T23:00:00+00:00",
              "source_rank": "latest"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    context = build_comment_context(
        SimpleNamespace(
            enable_comment_insights=True,
            comment_private_path=cache_path,
            comment_score_push_threshold=70,
            timezone="UTC",
        )
    )

    assert context["database_items"][0]["created_label"] == "2026-05-02 23:00"
    assert context["database_items"][0]["created_at"] == "2026-05-02"


def test_private_comment_cache_is_ignored_by_git() -> None:
    assert "data/private/" in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    ignored = subprocess.run(
        ["git", "check-ignore", "data/private/comments.json"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert ignored.returncode == 0


def test_bilibili_comment_payload_validation() -> None:
    assert ensure_comment_payload_success({"code": 0, "data": {"replies": []}}) == {"replies": []}

    with pytest.raises(BilibiliCommentError) as exc:
        ensure_comment_payload_success({"code": -352, "message": "风控"})
    assert exc.value.code == -352
    assert exc.value.retryable is False


def test_extract_reply_items_deduplicates_reply_sources() -> None:
    data = {
        "replies": [{"rpid": 1}, {"rpid": 2}, {"rpid": ""}],
        "hots": [{"rpid": 1}, {"rpid": 3}],
        "top_replies": [{"rpid": 3}, {"rpid": 4}],
    }

    assert [item["rpid"] for item in extract_reply_items(data)] == [1, 2, 3, 4]


def test_normalize_bilibili_comment_item_maps_public_fields() -> None:
    item = normalize_comment_item(
        {
            "rpid": 123,
            "ctime": 1770000000,
            "like": 9,
            "rcount": 2,
            "content": {"message": "想看对比一下"},
            "member": {"uname": "viewer"},
            "up_action": {"like": True},
        },
        video={"bvid": "BV1", "aid": 456, "title": "测试视频"},
        source_rank="latest",
    )

    assert item["platform"] == "bilibili"
    assert item["comment_id"] == "123"
    assert item["bvid"] == "BV1"
    assert item["message"] == "想看对比一下"
    assert item["created_at"] == "2026-02-02T02:40:00+00:00"
    assert item["like_count"] == 9
    assert item["reply_count"] == 2
    assert item["is_up_like"] is True
    assert "member_name" not in item
