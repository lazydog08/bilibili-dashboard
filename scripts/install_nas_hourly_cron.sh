#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${DASHBOARD_REPO_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
SCHEDULE="${DASHBOARD_NAS_CRON_SCHEDULE:-*/30 * * * *}"
MARKER_BEGIN="# BEGIN bilibili-dashboard NAS update"
MARKER_END="# END bilibili-dashboard NAS update"

quote_sh() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

if ! command -v crontab >/dev/null 2>&1; then
  echo "crontab is not available on this NAS account." >&2
  exit 1
fi

tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

if crontab -l >/dev/null 2>&1; then
  crontab -l | awk -v begin="$MARKER_BEGIN" -v end="$MARKER_END" '
    $0 == begin { skip = 1; next }
    $0 == end { skip = 0; next }
    skip != 1 { print }
  ' > "$tmp_file"
fi

repo_quoted="$(quote_sh "$REPO_DIR")"
{
  printf '\n%s\n' "$MARKER_BEGIN"
  printf '%s cd %s && DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH=1 ./scripts/nas_update_and_push_cloud.sh >/dev/null 2>&1\n' "$SCHEDULE" "$repo_quoted"
  printf '%s\n' "$MARKER_END"
} >> "$tmp_file"

crontab "$tmp_file"
echo "Installed NAS dashboard update cron for: $REPO_DIR"
