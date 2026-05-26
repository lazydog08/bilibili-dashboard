from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analytics import load_history  # noqa: E402
from comments import load_comment_cache, score_comment  # noqa: E402
from config import load_env_files, load_settings  # noqa: E402
from fetcher.bilibili_comments import BilibiliCommentError, fetch_comment_page  # noqa: E402


def _latest_videos(history: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots = [item for item in history.get("snapshots", []) if isinstance(item, dict)]
    if not snapshots:
        return []
    snapshots.sort(key=lambda item: str(item.get("date", "")))
    videos = snapshots[-1].get("videos", [])
    return [video for video in videos if isinstance(video, dict) and video.get("bvid")] if isinstance(videos, list) else []


def _score_item(item: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    scored = score_comment(item)
    return {
        **item,
        "score": scored["score"],
        "labels": scored["labels"],
        "fetched_at": fetched_at,
    }


def _identity(item: dict[str, Any]) -> str:
    return f"{item.get('platform') or 'bilibili'}:{item.get('comment_id') or ''}"


def _parse_created_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _merge_items(existing: list[dict[str, Any]], fetched: list[dict[str, Any]], retention_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    merged: dict[str, dict[str, Any]] = {}
    for item in [*existing, *fetched]:
        if not isinstance(item, dict):
            continue
        key = _identity(item)
        if key.endswith(":"):
            continue
        created = _parse_created_at(item.get("created_at"))
        if created and created.astimezone(timezone.utc) < cutoff:
            continue
        previous = merged.get(key)
        if previous is None or int(item.get("score") or 0) >= int(previous.get("score") or 0):
            merged[key] = item
    return sorted(
        merged.values(),
        key=lambda item: (int(item.get("score") or 0), str(item.get("created_at") or "")),
        reverse=True,
    )[:500]


async def fetch_comments() -> dict[str, Any]:
    load_env_files()
    settings = load_settings()
    history = load_history(settings.history_path)
    videos = _latest_videos(history)
    cookie = os.getenv("BILIBILI_COOKIE", "")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logs: list[dict[str, Any]] = []
    fetched: list[dict[str, Any]] = []

    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for index, video in enumerate(videos[: settings.comment_video_limit]):
            bvid = str(video.get("bvid") or "")
            try:
                latest = await fetch_comment_page(
                    client,
                    video,
                    sort=0,
                    page_size=settings.comment_recent_page_size,
                    source_rank="latest",
                    cookie=cookie,
                )
                fetched.extend(_score_item(item, fetched_at) for item in latest)
                logs.append({"bvid": bvid, "source_rank": "latest", "status": "ok", "count": len(latest)})
            except (httpx.HTTPError, BilibiliCommentError) as exc:
                logs.append({"bvid": bvid, "source_rank": "latest", "status": "failed", "message": str(exc)})

            if index < settings.comment_ranked_video_limit:
                await asyncio.sleep(0.2)
                try:
                    ranked = await fetch_comment_page(
                        client,
                        video,
                        sort=2,
                        page_size=settings.comment_ranked_page_size,
                        source_rank="ranked",
                        cookie=cookie,
                    )
                    fetched.extend(_score_item(item, fetched_at) for item in ranked)
                    logs.append({"bvid": bvid, "source_rank": "ranked", "status": "ok", "count": len(ranked)})
                except (httpx.HTTPError, BilibiliCommentError) as exc:
                    logs.append({"bvid": bvid, "source_rank": "ranked", "status": "failed", "message": str(exc)})

            await asyncio.sleep(0.35)

    cache = load_comment_cache(settings.comment_private_path)
    merged = _merge_items(cache.get("items", []), fetched, settings.comment_retention_days)
    payload = {
        "schema_version": 1,
        "updated_at": fetched_at,
        "items": merged,
        "fetch_logs": logs[-100:],
        "pushed_comment_ids": cache.get("pushed_comment_ids", []),
    }
    settings.comment_private_path.parent.mkdir(parents=True, exist_ok=True)
    settings.comment_private_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "videos_checked": min(len(videos), settings.comment_video_limit),
        "comments_fetched": len(fetched),
        "comments_cached": len(merged),
        "cache_path": str(settings.comment_private_path),
        "logs": logs,
    }


def main() -> int:
    result = asyncio.run(fetch_comments())
    print(f"videos checked: {result['videos_checked']}")
    print(f"comments fetched: {result['comments_fetched']}")
    print(f"comments cached: {result['comments_cached']}")
    print(f"cache: {result['cache_path']}")
    failures = [item for item in result["logs"] if item.get("status") == "failed"]
    if failures:
        print("failures:")
        for item in failures[:5]:
            print(f"- {item.get('bvid')} {item.get('source_rank')}: {item.get('message')}")
    return 0 if result["comments_fetched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
