from datetime import datetime
from zoneinfo import ZoneInfo

from health import build_dashboard_freshness, build_operational_status


def test_operational_status_reports_partial_platform_quality() -> None:
    status = build_operational_status(
        [
            {"name": "B 站", "status_label": "部分可用"},
            {"name": "抖音", "status_label": "成功"},
            {"name": "小红书", "status_label": "成功"},
        ],
        next_update_label="下次更新：今天 13:00",
        update_interval_minutes=30,
    )

    assert status["cards"][0]["value"] == "30 分钟"
    assert status["cards"][1]["value"] == "2 成功 / 1 部分可用"
    assert status["cards"][1]["class"] == "is-warning"
    assert "B 站：部分可用" in status["cards"][1]["meta"]
    assert len(status["cards"]) == 2


def test_operational_status_reports_all_platforms_successful() -> None:
    status = build_operational_status(
        [
            {"name": "B 站", "status_label": "成功"},
            {"name": "抖音", "status_label": "成功"},
            {"name": "小红书", "status_label": "成功"},
        ],
        next_update_label="下次更新：今天 13:00",
        update_interval_minutes=None,
    )

    assert status["cards"][0]["value"] == "按固定时刻"
    assert status["cards"][1]["value"] == "3 个平台正常"
    assert status["cards"][1]["class"] == "is-positive"
    assert len(status["cards"]) == 2


def test_dashboard_freshness_marks_overdue_interval_as_stale() -> None:
    freshness = build_dashboard_freshness(
        "2026-05-29T01:01:00+08:00",
        update_interval_minutes=60,
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 5, 31, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert freshness["status"] == "stale"
    assert freshness["class"] == "is-unavailable"
    assert freshness["stale_after_minutes"] == 180
    assert freshness["age_label"] == "2天10小时"
    assert "最近成功：2026-05-29 01:01" in freshness["meta"]


def test_operational_status_uses_freshness_for_nas_card() -> None:
    status = build_operational_status(
        [
            {"name": "B 站", "status_label": "成功"},
            {"name": "抖音", "status_label": "成功"},
            {"name": "小红书", "status_label": "成功"},
        ],
        next_update_label="下次更新：今天 13:00",
        update_interval_minutes=60,
        freshness={
            "status": "stale",
            "value": "已停更 2天10小时",
            "meta": "最近成功：2026-05-29 01:01；计划每 60 分钟更新",
            "class": "is-unavailable",
        },
    )

    assert status["cards"][0]["value"] == "已停更 2天10小时"
    assert status["cards"][0]["class"] == "is-unavailable"
    assert "最近成功" in status["cards"][0]["meta"]
