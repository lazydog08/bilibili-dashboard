#!/usr/bin/env python3
"""Validate the local Edge Bilibili session before refreshing collector auth.

Cookie values are deliberately never printed. The collector environment is
updated only after both the public account check and a creator-center endpoint
confirm the expected account.
"""

from __future__ import annotations

import argparse
import asyncio
import http.cookiejar
import os
import re
import signal
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx


NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
CREATOR_STAT_URL = "https://member.bilibili.com/x/web/index/stat"
DEFAULT_EDGE_COOKIE_FILE = (
    Path.home() / "Library/Application Support/Microsoft Edge/Default/Cookies"
)
REQUIRED_COOKIE_NAMES = frozenset({"SESSDATA", "DedeUserID", "bili_jct"})
COOKIE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
ENV_COOKIE_RE = re.compile(r"^\s*(?:export\s+)?BILIBILI_COOKIE=")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str


def _is_bilibili_domain(domain: str) -> bool:
    normalized = domain.lstrip(".").lower()
    return normalized == "bilibili.com" or normalized.endswith(".bilibili.com")


def _cookie_priority(cookie: http.cookiejar.Cookie) -> tuple[int, int, int]:
    domain = str(getattr(cookie, "domain", ""))
    path = str(getattr(cookie, "path", ""))
    return (
        1 if domain == ".bilibili.com" else 0,
        1 if path == "/" else 0,
        len(domain) + len(path),
    )


def cookie_header_from_jar(
    cookies: Iterable[http.cookiejar.Cookie], *, now: float | None = None
) -> str:
    """Build a Bilibili-only Cookie header without logging any values."""
    current_time = time.time() if now is None else now
    selected: dict[str, tuple[tuple[int, int, int], str]] = {}

    for cookie in cookies:
        name = str(getattr(cookie, "name", ""))
        value = str(getattr(cookie, "value", ""))
        domain = str(getattr(cookie, "domain", ""))
        expires = getattr(cookie, "expires", None)
        if not _is_bilibili_domain(domain):
            continue
        if expires is not None and float(expires) <= current_time:
            continue
        if not COOKIE_NAME_RE.fullmatch(name) or not value:
            continue
        if ";" in value or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value):
            continue
        priority = _cookie_priority(cookie)
        previous = selected.get(name)
        if previous is None or priority > previous[0]:
            selected[name] = (priority, value)

    missing = REQUIRED_COOKIE_NAMES.difference(selected)
    if missing:
        raise ValueError("required_auth_cookies_missing")

    account_id = selected["DedeUserID"][1]
    if not account_id.isdigit():
        raise ValueError("browser_account_id_invalid")

    preferred_order = {"SESSDATA": 0, "DedeUserID": 1, "bili_jct": 2}
    ordered_names = sorted(selected, key=lambda name: (preferred_order.get(name, 3), name))
    return "; ".join(f"{name}={selected[name][1]}" for name in ordered_names)


def _load_edge_cookie_header_direct(cookie_file: Path) -> str:
    try:
        import browser_cookie3
    except ImportError as exc:  # pragma: no cover - guarded by the launcher
        raise RuntimeError("browser_cookie_reader_unavailable") from exc

    if not cookie_file.is_file():
        raise RuntimeError("edge_cookie_database_missing")
    try:
        jar = browser_cookie3.edge(cookie_file=str(cookie_file), domain_name=".bilibili.com")
    except Exception as exc:  # browser/keychain errors vary across macOS releases
        raise RuntimeError("edge_cookie_database_unreadable") from exc
    return cookie_header_from_jar(jar)


def _run_cookie_reader(command: list[str], *, timeout: float) -> str:
    """Run keychain-backed extraction in a killable process group."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
        raise RuntimeError("edge_cookie_reader_timed_out") from exc

    if process.returncode != 0 or not stdout or len(stdout) > 65536:
        raise RuntimeError("edge_cookie_database_unreadable")
    try:
        return stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("edge_cookie_output_invalid") from exc


def load_edge_cookie_header(cookie_file: Path | None = None) -> str:
    database = (cookie_file or DEFAULT_EDGE_COOKIE_FILE).expanduser().resolve()
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--read-edge-cookie",
        "--cookie-file",
        str(database),
    ]
    return _run_cookie_reader(command, timeout=15.0)


async def validate_cookie(
    cookie_header: str,
    expected_account_id: str,
    *,
    timeout: float = 20.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ValidationResult:
    if not expected_account_id.isdigit():
        return ValidationResult(False, "configured_account_id_invalid")

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie_header,
        "Referer": "https://member.bilibili.com/platform/home",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
        ),
    }
    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        ) as client:
            nav_response = await client.get(NAV_URL)
            nav_response.raise_for_status()
            nav_payload = nav_response.json()
            if not isinstance(nav_payload, dict):
                return ValidationResult(False, "browser_session_not_authenticated")
            nav_data = nav_payload.get("data")
            if nav_payload.get("code") != 0 or not isinstance(nav_data, dict):
                return ValidationResult(False, "browser_session_not_authenticated")
            if nav_data.get("isLogin") is not True:
                return ValidationResult(False, "browser_session_not_authenticated")
            if str(nav_data.get("mid", "")) != expected_account_id:
                return ValidationResult(False, "browser_account_mismatch")

            creator_response = await client.get(CREATOR_STAT_URL)
            creator_response.raise_for_status()
            creator_payload = creator_response.json()
            if not isinstance(creator_payload, dict) or creator_payload.get("code") != 0:
                return ValidationResult(False, "creator_session_not_authenticated")
    except (httpx.HTTPError, ValueError, TypeError):
        return ValidationResult(False, "bilibili_validation_unavailable")

    return ValidationResult(True, "validated")


def update_env_cookie(env_file: Path, cookie_header: str) -> None:
    """Atomically update only BILIBILI_COOKIE and force owner-only permissions."""
    if env_file.is_symlink():
        raise RuntimeError("collector_env_symlink_refused")
    try:
        file_stat = env_file.stat()
    except FileNotFoundError as exc:
        raise RuntimeError("collector_env_missing") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError("collector_env_not_regular_file")

    original = env_file.read_text(encoding="utf-8")
    replacement = f"BILIBILI_COOKIE={shlex.quote(cookie_header)}\n"
    output: list[str] = []
    replaced = False
    for line in original.splitlines(keepends=True):
        if ENV_COOKIE_RE.match(line):
            if not replaced:
                output.append(replacement)
                replaced = True
            continue
        output.append(line)
    if not replaced:
        if output and not output[-1].endswith("\n"):
            output[-1] += "\n"
        output.append(replacement)

    env_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{env_file.name}.", dir=env_file.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("".join(output))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, env_file)
        os.chmod(env_file, 0o600)
        directory_fd = os.open(env_file.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


async def run(args: argparse.Namespace) -> int:
    try:
        cookie_header = load_edge_cookie_header(args.cookie_file)
    except RuntimeError:
        print("Bilibili Edge cookie reader or macOS keychain is unavailable; collector fallback remains active.")
        return 4
    except ValueError:
        print("Bilibili Edge session is unavailable or incomplete; collector fallback remains active.")
        return 2

    validation = await validate_cookie(
        cookie_header,
        args.account_id,
        timeout=args.timeout,
    )
    if not validation.ok:
        print("Bilibili Edge session did not pass account and creator-center validation; collector fallback remains active.")
        return 2
    if args.check:
        print("Bilibili Edge session passed account and creator-center validation.")
        return 0

    try:
        update_env_cookie(args.env_file, cookie_header)
    except (OSError, RuntimeError, UnicodeError):
        print("Bilibili session was valid, but the local collector credential could not be updated.")
        return 3
    print("Bilibili browser session validated and local collector credential refreshed.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--account-id")
    parser.add_argument("--cookie-file", type=Path)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--read-edge-cookie", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.read_edge_cookie:
        try:
            cookie_header = _load_edge_cookie_header_direct(
                (args.cookie_file or DEFAULT_EDGE_COOKIE_FILE).expanduser().resolve()
            )
        except (RuntimeError, ValueError):
            return 2
        sys.stdout.write(cookie_header)
        return 0
    if args.env_file is None or args.account_id is None:
        print("--env-file and --account-id are required.", file=sys.stderr)
        return 3
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
