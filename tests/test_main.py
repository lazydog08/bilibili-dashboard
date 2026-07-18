from __future__ import annotations

import asyncio
import json

import pytest

from config import PROJECT_ROOT, load_settings
from main import (
    _collect_platform_snapshots,
    _fetch_with_retries,
    _latest_creator_center_success_at,
    _latest_network_platform_capture,
    _merge_public_bilibili_snapshot,
    _with_manual_content_fallback,
    build_dashboard,
    parse_args,
)


def test_parse_args_allows_bilibili_only_live_path() -> None:
    args = parse_args(["--bilibili-only", "--snapshot-date", "yesterday"])
    assert args.bilibili_only is True
    assert args.snapshot_date == "yesterday"


def test_latest_network_capture_ignores_newer_manual_snapshot() -> None:
    history = {
        "platform_snapshots": [
            {
                "platform": "douyin",
                "capturedAt": "2026-07-18T14:00:00+08:00",
                "sourceStatus": {"status": "success", "source": "authorized_cookie"},
            },
            {
                "platform": "xiaohongshu",
                "capturedAt": "2026-07-18T14:30:00+08:00",
                "sourceStatus": {"status": "manual", "source": "manual_import"},
            },
        ]
    }

    assert _latest_network_platform_capture(history) == "2026-07-18T14:00:00+08:00"


def test_parse_args_rejects_bilibili_only_fixture_combo() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--fixture", "--bilibili-only"])


def test_parse_args_allows_cache_render_mode() -> None:
    args = parse_args(["--cache"])
    assert args.cache is True


def test_parse_args_rejects_bilibili_only_cache_combo() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--cache", "--bilibili-only"])


def test_fetch_with_retries_enforces_total_timeout() -> None:
    async def slow_fetch() -> dict:
        await asyncio.sleep(1)
        return {"ok": True}

    with pytest.raises(RuntimeError, match="slow timed out after"):
        asyncio.run(_fetch_with_retries("slow", slow_fetch, timeout_seconds=0.01))


def test_cache_render_preserves_existing_data_timestamp(tmp_path) -> None:
    old_timestamp = "2026-05-29T01:01:00+08:00"
    history_path = tmp_path / "history.json"
    output_path = tmp_path / "index.html"
    history_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "cache",
                "last_updated": old_timestamp,
                "warnings": [],
                "snapshots": [
                    {
                        "date": "2026-05-29",
                        "updated_at": old_timestamp,
                        "channel": {"total_followers": 1},
                        "videos": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings()
    object.__setattr__(settings, "history_path", history_path)
    object.__setattr__(settings, "fixture_path", PROJECT_ROOT / "data" / "fixtures" / "sample_history.json")
    object.__setattr__(settings, "output_path", output_path)

    result = asyncio.run(build_dashboard(parse_args(["--cache", "--no-feishu", "--no-bark"]), settings))

    saved = json.loads(history_path.read_text(encoding="utf-8"))
    assert saved["last_updated"] == old_timestamp
    assert result["context"]["last_updated_iso"] == old_timestamp


def test_public_bilibili_merge_adds_new_upload_without_overwriting_private_metrics() -> None:
    cached = {
        "date": "2026-07-01",
        "updated_at": "2026-07-01T15:00:00+08:00",
        "channel": {"total_followers": 188_000, "total_views": 9_000_000},
        "videos": [
            {
                "bvid": "BVexisting",
                "title": "旧标题",
                "publish_time": "2026-06-20",
                "views": 100,
                "ctr": 0.051,
                "avd_minutes": 2.3,
                "avp_percent": 0.4,
            }
        ],
    }
    public = {
        "date": "2026-07-18",
        "updated_at": "2026-07-18T15:00:00+08:00",
        "channel": {"total_followers": 188_999},
        "videos": [
            {
                "bvid": "BVnew",
                "title": "今天的新节目",
                "publish_time": "2026-07-18",
                "views": 1_000,
                "ctr": None,
            },
            {
                "bvid": "BVexisting",
                "title": "公开标题",
                "publish_time": "2026-06-20",
                "views": 200,
                "ctr": None,
            },
        ],
        "public_listing": {"status": "complete_30d", "verified_count": 2, "message": "完整"},
        "warnings": [],
    }

    merged = _merge_public_bilibili_snapshot(
        cached,
        public,
        creator_live=False,
        creator_warnings=["创作中心失败"],
    )

    assert merged["source"] == "public_partial"
    assert merged["channel"]["total_followers"] == 188_999
    assert merged["channel"]["total_views"] == 9_000_000
    assert [video["bvid"] for video in merged["videos"][:2]] == ["BVnew", "BVexisting"]
    existing = next(video for video in merged["videos"] if video["bvid"] == "BVexisting")
    assert existing["title"] == "公开标题"
    assert existing["views"] == 200
    assert existing["ctr"] == 0.051
    assert existing["avd_minutes"] == 2.3
    assert merged["creator_center_cached_at"] == "2026-07-01T15:00:00+08:00"


def test_repeated_public_fallback_does_not_advance_creator_success_time() -> None:
    cached = {
        "date": "2026-07-18",
        "updated_at": "2026-07-18T16:30:00+08:00",
        "source": "public_partial",
        "creator_center_cached_at": "2026-07-01T15:00:00+08:00",
        "channel": {"total_followers": 188_000},
        "videos": [],
    }
    public = {
        "date": "2026-07-18",
        "updated_at": "2026-07-18T17:00:00+08:00",
        "channel": {"total_followers": 188_999},
        "videos": [],
        "warnings": [],
    }

    merged = _merge_public_bilibili_snapshot(
        cached,
        public,
        creator_live=False,
        creator_warnings=["创作中心失败"],
    )

    assert merged["updated_at"] == "2026-07-18T17:00:00+08:00"
    assert merged["creator_center_cached_at"] == "2026-07-01T15:00:00+08:00"


def test_last_creator_success_ignores_newer_public_partial_marker() -> None:
    history = {
        "snapshots": [
            {"source": "live", "updated_at": "2026-07-01T15:00:14+08:00"},
            {
                "source": "public_partial",
                "updated_at": "2026-07-18T16:44:35+08:00",
                "creator_center_cached_at": "2026-07-18T16:32:04+08:00",
            },
        ]
    }

    assert _latest_creator_center_success_at(history) == "2026-07-01T15:00:14+08:00"


def test_public_partial_snapshot_keeps_platform_card_in_public_fallback_mode(tmp_path) -> None:
    settings = load_settings()
    object.__setattr__(settings, "log_path", tmp_path / "update.log")
    latest = {
        "date": "2026-07-18",
        "updated_at": "2026-07-18T15:00:00+08:00",
        "source": "public_partial",
        "creator_center_cached_at": "2026-07-01T15:00:00+08:00",
        "channel": {"total_followers": 188_999, "total_views": 9_000_000},
        "videos": [{"bvid": "BVnew", "publish_time": "2026-07-17"}],
        "public_listing": {"status": "partial", "verified_count": 3, "message": "非全量"},
    }

    history = asyncio.run(
        _collect_platform_snapshots(
            {"snapshots": [latest]},
            settings,
            latest,
            ["创作中心失败"],
            allow_platform_network=False,
            platforms_to_update={"bilibili"},
        )
    )

    stored = history["platform_snapshots"][-1]
    assert stored["sourceStatus"]["source"] == "bilibili_public_fallback"
    assert stored["sourceStatus"]["status"] == "partial"
    assert stored["metrics"]["views"]["value"] is None
    assert stored["raw"]["summary"]["public_listing_status"] == "partial"


def test_creator_live_snapshot_with_listing_warning_does_not_claim_creator_auth_failed(tmp_path) -> None:
    settings = load_settings()
    object.__setattr__(settings, "log_path", tmp_path / "update.log")
    latest = {
        "date": "2026-07-18",
        "updated_at": "2026-07-18T15:00:00+08:00",
        "source": "live",
        "channel": {"total_followers": 188_999, "total_views": 9_000_000},
        "videos": [{"bvid": "BVnew", "publish_time": "2026-07-17"}],
    }

    history = asyncio.run(
        _collect_platform_snapshots(
            {"snapshots": [latest]},
            settings,
            latest,
            ["公开投稿列表不是全量接口"],
            allow_platform_network=False,
            platforms_to_update={"bilibili"},
        )
    )

    stored = history["platform_snapshots"][-1]
    assert stored["sourceStatus"]["source"] == "bilibili_live"
    assert stored["sourceStatus"]["status"] == "partial"
    assert stored["metrics"]["views"]["value"] == 9_000_000


def test_manual_content_fallback_marks_cached_item_source() -> None:
    snapshot = {
        "platform": "xiaohongshu",
        "sourceStatus": {
            "status": "success",
            "source": "authorized_cookie",
            "message": "来自小红书本人账号授权后台数据中心；最新笔记已读取 1 条。",
        },
        "contentItems": [
            {
                "title": "清闲pro到底好不好？给大家踩踩坑",
                "publish_time": "2026-05-27 19:50",
                "views": 11822,
                "data_source": "小红书最新笔记详情",
                "metric_scope": "近7日后台汇总",
            }
        ],
    }
    manual_snapshot = {
        "sourceStatus": {"importedAt": "2026-05-23T20:11:00+08:00"},
        "contentItems": [
            {
                "title": "战争制裁下的俄罗斯，人们过着怎样的生活？",
                "publish_time": "2026年04月22日 20:17",
                "views": 119869,
                "data_source": None,
                "metric_scope": None,
            }
        ],
    }

    patched = _with_manual_content_fallback(snapshot, manual_snapshot)

    cached_item = patched["contentItems"][1]
    assert cached_item["title"] == "战争制裁下的俄罗斯，人们过着怎样的生活？"
    assert cached_item["data_source"] == "手动导入缓存"
    assert cached_item["metric_scope"] == "导入于 2026-05-23"
    assert patched["contentItems"][0]["data_source"] == "小红书最新笔记详情"


def test_manual_content_fallback_marks_snapshot_partial_and_counts_sources() -> None:
    snapshot = {
        "platform": "xiaohongshu",
        "sourceStatus": {
            "status": "success",
            "source": "authorized_cookie",
            "message": "来自小红书本人账号授权后台数据中心；最新笔记已读取 1 条。",
        },
        "contentItems": [
            {
                "title": "清闲pro到底好不好？给大家踩踩坑",
                "publish_time": "2026-05-27 19:50",
                "views": 11822,
                "data_source": "小红书最新笔记详情",
            }
        ],
    }
    manual_snapshot = {
        "sourceStatus": {"importedAt": "2026-05-23T20:11:00+08:00"},
        "contentItems": [
            {
                "title": "战争制裁下的俄罗斯，人们过着怎样的生活？",
                "publish_time": "2026年04月22日 20:17",
                "views": 119869,
            }
        ],
    }

    patched = _with_manual_content_fallback(snapshot, manual_snapshot)

    assert patched["sourceStatus"]["status"] == "partial"
    assert "实时作品 1 条" in patched["sourceStatus"]["message"]
    assert "手动缓存 1 条" in patched["sourceStatus"]["message"]
    assert "导入于 2026-05-23" in patched["sourceStatus"]["message"]
    assert patched["raw"]["summary"]["live_content_count"] == 1
    assert patched["raw"]["summary"]["manual_cached_content_count"] == 1


def test_manual_content_fallback_uses_the_actual_platform_name() -> None:
    snapshot = {
        "platform": "douyin",
        "sourceStatus": {"status": "success", "source": "authorized_cookie", "message": "抖音授权后台"},
        "contentItems": [{"title": "实时作品", "publish_time": "2026-07-18 12:00"}],
    }
    manual_snapshot = {
        "sourceStatus": {"importedAt": "2026-05-23T20:11:00+08:00"},
        "contentItems": [{"title": "缓存作品", "publish_time": "2026-05-20 12:00"}],
    }

    patched = _with_manual_content_fallback(snapshot, manual_snapshot)

    assert "可能与抖音当前前台/后台不一致" in patched["sourceStatus"]["message"]
    assert "可能与小红书当前前台/后台不一致" not in patched["sourceStatus"]["message"]
