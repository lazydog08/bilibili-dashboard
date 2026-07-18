from __future__ import annotations

import argparse
import asyncio
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, select_autoescape

from analytics import (
    derive_dashboard_context,
    load_fixture_history,
    load_history,
    merge_today_snapshot,
    save_history,
)
from config import Settings, load_env_files, load_settings
from fetcher.bilibili_api import ANTI_RISK_MESSAGE, BilibiliAuthOrRiskError, BilibiliClient
from fetcher.bark_api import send_bark_notification
from fetcher.douyin_api import DouyinClient
from fetcher.feishu_api import is_configured as feishu_is_configured
from fetcher.feishu_api import upsert_daily_summary
from fetcher.xiaohongshu_api import XiaohongshuClient
from platforms import (
    append_fetch_log,
    failed_platform_snapshot,
    load_manual_platform_snapshots,
    merge_content_items,
    merge_platform_snapshot,
    platform_snapshot_from_bilibili,
    platform_snapshot_from_bilibili_public_fallback,
    repair_latest_content_thumbnails,
    unavailable_platform_snapshot,
    write_update_log,
)


def render_dashboard(context: dict[str, Any], settings: Settings) -> Path:
    template_text = settings.template_path.read_text(encoding="utf-8")
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    template = env.from_string(template_text)
    html = template.render(**context)
    html = "\n".join(line.rstrip() for line in html.splitlines()) + "\n"
    settings.output_path.parent.mkdir(parents=True, exist_ok=True)
    settings.output_path.write_text(html, encoding="utf-8")
    return settings.output_path


def _latest_snapshot(history: dict[str, Any]) -> dict[str, Any] | None:
    snapshots = [item for item in history.get("snapshots", []) if isinstance(item, dict)]
    if not snapshots:
        return None
    snapshots.sort(key=lambda item: str(item.get("date", "")))
    return snapshots[-1]


def _latest_network_platform_capture(history: dict[str, Any]) -> str:
    ignored_sources = {"", "unknown", "manual_import", "bilibili_cache", "fixture", "unavailable"}
    candidates: list[tuple[datetime, str]] = []
    for snapshot in history.get("platform_snapshots", []):
        if not isinstance(snapshot, dict):
            continue
        source_status = snapshot.get("sourceStatus")
        if not isinstance(source_status, dict):
            continue
        if str(source_status.get("status") or "") not in {"success", "partial"}:
            continue
        if str(source_status.get("source") or "") in ignored_sources:
            continue
        captured_at = str(snapshot.get("capturedAt") or "")
        try:
            parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(str(snapshot.get("timezone") or "Asia/Shanghai")))
        candidates.append((parsed, captured_at))
    return max(candidates, key=lambda item: item[0])[1] if candidates else ""


def _resolve_snapshot_date(value: str | None, timezone_name: str) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    today = datetime.now(ZoneInfo(timezone_name)).date()
    if text == "today":
        return today.isoformat()
    if text == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("--snapshot-date must be today, yesterday, or YYYY-MM-DD") from exc


def _apply_snapshot_date(snapshot: dict[str, Any], snapshot_date: str | None) -> dict[str, Any]:
    if snapshot_date:
        snapshot["date"] = snapshot_date
    return snapshot


def _snapshot_has_videos(snapshot: dict[str, Any]) -> bool:
    videos = snapshot.get("videos", [])
    return isinstance(videos, list) and bool(videos)


PUBLIC_VIDEO_FIELDS = {
    "bvid",
    "title",
    "thumbnail",
    "publish_time",
    "views",
    "likes",
    "coins",
    "favorites",
    "shares",
    "replies",
    "data_source",
}


def _merge_public_bilibili_snapshot(
    base_snapshot: dict[str, Any] | None,
    public_snapshot: dict[str, Any],
    *,
    creator_live: bool,
    creator_warnings: list[str],
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    """Merge verified public fields without overwriting creator-only metrics."""
    base = deepcopy(base_snapshot) if isinstance(base_snapshot, dict) else {}
    public = deepcopy(public_snapshot)
    creator_cached_at = str(
        base.get("updated_at")
        if creator_live
        else (
            base.get("creator_center_cached_at")
            or base.get("updated_at")
            or base.get("date")
            or ""
        )
    )
    base_channel = base.get("channel") if isinstance(base.get("channel"), dict) else {}
    public_channel = public.get("channel") if isinstance(public.get("channel"), dict) else {}
    follower = public_channel.get("total_followers")
    if isinstance(follower, (int, float)) and follower > 0:
        base_channel["total_followers"] = int(follower)
    base["channel"] = base_channel

    cached_videos = [item for item in base.get("videos", []) if isinstance(item, dict)]
    merged_by_bvid = {
        str(item.get("bvid") or ""): deepcopy(item)
        for item in cached_videos
        if str(item.get("bvid") or "")
    }
    anonymous = [deepcopy(item) for item in cached_videos if not str(item.get("bvid") or "")]
    for public_video in public.get("videos", []):
        if not isinstance(public_video, dict):
            continue
        bvid = str(public_video.get("bvid") or "").strip()
        if not bvid:
            continue
        merged = merged_by_bvid.get(bvid, {})
        for field in PUBLIC_VIDEO_FIELDS:
            if field in public_video and public_video.get(field) is not None:
                merged[field] = public_video[field]
        for private_field in ("ctr", "avd_minutes", "avp_percent", "follower_gain", "impressions"):
            if private_field not in merged:
                merged[private_field] = None
        merged_by_bvid[bvid] = merged
    videos = [*merged_by_bvid.values(), *anonymous]
    videos.sort(key=lambda item: str(item.get("publish_time") or ""), reverse=True)

    public_warnings = public.get("warnings") if isinstance(public.get("warnings"), list) else []
    warnings = [str(item) for item in [*creator_warnings, *public_warnings] if str(item).strip()]
    if not creator_live:
        warnings.append(
            "B站创作中心授权不可用；节目标题、封面、发布时间和公开互动数来自公开接口，"
            "CTR、平均播放时长、平均播放占比等私有指标使用历史值或显示“--”。"
        )
    base.update(
        {
            "date": snapshot_date or public.get("date"),
            "updated_at": public.get("updated_at"),
            "source": "live" if creator_live else "public_partial",
            "videos": videos[:30],
            "warnings": list(dict.fromkeys(warnings)),
            "public_listing": deepcopy(public.get("public_listing", {})),
            "creator_center_cached_at": creator_cached_at,
        }
    )
    return base


def _latest_creator_center_success_at(history: dict[str, Any]) -> str:
    snapshots = history.get("snapshots") if isinstance(history, dict) else None
    if not isinstance(snapshots, list):
        return ""
    for snapshot in reversed(snapshots):
        if not isinstance(snapshot, dict) or snapshot.get("source") != "live":
            continue
        captured_at = str(snapshot.get("updated_at") or snapshot.get("date") or "")
        if captured_at:
            return captured_at
    return ""


async def _try_public_bilibili_snapshot(settings: Settings) -> tuple[dict[str, Any] | None, list[str]]:
    if not settings.bilibili_enabled:
        return None, []
    try:
        client = BilibiliClient()
        snapshot = await asyncio.wait_for(
            client.fetch_public_snapshot(
                settings.bilibili_account_id,
                timezone_name=settings.timezone,
            ),
            timeout=min(settings.bilibili_fetch_timeout_seconds, 45.0),
        )
        return snapshot, []
    except asyncio.TimeoutError:
        return None, ["B站公开投稿交叉核验超时；本轮无法确认节目列表是否完整。"]
    except Exception as exc:  # noqa: BLE001 - creator data may still be usable.
        return None, [f"B站公开投稿交叉核验失败；本轮无法确认节目列表是否完整：{exc}"]


async def _try_live_snapshot(
    settings: Settings,
    require_enable_flag: bool = True,
) -> tuple[dict[str, Any] | None, list[str]]:
    if require_enable_flag and not settings.enable_bilibili_fetch:
        return None, ["未设置 ENABLE_BILIBILI_FETCH=1，已跳过实时获取。"]
    if not settings.bilibili_cookie_present:
        return None, ["未配置 BILIBILI_COOKIE，已跳过实时获取。"]
    try:
        client = BilibiliClient()
        snapshot = await asyncio.wait_for(
            client.fetch_snapshot(),
            timeout=settings.bilibili_fetch_timeout_seconds,
        )
        return snapshot, []
    except asyncio.TimeoutError:
        return None, [
            f"实时获取超时（{settings.bilibili_fetch_timeout_seconds:g}秒），已使用缓存或示例数据。"
        ]
    except BilibiliAuthOrRiskError:
        print(ANTI_RISK_MESSAGE)
        return None, [ANTI_RISK_MESSAGE]
    except Exception as exc:  # noqa: BLE001 - live data must not prevent fixture/cache rendering.
        return None, [f"实时获取失败，已使用缓存或示例数据：{exc}"]


async def _fetch_with_retries(
    name: str,
    fetcher: Any,
    max_retries: int = 3,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await fetcher()
            except Exception as exc:  # noqa: BLE001 - one platform must not break the dashboard.
                last_error = exc
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (2**attempt))
        raise RuntimeError(f"{name} failed after {max_retries} attempts: {last_error}")

    try:
        if timeout_seconds and timeout_seconds > 0:
            return await asyncio.wait_for(run(), timeout=timeout_seconds)
        return await run()
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"{name} timed out after {timeout_seconds:g} seconds") from exc


def _record_platform_result(
    history: dict[str, Any],
    snapshot: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    history = merge_platform_snapshot(
        history,
        snapshot,
        content_limit=getattr(settings, "platform_content_limit", 50),
    )
    source_status = snapshot.get("sourceStatus", {}) if isinstance(snapshot.get("sourceStatus"), dict) else {}
    status = str(source_status.get("status") or "unknown")
    message = str(source_status.get("message") or "")
    platform = str(snapshot.get("platform") or "unknown")
    history = append_fetch_log(
        history,
        platform=platform,
        status=status,
        message=message,
        timezone_name=settings.timezone,
        retention_days=settings.log_retention_days,
    )
    try:
        write_update_log(
            settings.log_path,
            {
                "capturedAt": snapshot.get("capturedAt"),
                "platform": platform,
                "status": status,
                "message": message,
            },
        )
    except OSError:
        history = append_fetch_log(
            history,
            platform="system",
            status="failed",
            message="本地日志文件不可写。",
            timezone_name=settings.timezone,
            retention_days=settings.log_retention_days,
        )
    return history


def _manual_platform_snapshots(settings: Settings) -> dict[str, dict[str, Any]]:
    if not settings.manual_platform_enabled:
        return {}
    snapshots = load_manual_platform_snapshots(settings.manual_platform_path, settings.timezone)
    result: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        platform = str(snapshot.get("platform") or "")
        if not platform:
            continue
        if not snapshot.get("accountId"):
            snapshot["accountId"] = str(getattr(settings, f"{platform}_account_id", "") or "")
        result[platform] = snapshot
    return result


def _manual_content_import_label(manual_snapshot: dict[str, Any]) -> str:
    source_status = manual_snapshot.get("sourceStatus")
    imported_at = ""
    if isinstance(source_status, dict):
        imported_at = str(source_status.get("importedAt") or "")
    imported_at = imported_at or str(manual_snapshot.get("importedAt") or manual_snapshot.get("capturedAt") or "")
    if len(imported_at) >= 10:
        return f"导入于 {imported_at[:10]}"
    return "手动导入缓存"


def _annotated_manual_content_items(manual_snapshot: dict[str, Any], manual_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import_label = _manual_content_import_label(manual_snapshot)
    annotated: list[dict[str, Any]] = []
    for item in manual_items:
        if not isinstance(item, dict):
            continue
        item_copy = deepcopy(item)
        if not item_copy.get("data_source"):
            item_copy["data_source"] = "手动导入缓存"
        if not item_copy.get("metric_scope"):
            item_copy["metric_scope"] = import_label
        annotated.append(item_copy)
    return annotated


def _manual_cache_content_counts(items: list[dict[str, Any]]) -> tuple[int, int]:
    manual_count = sum(1 for item in items if str(item.get("data_source") or "") == "手动导入缓存")
    return max(len(items) - manual_count, 0), manual_count


def _with_manual_content_fallback(
    snapshot: dict[str, Any],
    manual_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(manual_snapshot, dict):
        return snapshot
    manual_items = manual_snapshot.get("contentItems")
    if not isinstance(manual_items, list) or not manual_items:
        return snapshot
    current_items = snapshot.get("contentItems") if isinstance(snapshot.get("contentItems"), list) else []
    merged_items = merge_content_items(
        current_items,
        _annotated_manual_content_items(manual_snapshot, manual_items),
        content_limit=60,
    )
    if merged_items == current_items:
        return snapshot
    patched = deepcopy(snapshot)
    patched["contentItems"] = merged_items
    source_status = patched.get("sourceStatus")
    if not isinstance(source_status, dict):
        source_status = {}
    message = str(source_status.get("message") or "")
    live_count, manual_cache_count = _manual_cache_content_counts(merged_items)
    import_label = _manual_content_import_label(manual_snapshot)
    if manual_cache_count and str(source_status.get("status") or "") == "success":
        source_status["status"] = "partial"
    platform_label = {
        "bilibili": "B站",
        "douyin": "抖音",
        "xiaohongshu": "小红书",
    }.get(str(snapshot.get("platform") or ""), "对应平台")
    suffix = (
        f"当前作品明细：实时作品 {live_count} 条，手动缓存 {manual_cache_count} 条（{import_label}）；"
        f"缓存作品可能与{platform_label}当前前台/后台不一致，汇总指标仍来自当前授权后台。"
    )
    source_status["message"] = f"{message} {suffix}".strip()
    patched["sourceStatus"] = source_status
    raw = deepcopy(patched.get("raw")) if isinstance(patched.get("raw"), dict) else {}
    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    summary["manual_content_count"] = len(manual_items)
    summary["merged_content_count"] = len(merged_items)
    summary["live_content_count"] = live_count
    summary["manual_cached_content_count"] = manual_cache_count
    raw["summary"] = summary
    patched["raw"] = raw
    return patched


async def _collect_platform_snapshots(
    history: dict[str, Any],
    settings: Settings,
    latest_bilibili_snapshot: dict[str, Any] | None,
    live_warnings: list[str],
    allow_platform_network: bool = True,
    platforms_to_update: set[str] | None = None,
    platform_fetch_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    manual_snapshots = _manual_platform_snapshots(settings)

    if platforms_to_update is None or "bilibili" in platforms_to_update:
        if settings.bilibili_enabled:
            latest = latest_bilibili_snapshot or _latest_snapshot(history)
            if latest:
                latest_source = str(latest.get("source") or "")
                creator_failed = bool(live_warnings) and (
                    latest_bilibili_snapshot is None or latest_source == "public_partial"
                )
                channel = latest.get("channel") if isinstance(latest.get("channel"), dict) else {}
                try:
                    public_follower = int(channel.get("total_followers") or 0) if latest_source == "public_partial" else 0
                except (TypeError, ValueError):
                    public_follower = 0
                if creator_failed and allow_platform_network:
                    try:
                        refreshed_follower = await BilibiliClient().fetch_public_follower(settings.bilibili_account_id)
                        if refreshed_follower > 0:
                            public_follower = refreshed_follower
                    except Exception:  # noqa: BLE001 - stale cache remains available and clearly labelled.
                        pass
                if creator_failed and public_follower:
                    bilibili_snapshot = platform_snapshot_from_bilibili_public_fallback(
                        latest,
                        follower=public_follower,
                        account_id=settings.bilibili_account_id,
                        timezone_name=settings.timezone,
                        message="; ".join(live_warnings[:1]),
                    )
                else:
                    status = "partial" if live_warnings else latest_source or "success"
                    bilibili_snapshot = platform_snapshot_from_bilibili(
                        latest,
                        account_id=settings.bilibili_account_id,
                        timezone_name=settings.timezone,
                        status=status,
                        message="; ".join(live_warnings[:2]) if live_warnings else None,
                    )
                history = _record_platform_result(
                    history,
                    bilibili_snapshot,
                    settings,
                )
            elif live_warnings:
                history = _record_platform_result(
                    history,
                    failed_platform_snapshot(
                        "bilibili",
                        account_id=settings.bilibili_account_id,
                        timezone_name=settings.timezone,
                        message="; ".join(live_warnings),
                    ),
                    settings,
                )
        else:
            history = _record_platform_result(
                history,
                unavailable_platform_snapshot(
                    "bilibili",
                    account_id=settings.bilibili_account_id,
                    timezone_name=settings.timezone,
                    message="B 站平台未启用。",
                ),
                settings,
            )

    if platforms_to_update is not None and "douyin" not in platforms_to_update:
        douyin_snapshot = None
    elif settings.douyin_enabled:
        try:
            client = DouyinClient(
                settings.douyin_account_id,
                settings.douyin_cookie_present,
                settings.douyin_data_url,
                official_data_url=settings.douyin_official_data_url,
                official_config_present=settings.douyin_official_config_present,
                manual_snapshot=manual_snapshots.get("douyin"),
                allow_network=allow_platform_network,
                content_limit=settings.platform_content_limit,
            )
            douyin_snapshot = await _fetch_with_retries(
                "douyin",
                lambda: client.fetch_snapshot(settings.timezone),
                timeout_seconds=platform_fetch_timeout_seconds,
            )
            douyin_snapshot = _with_manual_content_fallback(douyin_snapshot, manual_snapshots.get("douyin"))
        except Exception as exc:  # noqa: BLE001
            douyin_snapshot = failed_platform_snapshot(
                "douyin",
                account_id=settings.douyin_account_id,
                timezone_name=settings.timezone,
                message=str(exc),
            )
    else:
        douyin_snapshot = unavailable_platform_snapshot(
            "douyin",
            account_id=settings.douyin_account_id,
            timezone_name=settings.timezone,
            message="抖音平台未启用。",
        )
    if douyin_snapshot is not None:
        history = _record_platform_result(history, douyin_snapshot, settings)

    if platforms_to_update is not None and "xiaohongshu" not in platforms_to_update:
        xhs_snapshot = None
    elif settings.xiaohongshu_enabled:
        try:
            client = XiaohongshuClient(
                settings.xiaohongshu_account_id,
                settings.xiaohongshu_cookie_present,
                settings.xiaohongshu_data_url,
                content_data_url=settings.xiaohongshu_content_data_url,
                official_data_url=settings.xiaohongshu_official_data_url,
                official_config_present=settings.xiaohongshu_official_config_present,
                manual_snapshot=manual_snapshots.get("xiaohongshu"),
                allow_network=allow_platform_network,
                content_limit=settings.platform_content_limit,
            )
            xhs_snapshot = await _fetch_with_retries(
                "xiaohongshu",
                lambda: client.fetch_snapshot(settings.timezone),
                timeout_seconds=platform_fetch_timeout_seconds,
            )
            xhs_snapshot = _with_manual_content_fallback(xhs_snapshot, manual_snapshots.get("xiaohongshu"))
        except Exception as exc:  # noqa: BLE001
            xhs_snapshot = failed_platform_snapshot(
                "xiaohongshu",
                account_id=settings.xiaohongshu_account_id,
                timezone_name=settings.timezone,
                message=str(exc),
            )
    else:
        xhs_snapshot = unavailable_platform_snapshot(
            "xiaohongshu",
            account_id=settings.xiaohongshu_account_id,
            timezone_name=settings.timezone,
            message="小红书平台未启用。",
        )
    if xhs_snapshot is not None:
        history = _record_platform_result(history, xhs_snapshot, settings)
    return history


def _load_cache_or_fixture(settings: Settings, warnings: list[str]) -> tuple[dict[str, Any], str]:
    cache = load_history(settings.history_path)
    if cache.get("snapshots"):
        cache["source"] = "cache"
        cache["warnings"] = [*cache.get("warnings", []), *warnings]
        return cache, "cache"
    fixture = load_fixture_history(settings.fixture_path)
    fixture["source"] = "fixture"
    fixture["warnings"] = [*fixture.get("warnings", []), *warnings]
    return fixture, "fixture"


async def build_dashboard(args: argparse.Namespace, settings: Settings) -> dict[str, Any]:
    warnings: list[str] = []
    bilibili_warnings: list[str] = []
    display_warnings: list[str] | None = None
    snapshot_date = _resolve_snapshot_date(args.snapshot_date, settings.timezone)
    live_snapshot: dict[str, Any] | None = None
    bilibili_only = bool(getattr(args, "bilibili_only", False))
    platform_fetch_timeout = getattr(args, "platform_fetch_timeout", None) or settings.platform_fetch_timeout_seconds

    if args.fixture:
        history = load_fixture_history(settings.fixture_path)
        history["source"] = "fixture"
        fixture_warnings = history.get("warnings", [])
        display_warnings = fixture_warnings if isinstance(fixture_warnings, list) else []
    elif args.cache:
        history, _ = _load_cache_or_fixture(settings, warnings)
        cache_warnings = history.get("warnings", [])
        display_warnings = cache_warnings if isinstance(cache_warnings, list) else []
        bilibili_warnings = display_warnings
    else:
        should_live = args.live or bilibili_only or settings.enable_bilibili_fetch
        snapshot = None
        public_snapshot = None
        public_warnings: list[str] = []
        if should_live:
            snapshot, warnings = await _try_live_snapshot(
                settings,
                require_enable_flag=not (args.live or bilibili_only),
            )
            public_snapshot, public_warnings = await _try_public_bilibili_snapshot(settings)
            warnings = [*warnings, *public_warnings]
        else:
            warnings.append("未启用实时获取，已使用缓存或示例数据。")

        if snapshot:
            snapshot = _apply_snapshot_date(snapshot, snapshot_date)
            snapshot["source"] = "live"
            if public_snapshot:
                snapshot = _merge_public_bilibili_snapshot(
                    snapshot,
                    public_snapshot,
                    creator_live=True,
                    creator_warnings=[
                        *(
                            snapshot.get("warnings", [])
                            if isinstance(snapshot.get("warnings"), list)
                            else []
                        ),
                        *public_warnings,
                    ],
                    snapshot_date=snapshot_date,
                )
            live_snapshot = snapshot
            snapshot_warnings = snapshot.get("warnings", [])
            if not isinstance(snapshot_warnings, list):
                snapshot_warnings = []
            if not public_snapshot:
                snapshot_warnings = [*snapshot_warnings, *public_warnings]
                snapshot["warnings"] = snapshot_warnings
            bilibili_warnings = snapshot_warnings
            if _snapshot_has_videos(snapshot):
                history = load_history(settings.history_path)
                history = merge_today_snapshot(history, snapshot)
                history["source"] = "live_partial" if snapshot_warnings else "live"
                history["warnings"] = snapshot_warnings
                display_warnings = snapshot_warnings
            else:
                warnings = [
                    *warnings,
                    *snapshot_warnings,
                    "实时视频列表为空，已使用缓存或示例数据。",
                ]
                history, _ = _load_cache_or_fixture(settings, warnings)
                display_warnings = warnings
                bilibili_warnings = warnings
        else:
            history, _ = _load_cache_or_fixture(settings, warnings)
            cached_snapshot = _latest_snapshot(history)
            if public_snapshot and cached_snapshot and history.get("source") != "fixture":
                cached_snapshot = deepcopy(cached_snapshot)
                last_creator_success = _latest_creator_center_success_at(history)
                if last_creator_success:
                    cached_snapshot["creator_center_cached_at"] = last_creator_success
                fallback_snapshot = _merge_public_bilibili_snapshot(
                    cached_snapshot,
                    public_snapshot,
                    creator_live=False,
                    creator_warnings=warnings,
                    snapshot_date=snapshot_date,
                )
                history = merge_today_snapshot(history, fallback_snapshot)
                history["source"] = "live_partial"
                history["warnings"] = fallback_snapshot["warnings"]
                live_snapshot = fallback_snapshot
                display_warnings = fallback_snapshot["warnings"]
                bilibili_warnings = fallback_snapshot["warnings"]
            elif public_snapshot:
                fallback_snapshot = _merge_public_bilibili_snapshot(
                    None,
                    public_snapshot,
                    creator_live=False,
                    creator_warnings=warnings,
                    snapshot_date=snapshot_date,
                )
                history = merge_today_snapshot(history, fallback_snapshot)
                history["source"] = "live_partial"
                history["warnings"] = fallback_snapshot["warnings"]
                live_snapshot = fallback_snapshot
                display_warnings = fallback_snapshot["warnings"]
                bilibili_warnings = fallback_snapshot["warnings"]
            else:
                display_warnings = warnings
                bilibili_warnings = warnings

    history = await _collect_platform_snapshots(
        history,
        settings,
        live_snapshot,
        bilibili_warnings,
        allow_platform_network=not (args.fixture or args.cache),
        platforms_to_update=set() if args.cache else {"bilibili"} if bilibili_only else None,
        platform_fetch_timeout_seconds=platform_fetch_timeout,
    )
    history = repair_latest_content_thumbnails(history, settings.platform_content_limit)
    latest_network_capture = _latest_network_platform_capture(history)
    if args.fixture:
        history["last_updated"] = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    elif not args.cache and latest_network_capture:
        history["last_updated"] = latest_network_capture
    elif live_snapshot or not history.get("last_updated"):
        history["last_updated"] = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    save_history(history, settings.history_path)
    context = derive_dashboard_context(history, settings, display_warnings=display_warnings)
    output_path = render_dashboard(context, settings)

    feishu_summary = "Feishu sync skipped: disabled by --no-feishu." if args.no_feishu else ""
    latest = _latest_snapshot(history)
    if not args.no_feishu:
        if settings.feishu_enabled and feishu_is_configured() and latest:
            feishu_summary = await upsert_daily_summary(latest, settings.feishu_date_format)
        else:
            feishu_summary = "Feishu sync skipped: missing configuration."

    bark_summary = "Bark skipped: disabled by --no-bark." if args.no_bark else ""
    if not args.no_bark:
        bark_summary = await send_bark_notification(
            context.get("platform_cards", []),
            timezone_name=settings.timezone,
            group=settings.bark_group,
            sound=settings.bark_sound,
        )

    return {
        "history": history,
        "context": context,
        "output_path": output_path,
        "feishu_summary": feishu_summary,
        "bark_summary": bark_summary,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the Bilibili creator analytics dashboard.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fixture", action="store_true", help="Use bundled fixture data and never touch the network.")
    mode.add_argument("--live", action="store_true", help="Try live Bilibili Creator Center fetch when credentials allow.")
    mode.add_argument("--cache", action="store_true", help="Render from existing local history/cache without platform network requests.")
    parser.add_argument(
        "--bilibili-only",
        action="store_true",
        help="Only update the Bilibili live snapshot and keep other platform cache untouched.",
    )
    parser.add_argument(
        "--platform-fetch-timeout",
        type=float,
        default=None,
        help="Total seconds allowed for each non-Bilibili platform fetch.",
    )
    parser.add_argument("--no-feishu", action="store_true", help="Skip optional Feishu Bitable sync.")
    parser.add_argument("--no-bark", action="store_true", help="Skip optional Bark push notification.")
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="Override the live snapshot date: today, yesterday, or YYYY-MM-DD.",
    )
    args = parser.parse_args(argv)
    if args.bilibili_only and (args.fixture or args.cache):
        parser.error("--bilibili-only cannot be combined with --fixture or --cache")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_files()
    settings = load_settings()
    result = asyncio.run(build_dashboard(args, settings))
    history = result["history"]
    context = result["context"]
    output_path = result["output_path"]
    warnings = context.get("warnings", [])

    print(f"source: {history.get('source')}")
    print(f"snapshots: {context.get('snapshot_count', 0)}")
    print(f"videos rendered: {len(context.get('recent_videos', []))}")
    print(f"platforms rendered: {len(context.get('platform_cards', []))}")
    print(f"output: {output_path}")
    print(result["feishu_summary"])
    print(result["bark_summary"])
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"- {warning}")
    else:
        print("warnings: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
