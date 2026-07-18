from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from analytics import (
    format_number,
    normalize_thumbnail_url,
    parse_publish_time,
    safe_percent_change,
)
from fetcher.bilibili_api import BilibiliAPIError, BilibiliClient


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


def test_bilibili_missing_optional_depth_metrics_do_not_mark_snapshot_partial() -> None:
    client = BilibiliClient(cookie="DedeUserID=516185777")

    video = client._parse_video(
        {
            "Archive": {
                "bvid": "BVnew",
                "title": "刚发布的新视频",
                "cover": "http://i0.hdslb.com/bfs/archive/new.jpg",
                "ptime": 1779624225,
                "duration": 60,
            },
            "stat": {
                "view": 100,
                "like": 10,
                "coin": 2,
                "favorite": 3,
                "share": 1,
                "reply": 4,
            },
        }
    )

    assert video["ctr"] is None
    assert video["avd_minutes"] is None
    assert video["avp_percent"] is None
    assert video["metric_availability"]["ctr"] is False
    assert client.warnings == []


def test_bilibili_public_follower_stat_refreshes_channel() -> None:
    client = BilibiliClient(cookie="DedeUserID=516185777")
    channel = {"total_followers": 170_048}

    changed = client._apply_public_follower_stat(channel, {"follower": 170_273})

    assert changed is True
    assert channel["total_followers"] == 170_273


def test_bilibili_public_follower_fetch_does_not_require_creator_cookie(monkeypatch) -> None:
    client = BilibiliClient(cookie="")
    seen: list[str] = []

    async def fake_request_json(_http_client, url: str) -> dict[str, int]:
        seen.append(url)
        return {"mid": 516185777, "follower": 188_901}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    follower = asyncio.run(client.fetch_public_follower("516185777"))

    assert follower == 188_901
    assert seen == ["https://api.bilibili.com/x/relation/stat?vmid=516185777"]


def test_bilibili_public_snapshot_strictly_verifies_owner_but_keeps_non_full_api_partial(monkeypatch) -> None:
    client = BilibiliClient(cookie="")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    publish_times = [now - timedelta(days=1), now - timedelta(days=12), now - timedelta(days=31)]
    bvids = ["BVnew", "BVmiddle", "BVold"]

    async def fake_request_json(_http_client, url: str):  # noqa: ANN001
        if "/card?" in url:
            return {
                "card": {"mid": "516185777", "name": "懒狗小黑"},
                "follower": 188_999,
                "archive_count": 91,
            }
        if "/search/all/v2?" in url:
            return {
                "result": [
                    {
                        "result_type": "bili_user",
                        "data": [
                            {
                                "mid": 516185777,
                                "uname": "懒狗小黑",
                                "res": [
                                    {"bvid": bvid, "pubdate": int(published.timestamp())}
                                    for bvid, published in zip(bvids, publish_times, strict=True)
                                ],
                            },
                            {"mid": 1, "uname": "懒狗小黑", "res": [{"bvid": "BVwrong"}]},
                        ],
                    }
                ]
            }
        bvid = url.rsplit("=", 1)[-1]
        index = bvids.index(bvid)
        return {
            "bvid": bvid,
            "title": f"节目 {index}",
            "pubdate": int(publish_times[index].timestamp()),
            "pic": "//i0.hdslb.com/test.jpg",
            "owner": {"mid": 516185777, "name": "懒狗小黑"},
            "stat": {"view": 100 + index, "like": 10, "reply": 1},
        }

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    snapshot = asyncio.run(client.fetch_public_snapshot("516185777"))

    assert [video["bvid"] for video in snapshot["videos"]] == bvids
    assert snapshot["videos"][0]["ctr"] is None
    assert snapshot["public_listing"]["status"] == "partial"
    assert "不是全量稿件接口" in snapshot["warnings"][0]


def test_bilibili_public_snapshot_marks_recent_three_as_partial(monkeypatch) -> None:
    client = BilibiliClient(cookie="")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    bvids = ["BVa", "BVb", "BVc"]

    async def fake_request_json(_http_client, url: str):  # noqa: ANN001
        if "/card?" in url:
            return {
                "card": {"mid": 516185777, "name": "懒狗小黑"},
                "follower": 188_999,
                "archive_count": 91,
            }
        if "/search/all/v2?" in url:
            return {
                "result": [
                    {
                        "result_type": "bili_user",
                        "data": [{"mid": 516185777, "uname": "懒狗小黑", "res": [{"bvid": item} for item in bvids]}],
                    }
                ]
            }
        bvid = url.rsplit("=", 1)[-1]
        return {
            "bvid": bvid,
            "title": bvid,
            "pubdate": int((now - timedelta(days=bvids.index(bvid) + 1)).timestamp()),
            "owner": {"mid": 516185777},
            "stat": {"view": 1},
        }

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    snapshot = asyncio.run(client.fetch_public_snapshot("516185777"))

    assert snapshot["public_listing"]["status"] == "partial"
    assert "可能仍有遗漏" in snapshot["warnings"][0]


def test_bilibili_public_snapshot_rejects_view_from_wrong_owner(monkeypatch) -> None:
    client = BilibiliClient(cookie="")

    async def fake_request_json(_http_client, url: str):  # noqa: ANN001
        if "/card?" in url:
            return {"card": {"mid": 516185777, "name": "懒狗小黑"}, "follower": 1, "archive_count": 1}
        if "/search/all/v2?" in url:
            return {
                "result": [
                    {
                        "result_type": "bili_user",
                        "data": [{"mid": 516185777, "uname": "懒狗小黑", "res": [{"bvid": "BVwrong"}]}],
                    }
                ]
            }
        return {"bvid": "BVwrong", "title": "转载", "owner": {"mid": 123}, "stat": {"view": 99}}

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    with pytest.raises(BilibiliAPIError, match="rejected every candidate"):
        asyncio.run(client.fetch_public_snapshot("516185777"))


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


def test_bilibili_snapshot_skips_fan_detail_when_overview_has_fan_metrics(monkeypatch) -> None:
    client = BilibiliClient(cookie="DedeUserID=516185777")

    async def fake_request_first_json(self, http_client, urls, label):  # noqa: ANN001, ARG001
        if label == "overview":
            return {
                "total_fans": 170_888,
                "incr_fans": 42,
                "total_click": 123_456,
                "total_like": 789,
            }
        if label == "fan detail":
            raise AssertionError("fan detail should not be requested when overview already has fan metrics")
        return {}

    async def fake_fetch_video_payloads(self, http_client, timestamp):  # noqa: ANN001, ARG001
        return [
            [
                {
                    "bvid": "BVtest",
                    "title": "测试视频",
                    "cover": "http://i0.hdslb.com/bfs/archive/test.jpg",
                    "ptime": 1779624225,
                    "duration": 60,
                    "view": 100,
                    "like": 10,
                    "coin": 2,
                    "favorite": 3,
                    "share": 1,
                    "reply": 4,
                    "tm_rate": 100,
                    "full_play_ratio": 5000,
                    "avg_play_time": 30,
                    "total_new_attention_cnt": 5,
                }
            ]
        ]

    async def fake_request_json(self, http_client, url):  # noqa: ANN001, ARG001
        return {}

    async def fake_enrich_public_stats(self, channel, videos):  # noqa: ANN001, ARG001
        return None

    monkeypatch.setattr(BilibiliClient, "_request_first_json", fake_request_first_json)
    monkeypatch.setattr(BilibiliClient, "_fetch_video_payloads", fake_fetch_video_payloads)
    monkeypatch.setattr(BilibiliClient, "_request_json", fake_request_json)
    monkeypatch.setattr(BilibiliClient, "_enrich_public_stats", fake_enrich_public_stats)

    snapshot = asyncio.run(client.fetch_snapshot())

    assert snapshot["channel"]["total_followers"] == 170_888
    assert snapshot["channel"]["follower_delta_7d"] == 42
    assert not any("粉丝明细获取失败" in warning for warning in snapshot["warnings"])


def test_bilibili_snapshot_does_not_use_obsolete_video_detail_when_compare_has_metrics(monkeypatch) -> None:
    client = BilibiliClient(cookie="DedeUserID=516185777")

    async def fake_request_first_json(self, http_client, urls, label):  # noqa: ANN001, ARG001
        if label == "overview":
            return {
                "total_fans": 170_888,
                "incr_fans": 42,
                "total_click": 123_456,
                "total_like": 789,
            }
        return {}

    async def fake_fetch_video_payloads(self, http_client, timestamp):  # noqa: ANN001, ARG001
        return [
            [
                {
                    "Archive": {
                        "bvid": "BVtest",
                        "title": "测试视频",
                        "cover": "http://i0.hdslb.com/bfs/archive/test.jpg",
                        "ptime": 1779624225,
                        "duration": 60,
                    },
                    "stat": {
                        "view": 100,
                        "like": 10,
                        "coin": 2,
                        "favorite": 3,
                        "share": 1,
                        "reply": 4,
                    },
                }
            ],
            [
                {
                    "bvid": "BVtest",
                    "stat": {
                        "tm_rate": 100,
                        "full_play_ratio": 5000,
                    },
                }
            ],
        ]

    async def fake_request_json(self, http_client, url):  # noqa: ANN001, ARG001
        if "article/detail" in url:
            raise AssertionError("obsolete video detail endpoint should not be requested")
        return {}

    async def fake_enrich_public_stats(self, channel, videos):  # noqa: ANN001, ARG001
        return None

    monkeypatch.setattr(BilibiliClient, "_request_first_json", fake_request_first_json)
    monkeypatch.setattr(BilibiliClient, "_fetch_video_payloads", fake_fetch_video_payloads)
    monkeypatch.setattr(BilibiliClient, "_request_json", fake_request_json)
    monkeypatch.setattr(BilibiliClient, "_enrich_public_stats", fake_enrich_public_stats)

    snapshot = asyncio.run(client.fetch_snapshot())

    assert snapshot["warnings"] == []
    assert snapshot["videos"][0]["ctr"] == 0.01
    assert snapshot["videos"][0]["avp_percent"] == 0.5
    assert snapshot["videos"][0]["avd_minutes"] == 0.5


def test_bilibili_video_list_normal_merge_is_not_a_warning(monkeypatch) -> None:
    client = BilibiliClient(cookie="DedeUserID=516185777")

    async def fake_request_json(self, http_client, url):  # noqa: ANN001, ARG001
        return {
            "archives": [
                {
                    "Archive": {"bvid": "BVtest", "title": "测试视频", "ptime": 1779624225},
                    "stat": {"view": 100},
                }
            ]
        }

    async def fake_request_first_json(self, http_client, urls, label):  # noqa: ANN001, ARG001
        return {"list": [{"bvid": "BVtest", "stat": {"tm_rate": 100}}]}

    monkeypatch.setattr(BilibiliClient, "_request_json", fake_request_json)
    monkeypatch.setattr(BilibiliClient, "_request_first_json", fake_request_first_json)

    payloads = asyncio.run(client._fetch_video_payloads(object(), 1779624225000))

    assert len(payloads) == 2
    assert client.warnings == []
