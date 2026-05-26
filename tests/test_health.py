from health import build_operational_status


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
