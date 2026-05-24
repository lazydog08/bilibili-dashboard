from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from analytics import DEFAULT_TIMEZONE


BARK_API_BASE = "https://api.day.app"


def _line(card: dict[str, Any]) -> str:
    fans = card.get("fans", {}).get("label", "--")
    cycle = next((item for item in card.get("growth", []) if item.get("title") == "相比昨日的涨粉"), {})
    seven = next((item for item in card.get("growth", []) if item.get("title") == "7日涨粉"), {})
    suffix = ""
    if str(card.get("status_label", "")) not in {"成功", "部分可用"}:
        suffix = f"（{card.get('status_label', '暂不可用')}）"
    return (
        f"{card.get('name')} {fans}"
        f"（{cycle.get('value', {}).get('label', '--')} / 7日 {seven.get('value', {}).get('label', '--')}）"
        f"{suffix}"
    )


def format_bark_summary(platform_cards: list[dict[str, Any]], timezone_name: str = DEFAULT_TIMEZONE) -> tuple[str, str]:
    now = datetime.now(ZoneInfo(timezone_name))
    has_any_success = any(str(card.get("status_label")) in {"成功", "部分可用"} for card in platform_cards)
    title_prefix = "看板更新完成" if has_any_success else "看板更新失败"
    title = f"{title_prefix} · {now:%H:%M}"
    if has_any_success:
        body = "\n".join(_line(card) for card in platform_cards)
    else:
        lines = [f"{card.get('name')}：{card.get('status_label', '失败')}" for card in platform_cards]
        body = "\n".join(lines) + "\n失败详情见日志。"
    return title, body


async def send_bark_notification(
    platform_cards: list[dict[str, Any]],
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
    group: str = "数据看板",
    sound: str = "minuet",
) -> str:
    device_key = os.getenv("BARK_DEVICE_KEY", "").strip()
    if not device_key:
        return "Bark 未配置，跳过推送。"
    title, body = format_bark_summary(platform_cards, timezone_name)
    url = f"{BARK_API_BASE}/{quote(device_key, safe='')}/{quote(title, safe='')}/{quote(body, safe='')}"
    params = {"group": group, "sound": sound}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") not in {200, "200", 0, "0"}:
                return f"Bark 推送失败：code={payload.get('code')}"
            return "Bark 推送完成。"
    except Exception as exc:  # noqa: BLE001 - push failure must not break dashboard updates.
        return f"Bark 推送失败：{type(exc).__name__}"
