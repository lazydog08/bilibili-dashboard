from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.refresh_xhs_creator_notes import (
    build_opencli_command,
    build_opencli_check_command,
    build_opencli_doctor_command,
    capture_creator_notes,
    check_prerequisites,
    refresh_manual_payload,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_build_opencli_command_uses_json_output_and_limit() -> None:
    assert build_opencli_command("opencli", 50) == [
        "opencli",
        "xiaohongshu",
        "creator-notes",
        "--limit",
        "50",
        "-f",
        "json",
    ]


def test_check_prerequisites_passes_when_opencli_and_chrome_are_ready() -> None:
    def fake_runner(args, **kwargs):  # noqa: ANN001
        if args == ["opencli", "doctor"]:
            return subprocess.CompletedProcess(args, 0, stdout="[OK] Extension: connected\n[OK] Connectivity: ok\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    results = check_prerequisites(opencli_cmd="opencli", runner=fake_runner, system_name="Darwin")

    assert [item["name"] for item in results] == ["opencli", "Browser Bridge", "Chrome"]
    assert all(item["ok"] for item in results)
    assert build_opencli_check_command("opencli") == ["opencli", "--version"]
    assert build_opencli_doctor_command("opencli") == ["opencli", "doctor"]


def test_check_prerequisites_reports_chrome_not_running() -> None:
    def fake_runner(args, **kwargs):  # noqa: ANN001
        if args == ["opencli", "doctor"]:
            return subprocess.CompletedProcess(args, 0, stdout="[OK] Extension: connected\n[OK] Connectivity: ok\n", stderr="")
        if args[:2] == ["pgrep", "-x"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    results = check_prerequisites(opencli_cmd="opencli", runner=fake_runner, system_name="Darwin")

    chrome = next(item for item in results if item["name"] == "Chrome")
    assert chrome["ok"] is False
    assert "未运行" in chrome["message"]


def test_check_prerequisites_reports_browser_bridge_not_connected() -> None:
    def fake_runner(args, **kwargs):  # noqa: ANN001
        if args == ["opencli", "doctor"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="[MISSING] Extension: not connected\n[FAIL] Connectivity: failed\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    results = check_prerequisites(opencli_cmd="opencli", runner=fake_runner, system_name="Darwin")

    bridge = next(item for item in results if item["name"] == "Browser Bridge")
    assert bridge["ok"] is False
    assert "Browser Bridge" in bridge["message"]


def test_capture_creator_notes_parses_opencli_stdout() -> None:
    calls: list[list[str]] = []

    def fake_runner(args, **kwargs):  # noqa: ANN001
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(
                {
                    "rows": [
                        {
                            "title": "清闲pro到底好不好？给大家踩踩坑",
                            "date": "2026年05月27日 19:50",
                            "views": "11822",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            stderr="",
        )

    payload = capture_creator_notes(opencli_cmd="opencli", limit=1, runner=fake_runner)

    assert calls[0] == build_opencli_command("opencli", 1)
    assert payload["rows"][0]["title"] == "清闲pro到底好不好？给大家踩踩坑"


def test_refresh_manual_payload_imports_captured_creator_rows() -> None:
    payload = {
        "platforms": {
            "xiaohongshu": {
                "source": "manual_import",
                "contentItems": [{"title": "旧缓存"}],
            }
        }
    }
    captured = {
        "rows": [
            {
                "title": "战争制裁下的俄罗斯，人们过着怎样的生活？",
                "date": "2026年04月22日 20:17",
                "views": "120001",
                "likes": "2600",
                "collects": "970",
                "comments": "58",
            }
        ]
    }

    updated, count = refresh_manual_payload(
        payload,
        captured,
        imported_at="2026-06-02T13:30:00+08:00",
        captured_at="2026-06-02T13:29:00+08:00",
    )

    assert count == 1
    xhs_items = updated["platforms"]["xiaohongshu"]["contentItems"]
    assert xhs_items[0]["title"] == "战争制裁下的俄罗斯，人们过着怎样的生活？"
    assert xhs_items[0]["views"] == 120001


def test_refresh_script_runs_from_repo_root_without_pythonpath(tmp_path: Path) -> None:
    manual_path = tmp_path / "manual_platform_metrics.json"
    input_path = tmp_path / "xhs-notes.json"
    manual_path.write_text(
        json.dumps({"platforms": {"xiaohongshu": {"source": "manual_import", "contentItems": []}}}),
        encoding="utf-8",
    )
    input_path.write_text(
        json.dumps(
            [
                {
                    "title": "清闲pro到底好不好？给大家踩踩坑",
                    "date": "2026年05月27日 19:50",
                    "views": "11822",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/refresh_xhs_creator_notes.py",
            "--input",
            str(input_path),
            "--manual-path",
            str(manual_path),
            "--skip-render",
            "--imported-at",
            "2026-06-02T13:40:00+08:00",
        ],
        cwd=REPO_ROOT,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    updated = json.loads(manual_path.read_text(encoding="utf-8"))
    assert updated["platforms"]["xiaohongshu"]["contentItems"][0]["views"] == 11822
