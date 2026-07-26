#!/usr/bin/env python3
"""Capture and verify dashboard history before any generated data is published."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA_VERSION = 1
COUNT_FIELDS = ("snapshots", "platform_snapshots", "fetch_logs")


class HistoryIntegrityError(RuntimeError):
    """Raised when history or a guard manifest is invalid."""


def _load_json_object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HistoryIntegrityError(f"{label} cannot be read: {path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HistoryIntegrityError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HistoryIntegrityError(f"{label} must be a JSON object: {path}")
    return payload, raw


def _list_field(payload: dict[str, Any], field: str) -> list[Any]:
    value = payload.get(field, [])
    if not isinstance(value, list):
        raise HistoryIntegrityError(f"history field {field!r} must be a list")
    return value


def _snapshot_dates(payload: dict[str, Any]) -> list[str]:
    dates = {
        str(item.get("date")).strip()
        for item in _list_field(payload, "snapshots")
        if isinstance(item, dict) and str(item.get("date") or "").strip()
    }
    return sorted(dates)


def _snapshot_titles(snapshot: dict[str, Any]) -> list[str]:
    videos = snapshot.get("videos", [])
    if not isinstance(videos, list):
        return []
    titles: list[str] = []
    for video in videos:
        if not isinstance(video, dict):
            continue
        title = str(video.get("title") or "").strip()
        if title:
            titles.append(title)
    return titles


def _title_markers(payload: dict[str, Any], limit: int = 5) -> list[dict[str, str]]:
    dated_titles: list[tuple[str, str]] = []
    for snapshot in _list_field(payload, "snapshots"):
        if not isinstance(snapshot, dict):
            continue
        date = str(snapshot.get("date") or "").strip()
        titles = _snapshot_titles(snapshot)
        if date and titles:
            dated_titles.append((date, titles[0]))
    dated_titles.sort()
    if not dated_titles:
        return []

    # Avoid the oldest two retention-boundary dates and the newest mutable date.
    stable = dated_titles[2:-1] if len(dated_titles) > 4 else dated_titles
    if not stable:
        stable = dated_titles
    positions = sorted({round(index * (len(stable) - 1) / max(1, limit - 1)) for index in range(limit)})
    markers: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for position in positions:
        date, title = stable[position]
        key = (date, title)
        if key in seen:
            continue
        seen.add(key)
        markers.append({"date": date, "title": title})
    return markers


def _serialize_history(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def build_manifest_for_payload(payload: dict[str, Any], raw: bytes | None = None) -> dict[str, Any]:
    serialized = raw if raw is not None else _serialize_history(payload)
    counts = {field: len(_list_field(payload, field)) for field in COUNT_FIELDS}
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "history_sha256": hashlib.sha256(serialized).hexdigest(),
        "size_bytes": len(serialized),
        "counts": counts,
        "snapshot_dates": _snapshot_dates(payload),
        "title_markers": _title_markers(payload),
    }


def build_manifest(history_path: Path) -> dict[str, Any]:
    payload, raw = _load_json_object(history_path, "history")
    return build_manifest_for_payload(payload, raw)


def write_manifest(history_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = build_manifest(history_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return manifest


def _load_manifest(path: Path) -> dict[str, Any]:
    manifest, _ = _load_json_object(path, "history guard manifest")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise HistoryIntegrityError(
            f"unsupported history guard manifest schema: {manifest.get('schema_version')!r}"
        )
    if not isinstance(manifest.get("counts"), dict):
        raise HistoryIntegrityError("history guard manifest is missing counts")
    return manifest


def _minimum_count(field: str, baseline: int) -> int:
    if field == "snapshots":
        return max(0, baseline - 2)
    if field == "platform_snapshots":
        return max(0, baseline - max(100, math.ceil(baseline * 0.10)))
    if field == "fetch_logs":
        return max(0, baseline - max(200, math.ceil(baseline * 0.15)))
    raise AssertionError(f"unknown count field: {field}")


def verify_history_payload(
    manifest: dict[str, Any],
    candidate: dict[str, Any],
    raw: bytes | None = None,
) -> list[str]:
    serialized = raw if raw is not None else _serialize_history(candidate)
    errors: list[str] = []

    baseline_size = int(manifest.get("size_bytes") or 0)
    minimum_size = math.floor(baseline_size * 0.75)
    if len(serialized) < minimum_size:
        errors.append(
            f"history size regressed from {baseline_size} to {len(serialized)} bytes "
            f"(minimum {minimum_size})"
        )

    baseline_counts = manifest["counts"]
    for field in COUNT_FIELDS:
        baseline = int(baseline_counts.get(field) or 0)
        candidate_count = len(_list_field(candidate, field))
        minimum = _minimum_count(field, baseline)
        if candidate_count < minimum:
            errors.append(
                f"{field} regressed from {baseline} to {candidate_count} "
                f"(minimum {minimum})"
            )

    baseline_dates = [str(value) for value in manifest.get("snapshot_dates", []) if str(value)]
    candidate_dates = set(_snapshot_dates(candidate))
    missing_dates = [value for value in baseline_dates if value not in candidate_dates]
    allowed_missing_dates = set(baseline_dates[:2])
    disallowed_missing_dates = [value for value in missing_dates if value not in allowed_missing_dates]
    if len(missing_dates) > 2 or disallowed_missing_dates:
        errors.append(
            "historical snapshot dates disappeared: "
            + ", ".join(disallowed_missing_dates or missing_dates[:10])
        )

    candidate_titles_by_date: dict[str, set[str]] = {}
    for snapshot in _list_field(candidate, "snapshots"):
        if not isinstance(snapshot, dict):
            continue
        date = str(snapshot.get("date") or "").strip()
        if date:
            candidate_titles_by_date.setdefault(date, set()).update(_snapshot_titles(snapshot))
    for marker in manifest.get("title_markers", []):
        if not isinstance(marker, dict):
            continue
        date = str(marker.get("date") or "")
        title = str(marker.get("title") or "")
        if date and title and title not in candidate_titles_by_date.get(date, set()):
            errors.append(f"historical title marker disappeared for {date}: {title}")

    return errors


def verify_history(manifest_path: Path, candidate_path: Path) -> list[str]:
    manifest = _load_manifest(manifest_path)
    candidate, raw = _load_json_object(candidate_path, "candidate history")
    return verify_history_payload(manifest, candidate, raw)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="Capture a baseline manifest.")
    capture.add_argument("history", type=Path)
    capture.add_argument("--output", required=True, type=Path)

    verify = subparsers.add_parser("verify", help="Verify a candidate against a baseline manifest.")
    verify.add_argument("--baseline", required=True, type=Path)
    verify.add_argument("--candidate", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "capture":
            manifest = write_manifest(args.history, args.output)
            counts = manifest["counts"]
            print(
                "history baseline captured: "
                f"bytes={manifest['size_bytes']} "
                f"snapshots={counts['snapshots']} "
                f"platform_snapshots={counts['platform_snapshots']} "
                f"fetch_logs={counts['fetch_logs']}"
            )
            return 0

        errors = verify_history(args.baseline, args.candidate)
        if errors:
            for error in errors:
                print(f"history integrity failed: {error}", file=sys.stderr)
            return 2
        print("history integrity verified")
        return 0
    except (HistoryIntegrityError, OSError, ValueError) as exc:
        print(f"history integrity failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
