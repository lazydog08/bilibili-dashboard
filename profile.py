from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT


DEFAULT_PROFILE = {
    "name": "懒狗小黑",
    "mid": "516185777",
    "avatar_src": "assets/channel-avatar.jpg",
    "sign": "频道数据看板",
    "official_title": "B站创作者",
    "level_label": "",
}


def _format_number(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "--"


def _load_profile(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _safe_asset_src(value: Any) -> str:
    text = str(value or "").strip()
    if (
        text.startswith("assets/")
        and ".." not in text
        and '"' not in text
        and "'" not in text
        and "<" not in text
        and ">" not in text
    ):
        return text
    return str(DEFAULT_PROFILE["avatar_src"])


def build_brand_profile(config: Any = None, follower_count: Any = None) -> dict[str, Any]:
    data = {**DEFAULT_PROFILE, **_load_profile(PROJECT_ROOT / "data" / "public_profile.json")}
    mid = str(data.get("mid") or getattr(config, "bilibili_account_id", "") or DEFAULT_PROFILE["mid"])
    followers_label = f"{_format_number(follower_count)} 粉丝" if follower_count is not None else "粉丝数据同步中"
    return {
        "name": str(data.get("name") or DEFAULT_PROFILE["name"]),
        "subtitle": f"B站 UID {mid}",
        "product": "频道数据看板",
        "avatar_src": _safe_asset_src(data.get("avatar_src")),
        "sign": str(data.get("sign") or DEFAULT_PROFILE["sign"]),
        "official_title": str(data.get("official_title") or DEFAULT_PROFILE["official_title"]),
        "level_label": str(data.get("level_label") or ""),
        "followers_label": followers_label,
    }
