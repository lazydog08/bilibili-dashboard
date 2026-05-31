from __future__ import annotations

import asyncio
import json

import pytest

from config import PROJECT_ROOT, load_settings
from main import _fetch_with_retries, build_dashboard, parse_args


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
