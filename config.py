from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_PATHS = [
    PROJECT_ROOT / "data" / "secrets" / "dashboard.env",
    Path.home() / ".config" / "bilibili-dashboard" / "dashboard.env",
]


def _clean_env_value(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def load_env_files(paths: list[Path] | None = None) -> list[Path]:
    loaded: list[Path] = []
    for path in paths or DEFAULT_ENV_PATHS:
        env_path = Path(path)
        if not env_path.exists():
            continue
        loaded.append(env_path)
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key.removeprefix("export ").strip()
            if not key:
                continue
            os.environ.setdefault(key, _clean_env_value(value))
    return loaded


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


def _split_list(value: str | None, fallback: list[str]) -> list[str]:
    if not value:
        return fallback
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or fallback


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


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
    update_times: list[str] = field(default_factory=list)
    update_interval_minutes: int | None = None
    page_refresh_seconds: int = 0
    log_retention_days: int = 30
    platform_content_limit: int = 50
    log_path: Path = PROJECT_ROOT / "data" / "logs" / "update.log"
    manual_platform_path: Path = PROJECT_ROOT / "data" / "manual_platform_metrics.json"
    manual_platform_enabled: bool = True
    bilibili_fetch_timeout_seconds: float = 180.0
    platform_fetch_timeout_seconds: float = 45.0
    bark_device_key_present: bool = False
    bark_group: str = "数据看板"
    bark_sound: str = "minuet"
    bilibili_enabled: bool = True
    bilibili_account_id: str = ""
    douyin_enabled: bool = True
    douyin_account_id: str = ""
    douyin_cookie_present: bool = False
    douyin_data_url_present: bool = False
    douyin_data_url: str = ""
    douyin_official_config_present: bool = False
    douyin_official_data_url_present: bool = False
    douyin_official_data_url: str = ""
    xiaohongshu_enabled: bool = True
    xiaohongshu_account_id: str = ""
    xiaohongshu_cookie_present: bool = False
    xiaohongshu_data_url_present: bool = False
    xiaohongshu_data_url: str = ""
    xiaohongshu_content_data_url_present: bool = False
    xiaohongshu_content_data_url: str = ""
    xiaohongshu_official_config_present: bool = False
    xiaohongshu_official_data_url_present: bool = False
    xiaohongshu_official_data_url: str = ""


def load_settings() -> Settings:
    timezone = os.getenv("DASHBOARD_TIMEZONE", "Asia/Shanghai")
    update_interval_minutes = max(1, min(_env_int("DASHBOARD_UPDATE_INTERVAL_MINUTES", 30), 1440))
    refresh_seconds = _env_optional_int("DASHBOARD_PAGE_REFRESH_SECONDS")
    if refresh_seconds is None:
        refresh_seconds = update_interval_minutes * 60
    refresh_seconds = max(0, min(refresh_seconds, 86400))
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
        update_times=_split_list(os.getenv("DASHBOARD_UPDATE_TIMES"), ["12:30", "20:00"]),
        update_interval_minutes=update_interval_minutes,
        page_refresh_seconds=refresh_seconds,
        log_retention_days=_env_int("LOG_RETENTION_DAYS", 30),
        platform_content_limit=max(1, min(_env_int("PLATFORM_CONTENT_LIMIT", 50), 50)),
        log_path=PROJECT_ROOT / "data" / "logs" / "update.log",
        manual_platform_path=_env_path(
            "MANUAL_PLATFORM_DATA_PATH",
            PROJECT_ROOT / "data" / "manual_platform_metrics.json",
        ),
        manual_platform_enabled=_env_flag("MANUAL_PLATFORM_ENABLED", True),
        bilibili_fetch_timeout_seconds=max(
            1.0,
            min(_env_float("BILIBILI_FETCH_TIMEOUT_SECONDS", 180.0), 300.0),
        ),
        platform_fetch_timeout_seconds=max(
            1.0,
            min(_env_float("PLATFORM_FETCH_TIMEOUT_SECONDS", 45.0), 300.0),
        ),
        bark_device_key_present=bool(os.getenv("BARK_DEVICE_KEY")),
        bark_group=os.getenv("BARK_GROUP", "数据看板") or "数据看板",
        bark_sound=os.getenv("BARK_SOUND", "minuet") or "minuet",
        bilibili_enabled=_env_flag("BILIBILI_ENABLED", True),
        bilibili_account_id=os.getenv("BILIBILI_ACCOUNT_ID") or "516185777",
        douyin_enabled=_env_flag("DOUYIN_ENABLED", True),
        douyin_account_id=os.getenv("DOUYIN_ACCOUNT_ID", ""),
        douyin_cookie_present=bool(os.getenv("DOUYIN_COOKIE") or os.getenv("DOUYIN_TOKEN")),
        douyin_data_url_present=bool(os.getenv("DOUYIN_DATA_URL")),
        douyin_data_url=os.getenv("DOUYIN_DATA_URL", ""),
        douyin_official_config_present=bool(
            os.getenv("DOUYIN_ACCESS_TOKEN")
            or os.getenv("DOUYIN_APP_ID")
            or os.getenv("DOUYIN_APP_SECRET")
            or os.getenv("DOUYIN_OPEN_ID")
        ),
        douyin_official_data_url_present=bool(os.getenv("DOUYIN_OFFICIAL_DATA_URL")),
        douyin_official_data_url=os.getenv("DOUYIN_OFFICIAL_DATA_URL", ""),
        xiaohongshu_enabled=_env_flag("XIAOHONGSHU_ENABLED", True),
        xiaohongshu_account_id=os.getenv("XIAOHONGSHU_ACCOUNT_ID", ""),
        xiaohongshu_cookie_present=bool(os.getenv("XIAOHONGSHU_COOKIE") or os.getenv("XIAOHONGSHU_TOKEN")),
        xiaohongshu_data_url_present=bool(os.getenv("XIAOHONGSHU_DATA_URL")),
        xiaohongshu_data_url=os.getenv("XIAOHONGSHU_DATA_URL", ""),
        xiaohongshu_content_data_url_present=bool(os.getenv("XIAOHONGSHU_CONTENT_DATA_URL")),
        xiaohongshu_content_data_url=os.getenv("XIAOHONGSHU_CONTENT_DATA_URL", ""),
        xiaohongshu_official_config_present=bool(
            os.getenv("XIAOHONGSHU_ACCESS_TOKEN")
            or os.getenv("XIAOHONGSHU_APP_ID")
            or os.getenv("XIAOHONGSHU_APP_SECRET")
            or os.getenv("XIAOHONGSHU_OPEN_ID")
        ),
        xiaohongshu_official_data_url_present=bool(os.getenv("XIAOHONGSHU_OFFICIAL_DATA_URL")),
        xiaohongshu_official_data_url=os.getenv("XIAOHONGSHU_OFFICIAL_DATA_URL", ""),
    )
