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
