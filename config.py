from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_labels(value: str | None, fallback: list[str]) -> list[str]:
    if not value:
        return fallback
    labels = [item.strip() for item in value.split(",") if item.strip()]
    return labels[:4] if labels else fallback


@dataclass(frozen=True)
class Settings:
    enable_bilibili_fetch: bool
    bilibili_cookie_present: bool
    timezone: str
    history_path: Path
    fixture_path: Path
    template_path: Path
    output_path: Path
    kpi_labels: list[str] = field(default_factory=list)
    feishu_enabled: bool = False
    feishu_date_format: str = "iso"


def load_settings() -> Settings:
    timezone = os.getenv("DASHBOARD_TIMEZONE", "Asia/Shanghai")
    live_kpi_labels = _split_labels(
        os.getenv("BILIBILI_DASHBOARD_KPI_LABELS"),
        ["总粉丝数", "7日涨粉", "总播放量", "总点赞数"],
    )
    feishu_required = [
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_BASE_APP_TOKEN",
        "FEISHU_TABLE_ID",
    ]
    feishu_enabled = all(bool(os.getenv(name)) for name in feishu_required)
    feishu_date_format = os.getenv("FEISHU_DATE_FORMAT", "iso").strip().lower() or "iso"
    if feishu_date_format not in {"iso", "ms"}:
        feishu_date_format = "iso"

    return Settings(
        enable_bilibili_fetch=_env_flag("ENABLE_BILIBILI_FETCH", False),
        bilibili_cookie_present=bool(os.getenv("BILIBILI_COOKIE")),
        timezone=timezone,
        history_path=PROJECT_ROOT / "data" / "history.json",
        fixture_path=PROJECT_ROOT / "data" / "fixtures" / "sample_history.json",
        template_path=PROJECT_ROOT / "dashboard" / "template.html",
        output_path=PROJECT_ROOT / "dashboard" / "output" / "index.html",
        kpi_labels=live_kpi_labels,
        feishu_enabled=feishu_enabled,
        feishu_date_format=feishu_date_format,
    )
