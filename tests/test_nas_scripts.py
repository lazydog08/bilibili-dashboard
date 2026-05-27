from __future__ import annotations

import json
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


def test_cloud_update_script_pushes_public_nas_status(tmp_path: Path) -> None:
    bash = require_tool("bash")
    git = require_tool("git")
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    run_checked([git, "init", "--bare", "--initial-branch=main", str(remote)], cwd=tmp_path)
    run_checked([git, "init", "--initial-branch=main", str(repo)], cwd=tmp_path)
    (repo / "data").mkdir()
    (repo / "dashboard" / "output").mkdir(parents=True)
    (repo / "data" / "history.json").write_text('{"snapshots": []}\n', encoding="utf-8")
    (repo / "data" / "nas_status.json").write_text('{"last_run_at": "old"}\n', encoding="utf-8")
    (repo / "dashboard" / "output" / "nas_status.json").write_text('{"last_run_at": "old"}\n', encoding="utf-8")
    (repo / "dashboard" / "output" / "index.html").write_text("<!doctype html>\n", encoding="utf-8")

    run_checked([git, "config", "user.email", "test@example.com"], cwd=repo)
    run_checked([git, "config", "user.name", "Test Bot"], cwd=repo)
    run_checked([git, "remote", "add", "origin", str(remote)], cwd=repo)
    run_checked(
        [git, "add", "data/history.json", "data/nas_status.json", "dashboard/output/index.html", "dashboard/output/nas_status.json"],
        cwd=repo,
    )
    run_checked([git, "commit", "-m", "initial"], cwd=repo)
    run_checked([git, "push", "-u", "origin", "main"], cwd=repo)

    (repo / "data" / "nas_status.json").write_text('{"last_run_at": "new"}\n', encoding="utf-8")
    (repo / "dashboard" / "output" / "nas_status.json").write_text('{"last_run_at": "new"}\n', encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "DASHBOARD_REPO_DIR": str(repo),
            "DASHBOARD_ENV_FILE": str(tmp_path / "missing.env"),
            "DASHBOARD_UPDATE_LOG": str(tmp_path / "nas-update.log"),
            "DASHBOARD_CLOUD_LOCK_DIR": str(tmp_path / "nas-cloud-update.lock"),
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
    pushed_status = run_checked([git, "show", "origin/main:data/nas_status.json"], cwd=repo).stdout
    pushed_pages_status = run_checked([git, "show", "origin/main:dashboard/output/nas_status.json"], cwd=repo).stdout
    assert '"last_run_at": "new"' in pushed_status
    assert '"last_run_at": "new"' in pushed_pages_status


def test_cloud_update_script_skips_stale_generated_commit_after_remote_conflict(tmp_path: Path) -> None:
    bash = require_tool("bash")
    git = require_tool("git")
    remote = tmp_path / "remote.git"
    upstream = tmp_path / "upstream"
    repo = tmp_path / "repo"
    run_checked([git, "init", "--bare", "--initial-branch=main", str(remote)], cwd=tmp_path)
    run_checked([git, "init", "--initial-branch=main", str(upstream)], cwd=tmp_path)
    (upstream / "data").mkdir()
    (upstream / "dashboard" / "output").mkdir(parents=True)
    (upstream / "data" / "history.json").write_text('{"snapshots": ["base"]}\n', encoding="utf-8")
    (upstream / "dashboard" / "output" / "index.html").write_text("base\n", encoding="utf-8")
    run_checked([git, "config", "user.email", "test@example.com"], cwd=upstream)
    run_checked([git, "config", "user.name", "Test Bot"], cwd=upstream)
    run_checked([git, "remote", "add", "origin", str(remote)], cwd=upstream)
    run_checked([git, "add", "data/history.json", "dashboard/output/index.html"], cwd=upstream)
    run_checked([git, "commit", "-m", "initial"], cwd=upstream)
    run_checked([git, "push", "-u", "origin", "main"], cwd=upstream)

    run_checked([git, "clone", str(remote), str(repo)], cwd=tmp_path)
    run_checked([git, "config", "user.email", "test@example.com"], cwd=repo)
    run_checked([git, "config", "user.name", "Test Bot"], cwd=repo)
    (repo / "data" / "history.json").write_text('{"snapshots": ["nas-stale"]}\n', encoding="utf-8")
    (repo / "dashboard" / "output" / "index.html").write_text("nas-stale\n", encoding="utf-8")
    run_checked([git, "add", "data/history.json", "dashboard/output/index.html"], cwd=repo)
    run_checked([git, "commit", "-m", "chore: update dashboard from NAS stale"], cwd=repo)

    (upstream / "data" / "history.json").write_text('{"snapshots": ["remote-ui"]}\n', encoding="utf-8")
    (upstream / "dashboard" / "output" / "index.html").write_text("remote-ui\n", encoding="utf-8")
    run_checked([git, "add", "data/history.json", "dashboard/output/index.html"], cwd=upstream)
    run_checked([git, "commit", "-m", "feat: remote UI update"], cwd=upstream)
    run_checked([git, "push", "origin", "main"], cwd=upstream)

    env = os.environ.copy()
    env.update(
        {
            "DASHBOARD_REPO_DIR": str(repo),
            "DASHBOARD_ENV_FILE": str(tmp_path / "missing.env"),
            "DASHBOARD_UPDATE_LOG": str(tmp_path / "nas-update.log"),
            "DASHBOARD_CLOUD_LOCK_DIR": str(tmp_path / "nas-cloud-update.lock"),
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
    assert "Skipping stale generated-only NAS commit" in log_text
    assert "backup/nas-generated-conflict-" in log_text
    assert run_checked([git, "rev-list", "--left-right", "--count", "main...origin/main"], cwd=repo).stdout.strip() == "0\t0"
    assert (repo / "data" / "history.json").read_text(encoding="utf-8") == '{"snapshots": ["remote-ui"]}\n'
    backup_refs = run_checked([git, "for-each-ref", "--format=%(refname:short)", "refs/heads/backup/"], cwd=repo).stdout
    assert "backup/nas-generated-conflict-" in backup_refs


def test_write_nas_status_creates_public_heartbeat(tmp_path: Path) -> None:
    python = require_tool("python3")
    repo = tmp_path / "repo"
    repo.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "DASHBOARD_REPO_DIR": str(repo),
            "DASHBOARD_NAS_STATUS_PATH": "data/nas_status.json",
            "DASHBOARD_NAS_RUNNER_ID": "ugreen-nas",
        }
    )

    result = subprocess.run(
        [
            python,
            str(REPO_ROOT / "scripts" / "write_nas_status.py"),
            "--mode",
            "cache",
            "--dashboard-exit-code",
            "0",
            "--comment-fetch-status",
            "skipped",
            "--comment-render-status",
            "skipped",
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads((repo / "data" / "nas_status.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["runner_id"] == "ugreen-nas"
    assert payload["mode"] == "cache"
    assert payload["dashboard_status"] == "success"
    assert payload["comment_fetch_status"] == "skipped"
    assert payload["status_path"] == "data/nas_status.json"
    assert "repo_dir" not in payload
    assert "cookie" not in json.dumps(payload).lower()


def test_write_nas_status_rejects_paths_outside_repo(tmp_path: Path) -> None:
    python = require_tool("python3")
    repo = tmp_path / "repo"
    repo.mkdir()
    outside_path = tmp_path / "outside" / "nas_status.json"
    env = os.environ.copy()
    env.update(
        {
            "DASHBOARD_REPO_DIR": str(repo),
            "DASHBOARD_NAS_STATUS_PATH": str(outside_path),
        }
    )

    result = subprocess.run(
        [python, str(REPO_ROOT / "scripts" / "write_nas_status.py")],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "must stay inside the repository" in result.stderr
    assert not outside_path.exists()


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
