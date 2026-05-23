from __future__ import annotations

from analytics import (
    format_number,
    normalize_thumbnail_url,
    parse_publish_time,
    safe_percent_change,
)


def test_normalize_thumbnail_url_variants() -> None:
    assert normalize_thumbnail_url("https://example.com/a.png") == "https://example.com/a.png"
    assert normalize_thumbnail_url("//i0.hdslb.com/bfs/archive/a.jpg") == "https://i0.hdslb.com/bfs/archive/a.jpg"
    assert normalize_thumbnail_url("/bfs/archive/a.jpg") == "https://i0.hdslb.com/bfs/archive/a.jpg"
    assert normalize_thumbnail_url("bfs/archive/a.jpg") == "https://i0.hdslb.com/bfs/archive/a.jpg"
    assert normalize_thumbnail_url("") == ""
    assert normalize_thumbnail_url(None) == ""
    assert normalize_thumbnail_url("bfs/archive/already.webp").endswith("already.webp")


def test_safe_percent_change_handles_bad_values() -> None:
    assert safe_percent_change(110, 100) == 0.1
    assert safe_percent_change(100, 0) == 0.0
    assert safe_percent_change(None, 10) == -1.0
    assert safe_percent_change("bad", "also-bad") == 0.0


def test_format_number() -> None:
    assert format_number(3400000) == "3,400,000"
    assert format_number("30300000") == "30,300,000"
    assert format_number(12.5) == "12.5"


def test_parse_publish_time() -> None:
    assert parse_publish_time("2026-05-23T12:07:00+08:00") == "2026-05-23"
    assert parse_publish_time(1779518820) == "2026-05-23"
    assert parse_publish_time(1779518820000) == "2026-05-23"
    assert parse_publish_time("2026/05/23 12:07") == "2026-05-23"
