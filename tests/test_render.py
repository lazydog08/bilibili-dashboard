from __future__ import annotations
from pathlib import Path

from analytics import derive_dashboard_context, load_fixture_history
from config import DEFAULT_NAS_FINDER_URL, PROJECT_ROOT, load_settings
from main import _resolve_snapshot_date, render_dashboard


REQUIRED_TEXT = [
    "频道数据情况",
    "B 站",
    "抖音",
    "小红书",
    "近 30 日三平台粉丝趋势",
    "CTR（展示点击率）",
    "近三十天上线节目封面总览",
    "视频播放量与粉丝增量",
    "平均播放时长与完播率",
    "打开 NAS",
]


def test_render_fixture_creates_dashboard_without_network(tmp_path) -> None:
    settings = load_settings()
    object.__setattr__(settings, "output_path", tmp_path / "index.html")
    object.__setattr__(settings, "page_refresh_seconds", 1800)
    history = load_fixture_history(PROJECT_ROOT / "data" / "fixtures" / "sample_history.json")
    context = derive_dashboard_context(history, settings)
    output = render_dashboard(context, settings)
    assert output.exists()
    html = output.read_text(encoding="utf-8")
    for text in REQUIRED_TEXT:
        assert text in html
    assert "echarts.init" in html
    assert "const ctrChartData =" in html
    assert '<meta http-equiv="refresh" content="1800">' in html
    assert f'href="{DEFAULT_NAS_FINDER_URL}"' in html
    reach_start = html.find("reachChart.setOption({")
    interaction_start = html.find("interactionChart.setOption({", reach_start)
    assert reach_start != -1, "reach chart config is missing"
    assert interaction_start != -1, "interaction chart config boundary is missing"
    reach_chart_body = html[reach_start:interaction_start]
    for snippet in [
        "grid: { left: 90, right: 18, top: 46, bottom: 82 }",
        "nameGap: 18",
        "fontWeight: 800",
        "padding: [0, 0, 8, 0]",
        "axisLabel: { color: TEXT, margin: 12 }",
    ]:
        assert snippet in reach_chart_body
    assert not any(line.endswith((" ", "\t")) for line in html.splitlines())


def test_render_escapes_complex_video_titles(tmp_path) -> None:
    settings = load_settings()
    object.__setattr__(settings, "output_path", tmp_path / "index.html")
    history = {
        "schema_version": 1,
        "source": "fixture",
        "last_updated": "2026-05-23T12:07:00+08:00",
        "warnings": [],
        "snapshots": [
            {
                "date": "2026-05-23",
                "updated_at": "2026-05-23T12:07:00+08:00",
                "channel": {
                    "total_followers": 1,
                    "follower_delta_7d": 1,
                    "total_views": 1,
                    "total_likes": 1,
                },
                "videos": [
                    {
                        "bvid": "BVTEST",
                        "title": "中文“引号”、English \"quotes\"、apostrophe's、emoji 😄\n换行",
                        "thumbnail": "https://picsum.photos/seed/test/640/360",
                        "publish_time": "2026-05-23",
                        "views": 1,
                        "ctr": 0.08,
                        "avd_minutes": 2.0,
                        "avp_percent": 0.32,
                        "follower_gain": 1,
                    }
                ],
            }
        ],
    }
    context = derive_dashboard_context(history, settings)
    output = render_dashboard(context, settings)
    html = Path(output).read_text(encoding="utf-8")
    assert "中文“引号”" in html
    assert "\\u0027" in html or "apostrophe" in html
    assert "const ctrChartData =" in html
    assert "echarts.init" in html


def test_snapshot_date_argument_accepts_explicit_date() -> None:
    assert _resolve_snapshot_date("2026-05-22", "Asia/Shanghai") == "2026-05-22"
