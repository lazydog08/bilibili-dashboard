from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"{name} is not available")
    return path


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)


def _setup_runtime(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    git = _tool("git")
    remote = tmp_path / "remote.git"
    runtime = tmp_path / "runtime"
    _run([git, "init", "--bare", "--initial-branch=main", str(remote)], tmp_path)
    _run([git, "init", "--initial-branch=main", str(runtime)], tmp_path)
    (runtime / "scripts").mkdir()
    (runtime / "data").mkdir()
    shutil.copy2(REPO_ROOT / "scripts" / "mac_mini_update_and_sync.sh", runtime / "scripts")
    shutil.copy2(REPO_ROOT / "scripts" / "noon_watchdog.py", runtime / "scripts")
    (runtime / "dashboard.env").write_text("\n", encoding="utf-8")
    _run([git, "config", "user.email", "test@example.com"], runtime)
    _run([git, "config", "user.name", "Test Bot"], runtime)
    _run([git, "remote", "add", "origin", str(remote)], runtime)
    _run([git, "add", "scripts"], runtime)
    _run([git, "commit", "-m", "initial"], runtime)
    _run([git, "push", "-u", "origin", "main"], runtime)

    env = os.environ.copy()
    env.update(
        {
            "DASHBOARD_REPO_DIR": str(runtime),
            "DASHBOARD_MAC_RUNTIME_ROOT": str(runtime),
            "DASHBOARD_ENV_FILE": str(runtime / "dashboard.env"),
            "DASHBOARD_MAC_LOG_DIR": str(tmp_path / "logs"),
            "DASHBOARD_MAC_DRY_RUN": "1",
        }
    )
    return remote, runtime, env


def _run_collector(runtime: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_tool("bash"), str(runtime / "scripts" / "mac_mini_update_and_sync.sh")],
        cwd=runtime,
        env=env,
        text=True,
        capture_output=True,
    )


def test_mac_mini_preflight_accepts_clean_main(tmp_path: Path) -> None:
    _, runtime, env = _setup_runtime(tmp_path)

    result = _run_collector(runtime, env)

    assert result.returncode == 0, result.stdout + result.stderr
    log = (tmp_path / "logs" / "collector.log").read_text(encoding="utf-8")
    assert "Dry run: Mac mini collector configuration and GitHub publishing are ready." in log
    assert not (runtime / "data" / "private" / "mac-mini-collector.pause").exists()


def test_mac_mini_preflight_rejects_detached_head_and_pauses(tmp_path: Path) -> None:
    git = _tool("git")
    _, runtime, env = _setup_runtime(tmp_path)
    _run([git, "switch", "--detach"], runtime)

    result = _run_collector(runtime, env)

    assert result.returncode == 10
    pause = (runtime / "data" / "private" / "mac-mini-collector.pause").read_text(encoding="utf-8")
    assert "expected_main_got_detached_head" in pause
    assert not (runtime / "data" / "logs" / "mac-mini-collector.lock").exists()


def test_mac_mini_preflight_fast_forwards_complete_cloud_baseline(tmp_path: Path) -> None:
    git = _tool("git")
    remote, runtime, env = _setup_runtime(tmp_path)
    upstream = tmp_path / "upstream"
    _run([git, "clone", str(remote), str(upstream)], tmp_path)
    _run([git, "config", "user.email", "test@example.com"], upstream)
    _run([git, "config", "user.name", "Test Bot"], upstream)
    (upstream / "data").mkdir(exist_ok=True)
    (upstream / "data" / "history.json").write_text('{"snapshots": ["complete-cloud-baseline"]}\n', encoding="utf-8")
    _run([git, "add", "data/history.json"], upstream)
    _run([git, "commit", "-m", "cloud fallback update"], upstream)
    _run([git, "push", "origin", "main"], upstream)

    result = _run_collector(runtime, env)

    assert result.returncode == 0, result.stdout + result.stderr
    relationship = _run([git, "rev-list", "--left-right", "--count", "main...origin/main"], runtime)
    assert relationship.stdout.strip() == "0\t0"
    assert "complete-cloud-baseline" in (runtime / "data" / "history.json").read_text(encoding="utf-8")
