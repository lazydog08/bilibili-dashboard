from __future__ import annotations

from analytics import (
    format_number,
    normalize_thumbnail_url,
    parse_publish_time,
    safe_percent_change,
)
from fetcher.bilibili_api import BilibiliClient


def test_normalize_thumbnail_url_variants() -> None:
    assert normalize_thumbnail_url("https://example.com/a.png") == "https://example.com/a.png"
    assert normalize_thumbnail_url("//i0.hdslb.com/bfs/archive/a.jpg") == "https://i0.hdslb.com/bfs/archive/a.jpg"
    assert normalize_thumbnail_url("/bfs/archive/a.jpg") == "https://i0.hdslb.com/bfs/archive/a.jpg"
    assert normalize_thumbnail_url("bfs/archive/a.jpg") == "https://i0.hdslb.com/bfs/archive/a.jpg"
    assert normalize_thumbnail_url("") == ""
    assert normalize_thumbnail_url(None) == ""
    assert normalize_thumbnail_url("bfs/archive/already.webp").endswith("already.webp")


def test_safe_percent_change_handles_bad_values() -> None:
    assert safe_percent_change(110, 100) == 0.1
    assert safe_percent_change(100, 0) == 0.0
    assert safe_percent_change(None, 10) == -1.0
    assert safe_percent_change("bad", "also-bad") == 0.0


def test_format_number() -> None:
    assert format_number(3400000) == "3,400,000"
    assert format_number("30300000") == "30,300,000"
    assert format_number(12.5) == "12.5"


def test_parse_publish_time() -> None:
    assert parse_publish_time("2026-05-23T12:07:00+08:00") == "2026-05-23"
    assert parse_publish_time(1779518820) == "2026-05-23"
    assert parse_publish_time(1779518820000) == "2026-05-23"
    assert parse_publish_time("2026/05/23 12:07") == "2026-05-23"


def test_bilibili_archive_manager_item_maps_to_video() -> None:
    client = BilibiliClient(cookie="DedeUserID=516185777")
    video = client._parse_video(
        {
            "Archive": {
                "bvid": "BVtest",
                "title": "当年炒到 18 万一台的手机，现在怎么样了？",
                "cover": "http://i0.hdslb.com/bfs/archive/test.jpg",
                "ptime": 1779624225,
                "duration": 389,
            },
            "stat": {
                "view": 20292,
                "danmaku": 188,
                "reply": 112,
                "favorite": 1063,
                "coin": 2052,
                "share": 116,
                "like": 2248,
            },
        }
    )

    assert video["bvid"] == "BVtest"
    assert video["publish_time"] == "2026-05-24"
    assert video["views"] == 20292
    assert video["likes"] == 2248


def test_bilibili_public_follower_stat_refreshes_channel() -> None:
    client = BilibiliClient(cookie="DedeUserID=516185777")
    channel = {"total_followers": 170_048}

    changed = client._apply_public_follower_stat(channel, {"follower": 170_273})

    assert changed is True
    assert channel["total_followers"] == 170_273


def test_bilibili_public_archive_stat_only_increases_counters() -> None:
    client = BilibiliClient(cookie="DedeUserID=516185777")
    video = {
        "views": 88_036,
        "likes": 1_000,
        "coins": 100,
        "favorites": 200,
        "shares": 30,
        "replies": 20,
    }

    changed = client._apply_public_archive_stat(
        video,
        {
            "view": 90_001,
            "like": 999,
            "coin": 120,
            "favorite": 230,
            "share": 31,
            "reply": 21,
        },
    )

    assert changed is True
    assert video["views"] == 90_001
    assert video["likes"] == 1_000
    assert video["coins"] == 120
    assert video["favorites"] == 230
    assert video["shares"] == 31
    assert video["replies"] == 21
