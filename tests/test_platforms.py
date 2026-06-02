from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

from fetcher.bark_api import format_bark_summary
from fetcher.douyin_api import DouyinClient
from fetcher.xiaohongshu_api import XiaohongshuClient
from platforms import (
    build_platform_snapshot,
    derive_platform_context,
    load_manual_platform_snapshots,
    merge_content_items,
    merge_platform_snapshot,
    next_update_label,
    repair_latest_content_thumbnails,
)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        timezone="Asia/Shanghai",
        update_times=["12:30", "20:00"],
        bilibili_enabled=True,
        bilibili_account_id="",
        douyin_enabled=True,
        douyin_account_id="",
        xiaohongshu_enabled=True,
        xiaohongshu_account_id="",
    )


def test_platform_growth_deltas_and_insufficient_periods() -> None:
    history = {
        "platform_snapshots": [
            build_platform_snapshot(
                platform="douyin",
                captured_at="2026-05-22T12:30:00+08:00",
                fans=100,
                metrics={},
                status="success",
            ),
            build_platform_snapshot(
                platform="douyin",
                captured_at="2026-05-23T12:30:00+08:00",
                fans=115,
                metrics={},
                status="success",
            ),
        ]
    }
    card = derive_platform_context(history, _settings())["platform_cards"][1]
    assert card["fans"]["label"] == "115"
    assert card["growth"][0]["title"] == "相比昨日的涨粉"
    assert card["growth"][0]["value"]["label"] == "+15"
    assert card["growth"][1]["value"]["label"] == "--"
    assert card["growth"][2]["value"]["label"] == "--"


def test_follower_trend_chart_uses_daily_labels_and_latest_snapshot_per_day() -> None:
    history = {
        "platform_snapshots": [
            build_platform_snapshot(
                platform="bilibili",
                captured_at="2026-05-26T01:31:00+08:00",
                fans=100,
                metrics={},
                status="success",
                source="creator_center",
            ),
            build_platform_snapshot(
                platform="bilibili",
                captured_at="2026-05-26T15:32:00+08:00",
                fans=120,
                metrics={},
                status="success",
                source="creator_center",
            ),
        ]
    }

    chart = derive_platform_context(history, _settings())["follower_trend_chart"]

    assert chart["labels"] == ["05-26"]
    assert all(":" not in label for label in chart["labels"])
    bilibili_series = next(item for item in chart["series"] if item["name"] == "B 站")
    assert bilibili_series["data"] == [120]


def test_next_update_label_can_use_interval(monkeypatch) -> None:
    import platforms

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return cls(2026, 5, 24, 15, 47, tzinfo=tz)

    monkeypatch.setattr(platforms, "datetime", FixedDateTime)
    assert next_update_label(["12:30", "20:00"], "Asia/Shanghai", 30) == "下次更新：今天 16:00（约 0小时13分钟）"


def test_metric_delta_percent_is_unavailable_when_yesterday_is_zero() -> None:
    history = {
        "platform_snapshots": [
            build_platform_snapshot(
                platform="douyin",
                captured_at="2026-05-20T12:30:00+08:00",
                fans=100,
                metrics={"views": 100},
                status="success",
            ),
            build_platform_snapshot(
                platform="douyin",
                captured_at="2026-05-21T12:30:00+08:00",
                fans=100,
                metrics={"views": 100},
                status="success",
            ),
            build_platform_snapshot(
                platform="douyin",
                captured_at="2026-05-22T12:30:00+08:00",
                fans=100,
                metrics={"views": 150},
                status="success",
            ),
        ]
    }
    card = derive_platform_context(history, _settings())["platform_cards"][1]
    row = card["common_metrics"][0]
    assert row["today"]["label"] == "50"
    assert row["yesterday"]["label"] == "0"
    assert row["delta"]["label"] == "+50"
    assert row["delta_percent"]["label"] == "--"


def test_unavailable_platform_displays_placeholder() -> None:
    card = derive_platform_context({}, _settings())["platform_cards"][2]
    assert card["name"] == "小红书"
    assert card["fans"]["label"] == "--"
    assert card["status_label"] == "暂不可用"
    assert card["common_metrics"][0]["today"]["label"] == "--"


def test_bark_summary_formats_three_platforms() -> None:
    cards = derive_platform_context({}, _settings())["platform_cards"]
    title, body = format_bark_summary(cards, "Asia/Shanghai")
    assert title.startswith("看板更新失败")
    assert "B 站：暂不可用" in body
    assert "抖音：暂不可用" in body
    assert "小红书：暂不可用" in body


def test_manual_platform_file_feeds_real_values(tmp_path) -> None:
    manual_path = tmp_path / "manual_platform_metrics.json"
    manual_path.write_text(
        json.dumps(
            {
                "capturedAt": "2026-05-23T20:00:00+08:00",
                "platforms": {
                    "douyin": {
                        "fans": 1234,
                        "growth": {"cycle": 12, "7d": 45, "30d": 88},
                        "today": {"views": 300, "likes": 20},
                        "yesterday": {"views": 200, "likes": 10},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    snapshots = load_manual_platform_snapshots(manual_path, "Asia/Shanghai")
    assert len(snapshots) == 1
    history = {"platform_snapshots": snapshots}
    card = derive_platform_context(history, _settings())["platform_cards"][1]
    assert card["fans"]["label"] == "1,234"
    assert card["growth"][0]["value"]["label"] == "+12"
    assert card["growth"][1]["value"]["label"] == "+45"
    assert card["common_metrics"][0]["today"]["label"] == "300"
    assert card["common_metrics"][0]["yesterday"]["label"] == "200"
    assert card["common_metrics"][0]["delta"]["label"] == "+100"


def test_empty_manual_platform_file_is_ignored(tmp_path) -> None:
    manual_path = tmp_path / "manual_platform_metrics.json"
    manual_path.write_text(
        json.dumps({"platforms": {"xiaohongshu": {"fans": None, "today": {"views": None}}}}),
        encoding="utf-8",
    )
    assert load_manual_platform_snapshots(manual_path, "Asia/Shanghai") == []


def test_authorized_douyin_payload_maps_to_snapshot() -> None:
    from fetcher.authorized_source import snapshot_from_payload

    client = DouyinClient(account_id="douyin-1", cookie_present=True, data_url="https://example.invalid/data")
    snapshot = snapshot_from_payload(
        platform="douyin",
        account_id=client.account_id,
        payload={
            "data": {
                "follower_count": 2000,
                "play_count": 12345,
                "digg_count": 678,
                "comment_count": 90,
                "share_count": 12,
                "homepage_visits": 321,
            }
        },
        timezone_name="Asia/Shanghai",
        key_map=client.KEY_MAP,
        custom_key_map=client.CUSTOM_KEY_MAP,
    )
    assert snapshot["platform"] == "douyin"
    assert snapshot["fans"] == {"value": 2000, "status": "available", "source": "authorized_cookie"}
    assert snapshot["metrics"]["views"]["value"] == 12345
    assert snapshot["metrics"]["likes"]["value"] == 678
    assert snapshot["customMetrics"]["profile_visits"]["value"] == 321


def test_authorized_xhs_payload_maps_to_snapshot() -> None:
    from fetcher.authorized_source import snapshot_from_payload

    client = XiaohongshuClient(account_id="xhs-1", cookie_present=True, data_url="https://example.invalid/data")
    snapshot = snapshot_from_payload(
        platform="xiaohongshu",
        account_id=client.account_id,
        payload={
            "data": {
                "fans_count": 3000,
                "read_count": 22222,
                "like_count": 888,
                "collect_count": 77,
                "exposure_count": 45678,
                "search_entry_count": 66,
            }
        },
        timezone_name="Asia/Shanghai",
        key_map=client.KEY_MAP,
        custom_key_map=client.CUSTOM_KEY_MAP,
    )
    assert snapshot["platform"] == "xiaohongshu"
    assert snapshot["fans"] == {"value": 3000, "status": "available", "source": "authorized_cookie"}
    assert snapshot["metrics"]["views"]["value"] == 22222
    assert snapshot["metrics"]["favorites"]["value"] == 77
    assert snapshot["customMetrics"]["note_impressions"]["value"] == 45678
    assert snapshot["customMetrics"]["search_entries"]["value"] == 66


def test_merge_platform_snapshot_moves_content_to_latest_cache() -> None:
    snapshot = build_platform_snapshot(
        platform="douyin",
        captured_at="2026-05-23T12:30:00+08:00",
        fans=100,
        metrics={"views": 500},
        content_items=[
            {"title": "作品 1", "thumbnail": "https://example.com/1.jpg", "views": 100},
            {"title": "作品 2", "thumbnail": "https://example.com/2.jpg", "views": 200},
        ],
        status="success",
        source="authorized_cookie",
    )
    history = merge_platform_snapshot({}, snapshot, content_limit=1)
    stored = history["platform_snapshots"][0]
    assert "contentItems" not in stored
    assert stored["fans"]["value"] == 100
    assert history["latest_content"]["douyin"]["items"][0]["title"] == "作品 1"
    assert len(history["latest_content"]["douyin"]["items"]) == 1


def test_missing_douyin_cover_gets_readable_generated_thumbnail() -> None:
    history = {
        "latest_content": {
            "douyin": {
                "capturedAt": "2026-05-23T20:00:00+08:00",
                "items": [
                    {
                        "title": "缺少封面的竖版作品",
                        "publish_time": "2026-05-23",
                        "views": 100,
                    }
                ],
            }
        }
    }

    repaired = repair_latest_content_thumbnails(history, content_limit=10)
    cached_item = repaired["latest_content"]["douyin"]["items"][0]
    assert cached_item["thumbnail"].startswith("data:image/svg+xml")
    assert cached_item["thumbnail_note"] == "自动生成封面占位"

    card = derive_platform_context(repaired, _settings())["platform_cards"][1]
    assert card["content_items"][0]["thumbnail"].startswith("data:image/svg+xml")


def test_merge_content_items_dedupes_similar_titles_and_sorts_by_publish_time() -> None:
    live_items = [
        {
            "title": "我做了个离谱的充电器... #充电器 #DIY",
            "publish_time": "2026-03-27",
            "thumbnail": "https://example.com/live.jpg",
            "views": 100,
        },
        {
            "title": "战争制裁下的俄罗斯，老百姓过着怎样的生活？ #俄罗斯 #战争 #vlog",
            "publish_time": "2026-04-22",
            "thumbnail": "https://example.com/war.jpg",
            "views": 200,
        },
    ]
    manual_items = [
        {
            "title": "战争制裁下的俄罗斯，老百姓过着怎样的生活？",
            "publish_time": "2026年04月22日 22:49",
            "likes": 50,
        },
        {
            "title": "500天，我让这台小米手机走遍了中国！ #小米",
            "publish_time": "2025年03月01日 13:36",
            "views": 80,
        },
    ]

    merged = merge_content_items(live_items, manual_items)

    assert [item["publish_time"] for item in merged] == [
        "2026年04月22日 22:49",
        "2026-03-27",
        "2025年03月01日 13:36",
    ]
    assert len(merged) == 3
    assert merged[0]["title"] == "战争制裁下的俄罗斯，老百姓过着怎样的生活？"
    assert merged[0]["thumbnail"] == "https://example.com/war.jpg"
    assert merged[0]["likes"] == 50


def test_merge_content_items_skips_unverified_blank_latest_note() -> None:
    live_items = [
        {
            "title": "清闲pro到底好不好？给大家踩踩坑",
            "publish_time": "2026-05-27 19:50",
            "thumbnail": "https://example.com/xhs.jpg",
            "views": None,
            "likes": None,
            "favorites": None,
            "comments": None,
            "shares": None,
            "data_source": "小红书最新笔记详情",
            "metric_scope": "待核验",
            "metric_warning": "最新笔记详情接口返回了发布前日期的非零播放/互动，未展示为真实视频数据。",
        }
    ]
    manual_items = [
        {
            "title": "战争制裁下的俄罗斯，人们过着怎样的生活？",
            "publish_time": "2026年04月22日 20:17",
            "views": 119869,
        }
    ]

    merged = merge_content_items(live_items, manual_items)

    assert [item["title"] for item in merged] == ["战争制裁下的俄罗斯，人们过着怎样的生活？"]


def test_client_uses_manual_import_as_fallback_without_network() -> None:
    manual = build_platform_snapshot(
        platform="douyin",
        captured_at="2026-05-23T20:00:00+08:00",
        fans=456,
        metrics={"views": 789},
        status="manual",
        source="manual_import",
    )
    client = DouyinClient(
        account_id="douyin-1",
        cookie_present=True,
        data_url="https://example.invalid/data",
        manual_snapshot=manual,
        allow_network=False,
    )
    snapshot = asyncio.run(client.fetch_snapshot("Asia/Shanghai"))
    assert snapshot["sourceStatus"]["source"] == "manual_import"
    assert snapshot["fans"]["value"] == 456


def test_bilibili_live_growth_does_not_compare_against_fixture() -> None:
    history = {
        "platform_snapshots": [
            build_platform_snapshot(
                platform="bilibili",
                captured_at="2026-05-23T12:30:00+08:00",
                fans=3_400_000,
                metrics={},
                manual_growth={"7d": None},
                status="fixture",
                source="bilibili_cache",
            ),
            build_platform_snapshot(
                platform="bilibili",
                captured_at="2026-05-24T00:30:00+08:00",
                fans=168_735,
                metrics={},
                manual_growth={"7d": 248},
                status="partial",
                source="bilibili_live",
            ),
        ]
    }
    card = derive_platform_context(history, _settings())["platform_cards"][0]
    assert card["growth"][0]["value"]["label"] == "--"
    assert card["growth"][1]["value"]["label"] == "+248"
    assert [item["label"] for item in card["custom_metrics"]] == ["投币数", "弹幕数"]


def test_douyin_official_source_has_priority_over_manual(monkeypatch) -> None:
    import fetcher.douyin_api as douyin_api

    async def fake_official_json(url, access_token, params=None):  # noqa: ANN001
        return {"data": {"follower_count": 999, "play_count": 1000}}

    monkeypatch.setattr(douyin_api, "fetch_official_json", fake_official_json)
    monkeypatch.setenv("DOUYIN_ACCESS_TOKEN", "configured-token")
    manual = build_platform_snapshot(
        platform="douyin",
        fans=111,
        metrics={"views": 222},
        status="manual",
        source="manual_import",
    )
    client = DouyinClient(
        account_id="douyin-1",
        official_data_url="https://example.invalid/official",
        manual_snapshot=manual,
    )
    snapshot = asyncio.run(client.fetch_snapshot("Asia/Shanghai"))
    assert snapshot["sourceStatus"]["source"] == "official_api"
    assert snapshot["fans"]["value"] == 999
    assert snapshot["metrics"]["views"]["value"] == 1000


def test_xhs_cookie_source_has_priority_over_manual_when_official_missing(monkeypatch) -> None:
    import fetcher.xiaohongshu_api as xhs_api

    async def fake_authorized_json(url, cookie, referer):  # noqa: ANN001
        return {"data": {"fans_count": 777, "read_count": 888}}

    monkeypatch.setattr(xhs_api, "fetch_authorized_json", fake_authorized_json)
    monkeypatch.setenv("XIAOHONGSHU_COOKIE", "configured-cookie")
    manual = build_platform_snapshot(
        platform="xiaohongshu",
        fans=111,
        metrics={"views": 222},
        status="manual",
        source="manual_import",
    )
    client = XiaohongshuClient(
        account_id="xhs-1",
        data_url="https://example.invalid/cookie",
        manual_snapshot=manual,
    )
    snapshot = asyncio.run(client.fetch_snapshot("Asia/Shanghai"))
    assert snapshot["sourceStatus"]["source"] == "authorized_cookie"
    assert snapshot["fans"]["value"] == 777
    assert snapshot["metrics"]["views"]["value"] == 888


def test_xhs_cookie_source_merges_creator_summary_and_content_url(monkeypatch) -> None:
    import fetcher.xiaohongshu_api as xhs_api

    async def fake_xhs_get_json(url, cookie, *, extra_headers=None, referer=""):  # noqa: ANN001
        if "personal_info" in url:
            return {
                "success": True,
                "data": {
                    "red_num": "xhs-1",
                    "fans_count": 300,
                    "grow_info": {"fans_count": 300},
                },
            }
        if "account/base" in url:
            return {
                "success": True,
                "data": {
                    "seven": {
                        "view_count": 1200,
                        "like_count": 80,
                        "collect_count": 20,
                        "comment_count": 6,
                        "share_count": 3,
                    },
                    "thirty": {"net_rise_fans_count": 12},
                },
            }
        return {
            "success": True,
            "data": {
                "list": [
                    {
                        "note_id": "note-new",
                        "note_title": "最新一期小红书内容",
                        "publish_time": "2026-05-25 16:30",
                        "read_count": 4567,
                        "like_count": 123,
                    }
                ]
            },
        }

    monkeypatch.setattr(xhs_api, "_authorized_xhs_get_json", fake_xhs_get_json)
    monkeypatch.setenv("XIAOHONGSHU_COOKIE", "configured-cookie")
    client = XiaohongshuClient(
        account_id="",
        data_url=xhs_api.XHS_ACCOUNT_BASE_URL,
        content_data_url="https://creator.xiaohongshu.com/api/galaxy/creator/data/note_stats/new",
    )
    snapshot = asyncio.run(client.fetch_snapshot("Asia/Shanghai"))

    assert snapshot["sourceStatus"]["source"] == "authorized_cookie"
    assert snapshot["fans"]["value"] == 300
    assert snapshot["metrics"]["views"]["value"] == 1200
    assert snapshot["contentItems"][0]["note_id"] == "note-new"
    assert snapshot["contentItems"][0]["views"] == 4567
    assert "作品列表已读取 1 条" in snapshot["sourceStatus"]["message"]


def test_xhs_summary_url_reports_stale_content_fallback(monkeypatch) -> None:
    import fetcher.xiaohongshu_api as xhs_api

    async def fake_xhs_get_json(url, cookie, *, extra_headers=None, referer=""):  # noqa: ANN001
        if "personal_info" in url:
            return {"success": True, "data": {"fans_count": 300, "grow_info": {"fans_count": 300}}}
        if "account/base" in url:
            return {"success": True, "data": {"seven": {"view_count": 1200}, "thirty": {}}}
        raise RuntimeError("signed content endpoint rejected")

    monkeypatch.setattr(xhs_api, "_authorized_xhs_get_json", fake_xhs_get_json)
    monkeypatch.setenv("XIAOHONGSHU_COOKIE", "configured-cookie")
    client = XiaohongshuClient(account_id="", data_url=xhs_api.XHS_ACCOUNT_BASE_URL)
    snapshot = asyncio.run(client.fetch_snapshot("Asia/Shanghai"))

    assert snapshot["sourceStatus"]["source"] == "authorized_cookie"
    assert snapshot["contentItems"] == []
    assert "作品列表未更新" in snapshot["sourceStatus"]["message"]
    assert "XIAOHONGSHU_CONTENT_DATA_URL" in snapshot["sourceStatus"]["message"]


def test_xhs_latest_note_rejects_detail_metrics_with_pre_publish_activity(monkeypatch) -> None:
    import fetcher.xiaohongshu_api as xhs_api

    async def fake_xhs_get_json(url, cookie, *, extra_headers=None, referer=""):  # noqa: ANN001
        if "latest_note_data" in url:
            return {
                "success": True,
                "data": {
                    "noteInfo": {
                        "id": "note-new",
                        "title": "清闲pro到底好不好？给大家踩踩坑",
                        "coverUrl": "https://example.com/cover.jpg",
                        "postTime": 1779882644000,
                    }
                },
            }
        if "note_detail_new" in url:
            return {
                "success": True,
                "data": {
                    "seven": {
                        "view_count": 8217,
                        "like_count": 274,
                        "collect_count": 92,
                        "comment_count": 15,
                        "share_count": 48,
                        "view_time_avg": 479997,
                        "view_list": [
                            {"date": 1779206400000, "count": 511},
                            {"date": 1779881600000, "count": 7706},
                        ],
                    },
                    "analyse_infos": [{"quota": "readFeed", "count": 8217.0}],
                },
            }
        raise RuntimeError("content list is unavailable")

    monkeypatch.setattr(xhs_api, "_authorized_xhs_get_json", fake_xhs_get_json)
    monkeypatch.setenv("XIAOHONGSHU_COOKIE", "configured-cookie")
    source = xhs_api.XiaohongshuCookieSource(account_id="xhs-1")
    items, message = asyncio.run(
        source._fetch_latest_note_item("configured-cookie", "Asia/Shanghai", {})
    )

    assert "未通过发布时间校验" in message
    assert items[0]["title"] == "清闲pro到底好不好？给大家踩踩坑"
    assert items[0]["publish_time"] == "2026-05-27 19:50"
    assert items[0]["views"] is None
    assert items[0]["likes"] is None
    assert items[0]["metric_scope"] == "待核验"
    assert "发布前日期" in items[0]["metric_warning"]
