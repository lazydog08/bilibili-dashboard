from __future__ import annotations

import asyncio

import pytest

from main import _fetch_with_retries, parse_args


def test_parse_args_allows_bilibili_only_live_path() -> None:
    args = parse_args(["--bilibili-only", "--snapshot-date", "yesterday"])
    assert args.bilibili_only is True
    assert args.snapshot_date == "yesterday"


def test_parse_args_rejects_bilibili_only_fixture_combo() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--fixture", "--bilibili-only"])


def test_fetch_with_retries_enforces_total_timeout() -> None:
    async def slow_fetch() -> dict:
        await asyncio.sleep(1)
        return {"ok": True}

    with pytest.raises(RuntimeError, match="slow timed out after"):
        asyncio.run(_fetch_with_retries("slow", slow_fetch, timeout_seconds=0.01))
