from __future__ import annotations
import json
from pathlib import Path

from analytics import derive_dashboard_context, load_fixture_history
from config import PROJECT_ROOT, load_settings
from main import _resolve_snapshot_date, render_dashboard


REQUIRED_TEXT = [
    "频道数据情况",
    "懒狗小黑",
    "B站 UID",
    "bilibili 知名科技UP主",
    "Lv.6",
    "粉丝",
    "励志能拥有很多数码产品的，一条懒狗。",
    "B 站",
    "抖音",
    "小红书",
    "NAS 更新节奏",
    "平台数据质量",
    "近 30 日三平台粉丝趋势",
    "CTR（展示点击率）",
    "近三十天上线节目封面总览",
    "视频播放量与粉丝增量",
    "平均播放时长与完播率",
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
    assert "installCodexTrackpadScrollFallback" in html
    assert "local-scroll-controls" in html
    assert "三平台运营参考 · 本地静态看板" not in html
    assert 'src="assets/channel-avatar.jpg"' in html
    assert '<meta http-equiv="refresh" content="1800">' in html
    assert "打开 NAS" not in html
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


def test_render_includes_interactive_comment_database_when_enabled(tmp_path) -> None:
    settings = load_settings()
    object.__setattr__(settings, "output_path", tmp_path / "index.html")
    object.__setattr__(settings, "enable_comment_insights", True)
    comment_path = tmp_path / "comments.json"
    long_message = "这是一条很长的评论，" * 20
    comment_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "platform": "bilibili",
                        "comment_id": "1",
                        "bvid": "BV1ABC123DEF",
                        "video_title": "测试视频",
                        "message": long_message,
                        "like_count": 2,
                        "reply_count": 1,
                        "created_at": "2026-05-02T01:00:00+00:00",
                        "source_rank": "latest",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    object.__setattr__(settings, "comment_private_path", comment_path)
    history = load_fixture_history(PROJECT_ROOT / "data" / "fixtures" / "sample_history.json")

    context = derive_dashboard_context(history, settings)
    output = render_dashboard(context, settings)
    html = output.read_text(encoding="utf-8")

    assert "data-comment-db-open" in html
    assert "data-comment-db-list" in html
    assert "data-comment-sort-controls" in html
    assert 'data-comment-sort="time"' in html
    assert 'data-comment-sort="likes"' in html
    assert "data-comment-card" in html
    assert "data-comment-more-toggle" in html
    assert "installCommentDatabase" in html
    assert "installCommentRadarSort" in html
    assert "mode === 'likes'" in html
    assert "cardLikes(b) - cardLikes(a)" in html
    assert "cardTime(b).localeCompare(cardTime(a))" in html
    assert "https://www.bilibili.com/video/BV1ABC123DEF/#reply1" in html
    assert 'target="_blank" rel="noreferrer noopener">直达评论' not in html
    assert "link.target = '_blank'" not in html
    assert 'data-comment-created="2026-05-02T01:00:00+00:00"' in html
    assert "2026-05-02 09:00" in html


def test_render_combines_follower_trend_and_growth_contribution(tmp_path) -> None:
    settings = load_settings()
    object.__setattr__(settings, "output_path", tmp_path / "index.html")
    history = load_fixture_history(PROJECT_ROOT / "data" / "fixtures" / "sample_history.json")

    context = derive_dashboard_context(history, settings)
    output = render_dashboard(context, settings)
    html = output.read_text(encoding="utf-8")

    assert 'class="panel trend-panel follower-growth-panel"' in html
    assert "粉丝增长参考" in html
    assert html.find('id="followerTrendChart"') < html.find('id="growthContributionChart"')
    assert html.find('class="panel trend-panel follower-growth-panel"') < html.find('id="growthContributionChart"')


def test_channel_avatar_asset_exists_for_static_pages() -> None:
    avatar_path = PROJECT_ROOT / "dashboard" / "output" / "assets" / "channel-avatar.jpg"

    assert avatar_path.exists()
    assert avatar_path.stat().st_size > 1024
    assert avatar_path.read_bytes()[:2] == b"\xff\xd8"


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
