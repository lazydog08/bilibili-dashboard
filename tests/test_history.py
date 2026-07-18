from __future__ import annotations

from analytics import (
    derive_dashboard_context,
    load_fixture_history,
    load_history,
    merge_today_snapshot,
)
from config import PROJECT_ROOT, load_settings


def _snapshot(day: int) -> dict:
    date = f"2026-05-{day:02d}"
    return {
        "date": date,
        "updated_at": f"{date}T12:07:00+08:00",
        "channel": {
            "total_followers": day,
            "follower_delta_7d": day,
            "total_views": day,
            "total_likes": day,
        },
        "videos": [],
    }


def test_merge_today_snapshot_replaces_duplicate_date() -> None:
    history = {"schema_version": 1, "source": "fixture", "warnings": [], "snapshots": [_snapshot(1)]}
    updated = _snapshot(1)
    updated["channel"]["total_followers"] = 999
    merged = merge_today_snapshot(history, updated)
    assert len(merged["snapshots"]) == 1
    assert merged["snapshots"][0]["channel"]["total_followers"] == 999


def test_merge_today_snapshot_keeps_only_90_days() -> None:
    history = {"schema_version": 1, "source": "fixture", "warnings": [], "snapshots": []}
    for index in range(1, 96):
        snapshot = {
            "date": f"2026-01-{index:02d}",
            "updated_at": f"2026-01-01T12:07:00+08:00",
            "channel": {"total_followers": index},
            "videos": [],
        }
        history = merge_today_snapshot(history, snapshot, keep_days=90)
    assert len(history["snapshots"]) == 90


def test_history_load_fallback_shape_is_valid(tmp_path) -> None:
    history = load_history(tmp_path / "missing.json")
    assert history["schema_version"] == 1
    assert isinstance(history["warnings"], list)
    assert isinstance(history["snapshots"], list)


def test_fixture_history_shape_is_valid() -> None:
    history = load_fixture_history(PROJECT_ROOT / "data" / "fixtures" / "sample_history.json")
    assert len(history["snapshots"]) >= 30
    assert len(history["snapshots"][-1]["videos"]) >= 24


def test_malformed_optional_fields_do_not_crash_context_derivation() -> None:
    history = {
        "schema_version": 1,
        "source": "fixture",
        "warnings": ["fixture warning"],
        "snapshots": [
            {
                "date": "2026-05-23",
                "updated_at": "2026-05-23T12:07:00+08:00",
                "channel": {"total_followers": "bad"},
                "videos": [
                    {
                        "title": "标题含有“中文引号”、English \"quotes\"、apostrophe's、emoji 😄 和\n换行",
                        "thumbnail": None,
                        "publish_time": "bad date",
                        "ctr": "8.5%",
                        "avd_minutes": None,
                        "avp_percent": 32,
                    }
                ],
            }
        ],
    }
    context = derive_dashboard_context(history, load_settings())
    assert context["video_count"] == 1
    assert context["warnings"] == ["fixture warning"]


def test_display_warning_override_hides_stale_cache_fetch_failures() -> None:
    history = {
        "schema_version": 1,
        "source": "cache",
        "warnings": [
            "30 个视频明细接口不可用，已使用列表数据回退。",
            "粉丝明细获取失败，已使用 0 回退：fan detail failed: Bilibili request failed: Bilibili API returned code -400: 请求错误",
        ],
        "snapshots": [
            {
                "date": "2026-05-23",
                "updated_at": "2026-05-23T12:07:00+08:00",
                "warnings": [
                    "30 个视频明细接口不可用，已使用列表数据回退。",
                ],
                "channel": {"total_followers": 168707},
                "videos": [{"title": "缓存真实视频", "publish_time": "2026-05-23", "views": 100}],
            }
        ],
    }
    context = derive_dashboard_context(
        history,
        load_settings(),
        display_warnings=["未启用实时获取，已使用缓存或示例数据。"],
    )
    assert context["warnings"] == ["未启用实时获取，已使用缓存或示例数据。"]


def test_last_updated_is_rendered_as_readable_local_time() -> None:
    history = {
        "schema_version": 1,
        "source": "cache",
        "last_updated": "2026-05-25T23:01:47+08:00",
        "warnings": [],
        "snapshots": [_snapshot(25)],
    }

    context = derive_dashboard_context(history, load_settings())
    assert context["last_updated"] == "2026-05-25 23:01"


def test_live_snapshot_takes_display_priority_over_newer_fixture_date() -> None:
    fixture_snapshot = _snapshot(23)
    fixture_snapshot["source"] = "fixture"
    fixture_snapshot["channel"]["total_followers"] = 3_400_000
    live_snapshot = _snapshot(22)
    live_snapshot["source"] = "live"
    live_snapshot["channel"]["total_followers"] = 168_707
    live_snapshot["videos"] = [{"title": "真实视频", "publish_time": "2026-05-22", "views": 100}]

    history = {
        "schema_version": 1,
        "source": "live",
        "last_updated": "2026-05-23T15:38:00+08:00",
        "warnings": [],
        "snapshots": [live_snapshot, fixture_snapshot],
    }

    context = derive_dashboard_context(history, load_settings())
    assert context["kpis"][0]["value"] == 168_707
    assert context["recent_videos"][0]["title"] == "真实视频"


def test_empty_live_snapshot_does_not_override_fixture_display() -> None:
    fixture_snapshot = _snapshot(23)
    fixture_snapshot["source"] = "fixture"
    fixture_snapshot["channel"]["category_totals"] = {"风暴传媒粉丝数": 3_400_000}
    fixture_snapshot["videos"] = [{"title": "样例视频", "publish_time": "2026-05-23", "views": 100}]
    live_snapshot = _snapshot(22)
    live_snapshot["source"] = "live"
    live_snapshot["videos"] = []

    history = {
        "schema_version": 1,
        "source": "live",
        "last_updated": "2026-05-23T15:38:00+08:00",
        "warnings": [],
        "snapshots": [live_snapshot, fixture_snapshot],
    }

    context = derive_dashboard_context(history, load_settings())
    assert context["recent_videos"][0]["title"] == "样例视频"


def test_cached_live_like_snapshot_can_override_newer_fixture_date() -> None:
    fixture_snapshot = _snapshot(23)
    fixture_snapshot["source"] = "fixture"
    fixture_snapshot["channel"]["category_totals"] = {"风暴传媒粉丝数": 3_400_000}
    fixture_snapshot["videos"] = [{"title": "样例视频", "publish_time": "2026-05-23", "views": 100}]
    cached_live_snapshot = _snapshot(22)
    cached_live_snapshot["channel"]["total_followers"] = 168_707
    cached_live_snapshot["videos"] = [{"title": "缓存真实视频", "publish_time": "2026-05-22", "views": 100}]

    history = {
        "schema_version": 1,
        "source": "cache",
        "last_updated": "2026-05-23T15:38:00+08:00",
        "warnings": [],
        "snapshots": [cached_live_snapshot, fixture_snapshot],
    }

    context = derive_dashboard_context(history, load_settings())
    assert context["kpis"][0]["value"] == 168_707
    assert context["recent_videos"][0]["title"] == "缓存真实视频"


def test_live_partial_source_uses_partial_badge() -> None:
    live_snapshot = _snapshot(23)
    live_snapshot["videos"] = [{"title": "真实视频", "publish_time": "2026-05-23", "views": 100}]
    history = {
        "schema_version": 1,
        "source": "live_partial",
        "last_updated": "2026-05-24T14:45:46+08:00",
        "warnings": ["明细接口降级"],
        "snapshots": [live_snapshot],
    }

    context = derive_dashboard_context(history, load_settings())
    assert context["badge_text"] == "全平台运营数据"
    assert context["recent_videos"][0]["title"] == "真实视频"


def test_newer_public_partial_snapshot_overrides_older_creator_live_snapshot() -> None:
    creator_snapshot = {
        **_snapshot(1),
        "date": "2026-07-01",
        "updated_at": "2026-07-01T15:00:00+08:00",
        "source": "live",
        "videos": [{"bvid": "BVold", "title": "旧节目", "publish_time": "2026-06-15", "views": 100}],
    }
    public_snapshot = {
        **_snapshot(18),
        "date": "2026-07-18",
        "updated_at": "2026-07-18T15:00:00+08:00",
        "source": "public_partial",
        "videos": [{"bvid": "BVnew", "title": "新节目", "publish_time": "2026-07-17", "views": 200}],
    }
    history = {
        "schema_version": 1,
        "source": "live_partial",
        "last_updated": public_snapshot["updated_at"],
        "warnings": [],
        "snapshots": [creator_snapshot, public_snapshot],
    }

    context = derive_dashboard_context(history, load_settings())

    assert [video["bvid"] for video in context["recent_videos"]] == ["BVnew"]


def test_recent_program_grid_does_not_backfill_older_videos() -> None:
    snapshot = _snapshot(23)
    snapshot["date"] = "2026-07-18"
    snapshot["updated_at"] = "2026-07-18T15:00:00+08:00"
    snapshot["videos"] = [
        {"bvid": "BVnew", "title": "近30天节目", "publish_time": "2026-07-18", "views": 100},
        {"bvid": "BVold", "title": "31天前节目", "publish_time": "2026-06-17", "views": 200},
    ]
    history = {
        "schema_version": 1,
        "source": "live_partial",
        "last_updated": snapshot["updated_at"],
        "warnings": [],
        "snapshots": [snapshot],
    }

    context = derive_dashboard_context(history, load_settings())

    assert [video["bvid"] for video in context["recent_videos"]] == ["BVnew"]
    assert [video["bvid"] for video in context["program_videos"]] == ["BVnew", "BVold"]
    assert [video["program_period_label"] for video in context["program_videos"]] == ["近30天", "历史"]
    assert context["views_followers_chart"]["full_titles"] == ["31天前节目", "近30天节目"]
    assert context["recent_video_count"] == 1
    assert context["historical_video_count"] == 1


def test_missing_private_video_metrics_render_as_unavailable_and_leave_charts() -> None:
    snapshot = _snapshot(23)
    snapshot["date"] = "2026-07-18"
    snapshot["updated_at"] = "2026-07-18T15:00:00+08:00"
    snapshot["public_listing"] = {
        "status": "complete_30d",
        "message": "近30天节目范围可确认完整。",
    }
    snapshot["videos"] = [
        {
            "bvid": "BVnew",
            "title": "公开节目",
            "publish_time": "2026-07-18",
            "views": 100,
            "ctr": None,
            "avd_minutes": None,
            "avp_percent": None,
            "follower_gain": None,
        }
    ]
    history = {
        "schema_version": 1,
        "source": "live_partial",
        "last_updated": snapshot["updated_at"],
        "warnings": [],
        "snapshots": [snapshot],
    }

    context = derive_dashboard_context(history, load_settings())
    video = context["recent_videos"][0]

    assert video["ctr_label"] == "--"
    assert video["avd_label"] == "--"
    assert video["avp_label"] == "--"
    assert context["ctr_chart"]["labels"] == []
    assert context["avd_avp_chart"]["labels"] == []
    assert context["views_followers_chart"]["follower_gain"] == [None]
    assert context["program_listing_note"].endswith("近30天节目范围可确认完整。")
    assert context["program_listing_note"].startswith("近30天 1 条 · 历史 0 条")
