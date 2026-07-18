from __future__ import annotations

import argparse
import asyncio
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

from scripts import refresh_bilibili_browser_cookie as refresh


def make_cookie(
    name: str,
    value: str,
    *,
    domain: str = ".bilibili.com",
    path: str = "/",
    expires: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        value=value,
        domain=domain,
        path=path,
        expires=expires,
    )


def valid_cookie_jar() -> list[SimpleNamespace]:
    return [
        make_cookie("SESSDATA", "test-session"),
        make_cookie("DedeUserID", "516185777"),
        make_cookie("bili_jct", "test-csrf"),
        make_cookie("buvid3", "test-browser-id"),
    ]


def test_cookie_header_uses_only_current_bilibili_cookies() -> None:
    cookies = valid_cookie_jar() + [
        make_cookie("foreign", "must-not-copy", domain="example.com"),
        make_cookie("expired", "must-not-copy", expires=100),
        make_cookie("unsafe", "line;break"),
        make_cookie("tabbed", "line\tbreak"),
    ]

    header = refresh.cookie_header_from_jar(cookies, now=200)

    assert "SESSDATA=test-session" in header
    assert "DedeUserID=516185777" in header
    assert "bili_jct=test-csrf" in header
    assert "buvid3=test-browser-id" in header
    assert "must-not-copy" not in header
    assert "line;break" not in header
    assert "line\tbreak" not in header


def test_cookie_header_requires_complete_auth_cookie_set() -> None:
    cookies = [cookie for cookie in valid_cookie_jar() if cookie.name != "bili_jct"]

    try:
        refresh.cookie_header_from_jar(cookies)
    except ValueError as exc:
        assert str(exc) == "required_auth_cookies_missing"
    else:  # pragma: no cover - makes the intended failure explicit
        raise AssertionError("incomplete browser auth must be rejected")


def test_validate_cookie_requires_expected_account_and_creator_access() -> None:
    async def check(account_id: str, creator_code: int = 0) -> refresh.ValidationResult:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url == httpx.URL(refresh.NAV_URL):
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"isLogin": True, "mid": 516185777}},
                )
            return httpx.Response(200, json={"code": creator_code, "data": {}})

        return await refresh.validate_cookie(
            "SESSDATA=test-session",
            account_id,
            transport=httpx.MockTransport(handler),
        )

    assert asyncio.run(check("999999")).reason == "browser_account_mismatch"
    assert asyncio.run(check("516185777", creator_code=-101)).reason == (
        "creator_session_not_authenticated"
    )
    assert asyncio.run(check("516185777")).ok is True


def test_invalid_browser_session_never_changes_collector_env(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env_file = tmp_path / "dashboard.env"
    original = "KEEP_ME=yes\nBILIBILI_COOKIE='old-value'\n"
    env_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(refresh, "load_edge_cookie_header", lambda _path: "new-private-value")

    async def reject(*_args, **_kwargs) -> refresh.ValidationResult:
        return refresh.ValidationResult(False, "creator_session_not_authenticated")

    monkeypatch.setattr(refresh, "validate_cookie", reject)
    args = argparse.Namespace(
        cookie_file=None,
        account_id="516185777",
        timeout=1.0,
        check=False,
        env_file=env_file,
    )

    assert asyncio.run(refresh.run(args)) == 2
    assert env_file.read_text(encoding="utf-8") == original
    assert "new-private-value" not in capsys.readouterr().out


def test_cookie_reader_failure_is_distinct_from_expired_login(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env_file = tmp_path / "dashboard.env"
    env_file.write_text("KEEP_ME=yes\n", encoding="utf-8")

    def fail_reader(_path):
        raise RuntimeError("edge_cookie_reader_timed_out")

    monkeypatch.setattr(refresh, "load_edge_cookie_header", fail_reader)
    args = argparse.Namespace(
        cookie_file=None,
        account_id="516185777",
        timeout=1.0,
        check=False,
        env_file=env_file,
    )

    assert asyncio.run(refresh.run(args)) == 4
    assert env_file.read_text(encoding="utf-8") == "KEEP_ME=yes\n"
    assert "keychain" in capsys.readouterr().out.lower()


def test_valid_browser_session_atomically_replaces_only_cookie(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env_file = tmp_path / "dashboard.env"
    env_file.write_text(
        "KEEP_ME=yes\nBILIBILI_COOKIE='old-value'\nBILIBILI_COOKIE='duplicate'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(refresh, "load_edge_cookie_header", lambda _path: "new-private-value")

    async def accept(*_args, **_kwargs) -> refresh.ValidationResult:
        return refresh.ValidationResult(True, "validated")

    monkeypatch.setattr(refresh, "validate_cookie", accept)
    args = argparse.Namespace(
        cookie_file=None,
        account_id="516185777",
        timeout=1.0,
        check=False,
        env_file=env_file,
    )

    assert asyncio.run(refresh.run(args)) == 0
    updated = env_file.read_text(encoding="utf-8")
    assert updated.startswith("KEEP_ME=yes\n")
    assert updated.count("BILIBILI_COOKIE=") == 1
    assert "old-value" not in updated
    assert "duplicate" not in updated
    assert "new-private-value" in updated
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "new-private-value" not in capsys.readouterr().out


def test_env_symlink_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "target.env"
    target.write_text("KEEP_ME=yes\n", encoding="utf-8")
    link = tmp_path / "dashboard.env"
    link.symlink_to(target)

    try:
        refresh.update_env_cookie(link, "private-value")
    except RuntimeError as exc:
        assert str(exc) == "collector_env_symlink_refused"
    else:  # pragma: no cover
        raise AssertionError("symlink targets must never be overwritten")

    assert target.read_text(encoding="utf-8") == "KEEP_ME=yes\n"


def test_browser_cookie_reader_has_a_hard_timeout() -> None:
    started = time.monotonic()
    try:
        refresh._run_cookie_reader(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.05,
        )
    except RuntimeError as exc:
        assert str(exc) == "edge_cookie_reader_timed_out"
    else:  # pragma: no cover
        raise AssertionError("a stalled keychain reader must be terminated")
    assert time.monotonic() - started < 3
