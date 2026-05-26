from __future__ import annotations

from visual_context import (
    build_content_engagement_chart,
    build_growth_contribution_chart,
    empty_visual_context,
    indexed_series,
    rolling_average,
)


def test_rolling_average_requires_full_valid_window() -> None:
    assert rolling_average([1, 2, 3], window=2) == [None, 1.5, 2.5]
    assert rolling_average([1, None, 3], window=2) == [None, None, None]


def test_indexed_series_uses_common_first_valid_point_and_keeps_gaps() -> None:
    chart = indexed_series(
        ["D1", "D2", "D3", "D4"],
        [
            {"name": "A", "data": [0, 100, None, 120]},
            {"name": "B", "data": [None, 50, 60, 90]},
        ],
    )

    assert chart["labels"] == ["D2", "D3", "D4"]
    assert chart["series"][0]["data"] == [100.0, None, 120.0]
    assert chart["series"][1]["data"] == [100.0, 120.0, 180.0]


def test_growth_contribution_empty_when_all_values_are_zero() -> None:
    chart = build_growth_contribution_chart(
        [
            {"name": "B站", "growth": [{"value": {"raw": 0}}, {"value": {"raw": 0}}]},
            {"name": "抖音", "growth": [{"value": {"raw": 0}}, {"value": {"raw": 0}}]},
        ]
    )

    assert chart["labels"] == []
    assert chart["empty"] == "暂无可比增长数据"


def test_content_engagement_chart_filters_zero_views_and_sorts_by_rate() -> None:
    small = build_content_engagement_chart(
        [
            {"title": "A", "views": 100, "likes": 10},
            {"title": "B", "views": 0, "likes": 10},
        ]
    )
    assert small["values"] == []
    assert small["empty"] == "样本不足"

    chart = build_content_engagement_chart(
        [
            {"title": f"V{index}", "views": index * 100, "likes": index * 10, "comments": index}
            for index in range(1, 6)
        ]
    )
    assert len(chart["items"]) == 5
    assert chart["values"][0] >= chart["values"][-1]
    assert chart["median_engagement"] is not None
    assert chart["empty"] == ""


def test_empty_visual_context_matches_template_contract() -> None:
    context = empty_visual_context("参考图数据生成失败")

    assert context["summary_cards"] == []
    assert context["follower_reference_chart"]["series"] == []
    assert context["indexed_follower_chart"]["empty"] == "参考图数据生成失败"
    assert context["content_engagement_chart"]["items"] == []
