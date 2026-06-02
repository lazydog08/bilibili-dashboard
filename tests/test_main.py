from __future__ import annotations

import asyncio
import json

import pytest

from config import PROJECT_ROOT, load_settings
from main import _fetch_with_retries, _with_manual_content_fallback, build_dashboard, parse_args


def test_parse_args_allows_bilibili_only_live_path() -> None:
    args = parse_args(["--bilibili-only", "--snapshot-date", "yesterday"])
    assert args.bilibili_only is True
    assert args.snapshot_date == "yesterday"


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
