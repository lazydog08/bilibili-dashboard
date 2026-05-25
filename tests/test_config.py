from __future__ import annotations

import os

from config import load_env_files, load_settings


def test_load_env_files_sets_missing_values_without_overwriting(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / "dashboard.env"
    env_path.write_text(
        "\n".join(
            [
                "NEW_VALUE=from-file",
                "EXISTING_VALUE=from-file",
                "export QUOTED_VALUE='quoted text'",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("NEW_VALUE", raising=False)
    monkeypatch.delenv("QUOTED_VALUE", raising=False)
    monkeypatch.setenv("EXISTING_VALUE", "from-env")

    assert load_env_files([env_path]) == [env_path]
    assert os.getenv("NEW_VALUE") == "from-file"
    assert os.getenv("EXISTING_VALUE") == "from-env"
    assert os.getenv("QUOTED_VALUE") == "quoted text"


def test_platform_fetch_timeout_setting_is_clamped(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_FETCH_TIMEOUT_SECONDS", "0")
    assert load_settings().platform_fetch_timeout_seconds == 1.0

    monkeypatch.setenv("PLATFORM_FETCH_TIMEOUT_SECONDS", "999")
    assert load_settings().platform_fetch_timeout_seconds == 300.0


def test_bilibili_fetch_timeout_setting_is_clamped(monkeypatch) -> None:
    monkeypatch.setenv("BILIBILI_FETCH_TIMEOUT_SECONDS", "0")
    assert load_settings().bilibili_fetch_timeout_seconds == 1.0

    monkeypatch.setenv("BILIBILI_FETCH_TIMEOUT_SECONDS", "999")
    assert load_settings().bilibili_fetch_timeout_seconds == 300.0


def test_update_interval_setting_is_clamped(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_UPDATE_INTERVAL_MINUTES", "30")
    settings = load_settings()
    assert settings.update_interval_minutes == 30
    assert settings.page_refresh_seconds == 1800

    monkeypatch.setenv("DASHBOARD_UPDATE_INTERVAL_MINUTES", "0")
    assert load_settings().update_interval_minutes == 1

    monkeypatch.setenv("DASHBOARD_PAGE_REFRESH_SECONDS", "0")
    assert load_settings().page_refresh_seconds == 0


def test_nas_finder_url_setting_has_default_and_override(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_NAS_FINDER_URL", raising=False)
    assert load_settings().nas_finder_url.startswith("smb://192.168.31.68/")

    monkeypatch.setenv("DASHBOARD_NAS_FINDER_URL", "smb://nas.local/share")
    assert load_settings().nas_finder_url == "smb://nas.local/share"
