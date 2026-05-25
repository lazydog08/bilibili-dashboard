#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${DASHBOARD_REPO_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
SCHEDULE="${DASHBOARD_NAS_CRON_SCHEDULE:-*/30 * * * *}"
CRON_MODE="${DASHBOARD_NAS_CRON_MODE:-user}"
RUN_AS_USER="${DASHBOARD_NAS_RUN_AS_USER:-${DASHBOARD_NAS_CRON_USER:-}}"
SU_BIN="${DASHBOARD_NAS_SU_BIN:-/bin/su}"
DRY_RUN="${DASHBOARD_NAS_CRON_DRY_RUN:-0}"
MARKER_BEGIN="# BEGIN bilibili-dashboard NAS update"
MARKER_END="# END bilibili-dashboard NAS update"

quote_sh() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

build_update_command() {
  local repo_quoted
  repo_quoted="$(quote_sh "$REPO_DIR")"
  printf 'cd %s && DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH=1 ./scripts/nas_update_and_push_cloud.sh >/dev/null 2>&1' "$repo_quoted"
}

build_cron_command() {
  local update_command
  update_command="$(build_update_command)"

  case "$CRON_MODE" in
    user|"")
      printf '%s' "$update_command"
      ;;
    root-su|ugreen-root)
      if [[ -z "$RUN_AS_USER" ]]; then
        echo "DASHBOARD_NAS_RUN_AS_USER is required when DASHBOARD_NAS_CRON_MODE=$CRON_MODE." >&2
        exit 1
      fi
      printf '%s - %s -c %s' "$SU_BIN" "$(quote_sh "$RUN_AS_USER")" "$(quote_sh "$update_command")"
      ;;
    *)
      echo "Unsupported DASHBOARD_NAS_CRON_MODE: $CRON_MODE. Use user or root-su." >&2
      exit 1
      ;;
  esac
}

CRON_COMMAND="$(build_cron_command)"

if [[ "$DRY_RUN" == "1" ]]; then
  printf '%s\n' "$MARKER_BEGIN"
  printf '%s %s\n' "$SCHEDULE" "$CRON_COMMAND"
  printf '%s\n' "$MARKER_END"
  exit 0
fi

if [[ "$CRON_MODE" == "root-su" || "$CRON_MODE" == "ugreen-root" ]]; then
  if [[ "$(id -u)" != "0" ]]; then
    echo "DASHBOARD_NAS_CRON_MODE=$CRON_MODE must be installed from root crontab." >&2
    exit 1
  fi
fi

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

{
  printf '\n%s\n' "$MARKER_BEGIN"
  printf '%s %s\n' "$SCHEDULE" "$CRON_COMMAND"
  printf '%s\n' "$MARKER_END"
} >> "$tmp_file"

crontab "$tmp_file"
if [[ "$CRON_MODE" == "root-su" || "$CRON_MODE" == "ugreen-root" ]]; then
  echo "Installed root NAS dashboard update cron for: $REPO_DIR as $RUN_AS_USER"
else
  echo "Installed NAS dashboard update cron for: $REPO_DIR"
fi
