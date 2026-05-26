from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"{name} is not available")
    return path


def run_checked(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=True)


def test_cloud_update_script_corrects_existing_remote_url(tmp_path: Path) -> None:
    bash = require_tool("bash")
    git = require_tool("git")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "data").mkdir()
    (repo / "dashboard" / "output").mkdir(parents=True)
    (repo / "data" / "history.json").write_text('{"snapshots": []}\n', encoding="utf-8")
    (repo / "dashboard" / "output" / "index.html").write_text("<!doctype html>\n", encoding="utf-8")

    run_checked([git, "init"], cwd=repo)
    run_checked([git, "config", "user.email", "test@example.com"], cwd=repo)
    run_checked([git, "config", "user.name", "Test Bot"], cwd=repo)
    run_checked([git, "add", "data/history.json", "dashboard/output/index.html"], cwd=repo)
    run_checked([git, "commit", "-m", "initial"], cwd=repo)
    run_checked([git, "remote", "add", "origin", "https://github.com/lazydog08/bilibili-dashboard.git"], cwd=repo)

    expected_remote = "git@github.com:lazydog08/bilibili-dashboard.git"
    env = os.environ.copy()
    env.update(
        {
            "DASHBOARD_REPO_DIR": str(repo),
            "DASHBOARD_ENV_FILE": str(tmp_path / "missing.env"),
            "DASHBOARD_UPDATE_LOG": str(tmp_path / "nas-update.log"),
            "DASHBOARD_CLOUD_LOCK_DIR": str(tmp_path / "nas-cloud-update.lock"),
            "DASHBOARD_CLOUD_REMOTE_URL": expected_remote,
            "DASHBOARD_GIT_PULL_BEFORE_PUSH": "0",
            "DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH": "0",
        }
    )

    result = subprocess.run(
        [bash, str(REPO_ROOT / "scripts" / "nas_update_and_push_cloud.sh")],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )

    log_text = (tmp_path / "nas-update.log").read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr + log_text
    remote = run_checked([git, "remote", "get-url", "origin"], cwd=repo).stdout.strip()
    assert remote == expected_remote
    assert "does not match configured cloud remote" in log_text


def test_cron_installer_renders_ugreen_root_su_dry_run(tmp_path: Path) -> None:
    bash = require_tool("bash")
    env = os.environ.copy()
    env.update(
        {
            "DASHBOARD_NAS_CRON_DRY_RUN": "1",
            "DASHBOARD_NAS_CRON_MODE": "ugreen-root",
            "DASHBOARD_NAS_RUN_AS_USER": "小黑",
            "DASHBOARD_REPO_DIR": "/home/小黑/bilibili-dashboard",
        }
    )

    result = subprocess.run(
        [bash, str(REPO_ROOT / "scripts" / "install_nas_hourly_cron.sh")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "# BEGIN bilibili-dashboard NAS update" in result.stdout
    assert "*/30 * * * * /bin/su - '小黑' -c" in result.stdout
    assert "cd '\\''/home/小黑/bilibili-dashboard'\\'' && DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH=1" in result.stdout
    assert "# END bilibili-dashboard NAS update" in result.stdout


def test_nas_update_script_fetches_comments_before_publish() -> None:
    script = (REPO_ROOT / "scripts" / "nas_update_dashboard.sh").read_text(encoding="utf-8")

    assert "ENABLE_COMMENT_INSIGHTS" in script
    assert "scripts/fetch_bilibili_comments.py" in script
    assert (REPO_ROOT / "scripts" / "fetch_bilibili_comments.py").exists()
    assert '"$REPO_DIR/main.py" "--cache" "--no-feishu" "--no-bark"' in script
    assert '"--cache" "--no-feishu" "--no-bark"' in script
