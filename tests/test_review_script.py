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


def init_review_repo(tmp_path: Path) -> Path:
    git = require_tool("git")
    repo = tmp_path / "repo"
    repo.mkdir()
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir()
    review_script = scripts_dir / "review.sh"
    shutil.copyfile(REPO_ROOT / "scripts" / "review.sh", review_script)
    review_script.chmod(0o755)
    run_checked([git, "init"], cwd=repo)
    run_checked([git, "config", "user.email", "test@example.com"], cwd=repo)
    run_checked([git, "config", "user.name", "Test Bot"], cwd=repo)
    return repo


def write_mock_claude(tmp_path: Path) -> Path:
    mock_dir = tmp_path / "bin"
    mock_dir.mkdir()
    mock = mock_dir / "claude"
    mock.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                'printf "%s\\n" "$@" > "$CLAUDE_ARGS_LOG"',
                "cat >/dev/null",
                'printf "%s\\n" "No findings."',
            ]
        ),
        encoding="utf-8",
    )
    mock.chmod(0o755)
    return mock_dir


def test_review_script_blocks_credential_like_diff_before_claude(tmp_path: Path) -> None:
    git = require_tool("git")
    bash = require_tool("bash")
    repo = init_review_repo(tmp_path)
    mock_dir = write_mock_claude(tmp_path)
    args_log = tmp_path / "claude-args.log"
    (repo / "leak.txt").write_text("token=sk-" + "a" * 32 + "\n", encoding="utf-8")
    run_checked([git, "add", "leak.txt"], cwd=repo)
    env = os.environ.copy()
    env["PATH"] = f"{mock_dir}{os.pathsep}{env['PATH']}"
    env["CLAUDE_ARGS_LOG"] = str(args_log)

    result = subprocess.run(
        [bash, "scripts/review.sh", "staged"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "credential-like text" in result.stderr
    assert not args_log.exists()
    assert "stopped before sending anything to Claude" in (repo / ".reviews" / "latest.md").read_text(encoding="utf-8")


def test_review_script_uses_no_tools_by_default(tmp_path: Path) -> None:
    git = require_tool("git")
    bash = require_tool("bash")
    repo = init_review_repo(tmp_path)
    mock_dir = write_mock_claude(tmp_path)
    args_log = tmp_path / "claude-args.log"
    (repo / "safe.txt").write_text("layout fix\n", encoding="utf-8")
    run_checked([git, "add", "safe.txt"], cwd=repo)
    env = os.environ.copy()
    env["PATH"] = f"{mock_dir}{os.pathsep}{env['PATH']}"
    env["CLAUDE_ARGS_LOG"] = str(args_log)

    result = subprocess.run(
        [bash, "scripts/review.sh", "staged"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    args = args_log.read_text(encoding="utf-8").splitlines()
    assert "--tools" in args
    tools_index = args.index("--tools")
    assert args[tools_index + 1] == ""
    assert "--allowedTools" not in args
    assert "No findings." in (repo / ".reviews" / "latest.md").read_text(encoding="utf-8")
