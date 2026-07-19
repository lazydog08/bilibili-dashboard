from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from scripts.noon_watchdog import assess_freshness, build_bark_message


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


def test_cloud_update_script_can_publish_failure_heartbeat_after_refresh_failure() -> None:
    script = (REPO_ROOT / "scripts" / "nas_update_and_push_cloud.sh").read_text(encoding="utf-8")

    assert "DASHBOARD_REFRESH_STATUS" in script
    assert "Dashboard refresh failed" in script
    assert 'exit "$DASHBOARD_REFRESH_STATUS"' in script


def test_cloud_update_script_recovers_stale_lock_dir(tmp_path: Path) -> None:
    bash = require_tool("bash")
    git = require_tool("git")
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    lock_dir = tmp_path / "stale.lock"
    run_checked([git, "init", "--bare", "--initial-branch=main", str(remote)], cwd=tmp_path)
    run_checked([git, "init", "--initial-branch=main", str(repo)], cwd=tmp_path)
    (repo / "data").mkdir()
    (repo / "dashboard" / "output").mkdir(parents=True)
    (repo / "data" / "history.json").write_text('{"snapshots": []}\n', encoding="utf-8")
    (repo / "dashboard" / "output" / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    run_checked([git, "config", "user.email", "test@example.com"], cwd=repo)
    run_checked([git, "config", "user.name", "Test Bot"], cwd=repo)
    run_checked([git, "remote", "add", "origin", str(remote)], cwd=repo)
    run_checked([git, "add", "data/history.json", "dashboard/output/index.html"], cwd=repo)
    run_checked([git, "commit", "-m", "initial"], cwd=repo)
    run_checked([git, "push", "-u", "origin", "main"], cwd=repo)

    lock_dir.mkdir()
    (lock_dir / "pid").write_text("999999\n", encoding="utf-8")
    os.utime(lock_dir, (1, 1))
    env = os.environ.copy()
    env.update(
        {
            "DASHBOARD_REPO_DIR": str(repo),
            "DASHBOARD_ENV_FILE": str(tmp_path / "missing.env"),
            "DASHBOARD_UPDATE_LOG": str(tmp_path / "nas-update.log"),
            "DASHBOARD_CLOUD_LOCK_DIR": str(lock_dir),
            "DASHBOARD_LOCK_MAX_AGE_SECONDS": "1",
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
    assert "Removing stale NAS cloud update lock" in log_text
    assert not lock_dir.exists()


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
            "DASHBOARD_REQUIRED_FRESH_PLATFORMS": "bilibili,douyin",
            "DASHBOARD_SOURCE_VERSION": "test-version",
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
    assert payload["source_version"] == "test-version"
    assert payload["mode"] == "cache"
    assert payload["dashboard_status"] == "failed"
    assert payload["data_quality_status"] == "failed"
    assert payload["required_stale_platforms"] == ["bilibili", "douyin"]
    assert payload["comment_fetch_status"] == "skipped"
    assert payload["status_path"] == "data/nas_status.json"
    assert "repo_dir" not in payload
    assert "cookie" not in json.dumps(payload).lower()


def test_write_nas_status_accepts_fresh_required_platforms_but_reports_optional_degradation(tmp_path: Path) -> None:
    python = require_tool("python3")
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    history = {
        "platform_snapshots": [
            {
                "platform": "bilibili",
                "capturedAt": captured_at,
                "timezone": "Asia/Shanghai",
                "sourceStatus": {"status": "partial", "source": "bilibili_public_fallback"},
            },
            {
                "platform": "douyin",
                "capturedAt": captured_at,
                "timezone": "Asia/Shanghai",
                "sourceStatus": {"status": "success", "source": "authorized_cookie"},
            },
        ]
    }
    (repo / "data" / "history.json").write_text(json.dumps(history), encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "DASHBOARD_REPO_DIR": str(repo),
            "DASHBOARD_REQUIRED_FRESH_PLATFORMS": "bilibili,douyin",
        }
    )

    result = subprocess.run(
        [python, str(REPO_ROOT / "scripts" / "write_nas_status.py"), "--dashboard-exit-code", "0"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads((repo / "data" / "nas_status.json").read_text(encoding="utf-8"))
    assert payload["dashboard_status"] == "degraded"
    assert payload["data_quality_status"] == "degraded"
    assert payload["required_stale_platforms"] == []
    assert payload["platform_freshness"]["bilibili"]["fresh"] is True
    assert payload["platform_freshness"]["douyin"]["fresh"] is True
    assert payload["platform_freshness"]["xiaohongshu"]["fresh"] is False


def test_write_nas_status_does_not_treat_manual_snapshot_as_fresh_required_network_data(tmp_path: Path) -> None:
    python = require_tool("python3")
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    history = {
        "platform_snapshots": [
            {
                "platform": "douyin",
                "capturedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                "timezone": "Asia/Shanghai",
                "sourceStatus": {"status": "manual", "source": "manual_import"},
            }
        ]
    }
    (repo / "data" / "history.json").write_text(json.dumps(history), encoding="utf-8")
    env = os.environ.copy()
    env.update({"DASHBOARD_REPO_DIR": str(repo), "DASHBOARD_REQUIRED_FRESH_PLATFORMS": "douyin"})

    result = subprocess.run(
        [python, str(REPO_ROOT / "scripts" / "write_nas_status.py"), "--dashboard-exit-code", "0"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads((repo / "data" / "nas_status.json").read_text(encoding="utf-8"))
    assert payload["dashboard_status"] == "failed"
    assert payload["required_stale_platforms"] == ["douyin"]
    assert payload["platform_freshness"]["douyin"]["fresh"] is False


def test_daily_fetch_workflow_has_scheduled_cloud_fallback() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "daily_fetch.yml").read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "cron: '12,42 * * * *'" in workflow
    assert "DASHBOARD_CLOUD_STALE_MINUTES" in workflow
    assert "id: freshness" in workflow
    assert workflow.count("steps.freshness.outputs.should_refresh == 'true'") >= 7
    assert "workflow_dispatch:" in workflow


def test_check_dashboard_freshness_skips_recent_history(tmp_path: Path) -> None:
    python = require_tool("python3")
    repo = tmp_path / "repo"
    repo.mkdir()
    history = repo / "history.json"
    output = tmp_path / "github-output.txt"
    history.write_text('{"last_updated": "2026-05-31T15:23:14+08:00"}\n', encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_OUTPUT": str(output),
            "DASHBOARD_FRESHNESS_NOW": "2026-05-31T15:55:00+08:00",
        }
    )

    result = subprocess.run(
        [
            python,
            str(REPO_ROOT / "scripts" / "check_dashboard_freshness.py"),
            "--history-path",
            str(history),
            "--stale-minutes",
            "60",
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "should_refresh=false" in output.read_text(encoding="utf-8")
    assert "fresh" in result.stdout


def test_check_dashboard_freshness_uses_env_default_and_refreshes_missing_history(tmp_path: Path) -> None:
    python = require_tool("python3")
    output = tmp_path / "github-output.txt"
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_OUTPUT": str(output),
            "DASHBOARD_CLOUD_STALE_MINUTES": "60",
        }
    )

    result = subprocess.run(
        [python, str(REPO_ROOT / "scripts" / "check_dashboard_freshness.py")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    output_text = output.read_text(encoding="utf-8")
    assert "should_refresh=true" in output_text
    assert "reason=missing_history" in output_text
    assert "missing_history" in result.stdout


def test_check_dashboard_freshness_refreshes_stale_history(tmp_path: Path) -> None:
    python = require_tool("python3")
    repo = tmp_path / "repo"
    repo.mkdir()
    history = repo / "history.json"
    output = tmp_path / "github-output.txt"
    history.write_text('{"last_updated": "2026-05-31T14:20:00+08:00"}\n', encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_OUTPUT": str(output),
            "DASHBOARD_FRESHNESS_NOW": "2026-05-31T15:55:00+08:00",
        }
    )

    result = subprocess.run(
        [
            python,
            str(REPO_ROOT / "scripts" / "check_dashboard_freshness.py"),
            "--history-path",
            str(history),
            "--stale-minutes",
            "60",
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    output_text = output.read_text(encoding="utf-8")
    assert "should_refresh=true" in output_text
    assert "age_minutes=95" in output_text
    assert "stale" in result.stdout


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
    assert "cd '\\''/home/小黑/bilibili-dashboard'\\'' && mkdir -p data/logs && DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH=1" in result.stdout
    assert "data/logs/cron-update.log" in result.stdout
    assert ">/dev/null 2>&1" not in result.stdout
    assert "# END bilibili-dashboard NAS update" in result.stdout


def test_nas_update_script_fetches_comments_before_publish() -> None:
    script = (REPO_ROOT / "scripts" / "nas_update_dashboard.sh").read_text(encoding="utf-8")

    assert "ENABLE_COMMENT_INSIGHTS" in script
    assert "scripts/fetch_bilibili_comments.py" in script
    assert (REPO_ROOT / "scripts" / "fetch_bilibili_comments.py").exists()
    assert '"$REPO_DIR/main.py" "--cache" "--no-feishu" "--no-bark"' in script
    assert '"--cache" "--no-feishu" "--no-bark"' in script


def test_nas_update_script_refreshes_xhs_creator_notes_before_main_render() -> None:
    script = (REPO_ROOT / "scripts" / "nas_update_dashboard.sh").read_text(encoding="utf-8")

    assert "XHS_CREATOR_NOTES_REFRESH_ENABLED" in script
    assert 'XHS_CREATOR_NOTES_REQUIRED:-0' in script
    assert "scripts/refresh_xhs_creator_notes.py" in script
    assert '"--check" "--opencli-cmd" "$opencli_cmd"' in script
    assert "Skipping Xiaohongshu creator notes refresh because Browser Bridge/Chrome is not ready." in script
    assert '--xhs-creator-notes-status "$XHS_CREATOR_NOTES_STATUS"' in script
    assert script.index("run_xhs_creator_notes_refresh") < script.index('CMD=("$PYTHON_BIN" "main.py")')


def test_write_nas_status_records_xhs_creator_notes_status(tmp_path: Path) -> None:
    python = require_tool("python3")
    repo = tmp_path / "repo"
    repo.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "DASHBOARD_REPO_DIR": str(repo),
            "DASHBOARD_NAS_STATUS_PATH": "data/nas_status.json",
        }
    )

    result = subprocess.run(
        [
            python,
            str(REPO_ROOT / "scripts" / "write_nas_status.py"),
            "--xhs-creator-notes-status",
            "failed",
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads((repo / "data" / "nas_status.json").read_text(encoding="utf-8"))
    assert payload["xhs_creator_notes_status"] == "failed"


def test_noon_watchdog_marks_recent_page_and_heartbeat_as_normal() -> None:
    page_html = '<div data-dashboard-updated="2026-05-31T11:30:00+08:00">最后更新</div>'
    nas_status = {"last_run_at": "2026-05-31T03:30:00+00:00"}

    result = assess_freshness(
        page_html=page_html,
        nas_status=nas_status,
        now=datetime.fromisoformat("2026-05-31T12:00:00+08:00"),
        max_age_minutes=90,
    )

    assert result.ok is True
    assert result.page_age_minutes == 30
    assert result.heartbeat_age_minutes == 30
    assert result.reasons == []


def test_noon_watchdog_marks_stale_page_as_abnormal() -> None:
    page_html = '<div data-dashboard-updated="2026-05-31T09:59:00+08:00">最后更新</div>'
    nas_status = {"last_run_at": "2026-05-31T03:45:00+00:00"}

    result = assess_freshness(
        page_html=page_html,
        nas_status=nas_status,
        now=datetime.fromisoformat("2026-05-31T12:00:00+08:00"),
        max_age_minutes=90,
    )

    assert result.ok is False
    assert result.page_age_minutes == 121
    assert "page_stale" in result.reasons


def test_noon_watchdog_marks_failed_xhs_creator_note_refresh_as_abnormal() -> None:
    page_html = '<div data-dashboard-updated="2026-05-31T11:45:00+08:00">最后更新</div>'
    nas_status = {
        "last_run_at": "2026-05-31T03:45:00+00:00",
        "xhs_creator_notes_status": "failed",
    }

    result = assess_freshness(
        page_html=page_html,
        nas_status=nas_status,
        now=datetime.fromisoformat("2026-05-31T12:00:00+08:00"),
        max_age_minutes=90,
        xhs_creator_notes_required=True,
    )

    assert result.ok is False
    assert "xhs_creator_notes_failed" in result.reasons


def test_noon_watchdog_allows_failed_optional_xhs_creator_note_refresh() -> None:
    page_html = '<div data-dashboard-updated="2026-05-31T11:45:00+08:00">最后更新</div>'
    nas_status = {
        "last_run_at": "2026-05-31T03:45:00+00:00",
        "xhs_creator_notes_status": "failed",
    }

    result = assess_freshness(
        page_html=page_html,
        nas_status=nas_status,
        now=datetime.fromisoformat("2026-05-31T12:00:00+08:00"),
        max_age_minutes=90,
        xhs_creator_notes_required=False,
    )

    assert result.ok is True
    assert result.reasons == []


def test_noon_watchdog_rejects_fresh_heartbeat_with_stale_required_platform() -> None:
    page_html = '<div data-dashboard-updated="2026-05-31T11:45:00+08:00">最后更新</div>'
    nas_status = {
        "last_run_at": "2026-05-31T03:45:00+00:00",
        "dashboard_status": "failed",
        "data_quality_status": "failed",
        "required_stale_platforms": ["bilibili"],
    }

    result = assess_freshness(
        page_html=page_html,
        nas_status=nas_status,
        now=datetime.fromisoformat("2026-05-31T12:00:00+08:00"),
        max_age_minutes=90,
    )

    assert result.ok is False
    assert "dashboard_failed" in result.reasons
    assert "data_quality_failed" in result.reasons
    assert "required_platforms_stale" in result.reasons


def test_mac_mini_collector_has_owner_lock_atomic_sync_and_secret_free_plist() -> None:
    owner = (REPO_ROOT / ".collector-owner").read_text(encoding="utf-8").strip()
    nas_script = (REPO_ROOT / "scripts" / "nas_update_dashboard.sh").read_text(encoding="utf-8")
    mac_script = (REPO_ROOT / "scripts" / "mac_mini_update_and_sync.sh").read_text(encoding="utf-8")
    installer = (REPO_ROOT / "scripts" / "install_mac_mini_collector.sh").read_text(encoding="utf-8")
    plist = (REPO_ROOT / "launchd" / "com.lazydog.creator-data-dashboard.collector.plist").read_text(
        encoding="utf-8"
    )

    assert owner == "mac-mini"
    assert "Platform collection is delegated to Mac mini" in nas_script
    assert "mac-mini-collector.lock" in mac_script
    assert '.venv-mac' in mac_script
    assert '/opt/homebrew/bin/python3' in mac_script
    assert "import browser_cookie3, dateutil, httpx, jinja2" in mac_script
    assert 'MAC_PYTHON_BIN="$MAC_VENV_ROOT/bin/python"' in mac_script
    assert '"$PYTHON_BIN"' not in mac_script
    assert "atomic_copy" in mac_script
    assert 'cp -X "$source" "$temporary"' in mac_script
    assert 'cp -p "$source" "$temporary"' not in mac_script
    assert "data_quality_status" in mac_script
    assert "refresh_bilibili_browser_cookie.py" in mac_script
    assert "BILIBILI_BROWSER_COOKIE_REFRESH_ENABLED" in mac_script
    assert "BILIBILI_AUTH_ALERT_STAMP" in mac_script
    assert "86400" in mac_script
    assert '[[ "$refresh_exit" == "2" ]]' in mac_script
    assert "send_bilibili_refresh_error_bark" in mac_script
    assert mac_script.index("refresh_bilibili_browser_cookie") < mac_script.index(
        'log "Mac mini platform collection started."'
    )
    assert "mount_nas" not in mac_script
    assert "unset GIT_EXEC_PATH" in mac_script
    assert 'git push "$REMOTE_NAME" "HEAD:$BRANCH"' in mac_script
    assert "Refusing to publish unexpected staged path" in mac_script
    assert "launchctl bootstrap" in installer
    assert 'launchctl kickstart "gui/$(id -u)/$LABEL"' in installer
    assert "launchctl kickstart -k" not in installer
    assert "--exclude '.git'" in installer
    assert "--exclude 'dashboard.env'" in installer
    assert ".source-version" in installer
    assert "auth git-credential" in installer
    assert "core.filemode false" in installer
    assert "com.lazydog.creator-data-dashboard.collector" in plist
    assert "<key>StartCalendarInterval</key>" in plist
    assert "<key>Minute</key>" in plist
    assert "<integer>0</integer>" in plist
    assert "<key>StartInterval</key>" not in plist
    assert "BILIBILI_COOKIE" not in plist
    assert "BARK" not in plist


def test_noon_watchdog_bark_message_reports_normal_status() -> None:
    result = assess_freshness(
        page_html='<div data-dashboard-updated="2026-05-31T11:50:00+08:00">最后更新</div>',
        nas_status={"last_run_at": "2026-05-31T03:50:00+00:00"},
        now=datetime.fromisoformat("2026-05-31T12:00:00+08:00"),
        max_age_minutes=90,
    )

    title, body = build_bark_message("normal", result)

    assert title == "Bilibili 看板更新正常"
    assert "网页数据：2026-05-31T11:50:00+08:00" in body
    assert "NAS 心跳：2026-05-31T03:50:00+00:00" in body


def test_noon_watchdog_cron_installer_renders_root_su_dry_run(tmp_path: Path) -> None:
    bash = require_tool("bash")
    env = os.environ.copy()
    env.update(
        {
            "DASHBOARD_NAS_WATCHDOG_CRON_DRY_RUN": "1",
            "DASHBOARD_NAS_CRON_MODE": "root-su",
            "DASHBOARD_NAS_RUN_AS_USER": "小黑",
            "DASHBOARD_REPO_DIR": "/home/小黑/bilibili-dashboard",
        }
    )

    result = subprocess.run(
        [bash, str(REPO_ROOT / "scripts" / "install_nas_noon_watchdog_cron.sh")],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "# BEGIN bilibili-dashboard noon watchdog" in result.stdout
    assert "0 12 * * * /bin/su - '小黑' -c" in result.stdout
    assert "python3 scripts/noon_watchdog.py" in result.stdout
    assert "data/logs/noon-watchdog.log" in result.stdout
