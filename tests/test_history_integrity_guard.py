from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.history_integrity_guard import HistoryIntegrityError, verify_history, write_manifest


def _history() -> dict[str, object]:
    snapshots = [
        {
            "date": f"2026-05-{day:02d}" if day <= 31 else f"2026-06-{day - 31:02d}",
            "videos": [{"title": f"retained-title-{day}"}],
        }
        for day in range(1, 61)
    ]
    platform_snapshots = [
        {
            "platform": ("bilibili", "douyin", "xiaohongshu")[index % 3],
            "capturedAt": f"2026-06-{(index % 30) + 1:02d}T{index % 24:02d}:00:00+08:00",
        }
        for index in range(1200)
    ]
    fetch_logs = [
        {
            "platform": ("bilibili", "douyin", "xiaohongshu")[index % 3],
            "capturedAt": f"2026-06-{(index % 30) + 1:02d}T{index % 24:02d}:00:00+08:00",
            "status": "success",
        }
        for index in range(900)
    ]
    return {
        "schema_version": 1,
        "last_updated": "2026-06-29T23:00:00+08:00",
        "snapshots": snapshots,
        "platform_snapshots": platform_snapshots,
        "fetch_logs": fetch_logs,
    }


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_history_integrity_guard_accepts_normal_retention_and_growth(tmp_path: Path) -> None:
    baseline = tmp_path / "history.json"
    manifest = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    payload = _history()
    _write(baseline, payload)
    write_manifest(baseline, manifest)

    updated = copy.deepcopy(payload)
    updated["snapshots"] = updated["snapshots"][1:]
    updated["snapshots"].append(
        {"date": "2026-06-30", "videos": [{"title": "new-title"}]}
    )
    updated["platform_snapshots"] = updated["platform_snapshots"][60:]
    updated["platform_snapshots"].extend(copy.deepcopy(updated["platform_snapshots"][-20:]))
    updated["fetch_logs"] = updated["fetch_logs"][100:]
    updated["fetch_logs"].extend(copy.deepcopy(updated["fetch_logs"][-20:]))
    _write(candidate, updated)

    assert verify_history(manifest, candidate) == []


def test_history_integrity_guard_rejects_collapsed_history(tmp_path: Path) -> None:
    baseline = tmp_path / "history.json"
    manifest = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    _write(baseline, _history())
    write_manifest(baseline, manifest)
    _write(
        candidate,
        {
            "snapshots": [{"date": "2026-06-30", "videos": []}],
            "platform_snapshots": [],
            "fetch_logs": [],
        },
    )

    errors = verify_history(manifest, candidate)

    assert any("history size regressed" in error for error in errors)
    assert any("snapshots regressed" in error for error in errors)
    assert any("platform_snapshots regressed" in error for error in errors)
    assert any("fetch_logs regressed" in error for error in errors)
    assert any("historical snapshot dates disappeared" in error for error in errors)


def test_history_integrity_guard_rejects_disappearing_historical_title(tmp_path: Path) -> None:
    baseline = tmp_path / "history.json"
    manifest = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    payload = _history()
    _write(baseline, payload)
    captured = write_manifest(baseline, manifest)
    marker = captured["title_markers"][0]

    changed = copy.deepcopy(payload)
    for snapshot in changed["snapshots"]:
        if snapshot["date"] == marker["date"]:
            snapshot["videos"][0]["title"] = "replacement-title"
    _write(candidate, changed)

    errors = verify_history(manifest, candidate)

    assert errors == [
        f"historical title marker disappeared for {marker['date']}: {marker['title']}"
    ]


def test_history_integrity_guard_rejects_non_list_history_field(tmp_path: Path) -> None:
    baseline = tmp_path / "history.json"
    manifest = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    _write(baseline, _history())
    write_manifest(baseline, manifest)
    candidate.write_text('{"snapshots": {}, "platform_snapshots": [], "fetch_logs": []}\n', encoding="utf-8")

    with pytest.raises(HistoryIntegrityError, match="snapshots"):
        verify_history(manifest, candidate)
