#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RUNTIME_ROOT="${DASHBOARD_MAC_RUNTIME_ROOT:-$HOME/Library/Application Support/CreatorDataDashboard}"
CONFIG_FILE="$RUNTIME_ROOT/dashboard.env"
NAS_MOUNT_PATH="${DASHBOARD_NAS_MOUNT_PATH:-/Volumes/personal_folder}"
NAS_MOUNT_URL="${DASHBOARD_NAS_MOUNT_URL:-smb://192.168.31.67/personal_folder}"
NAS_CONFIG_FILE="${DASHBOARD_NAS_CONFIG_FILE:-$NAS_MOUNT_PATH/.config/bilibili-dashboard/dashboard.env}"
PLIST_SOURCE="$SOURCE_DIR/launchd/com.lazydog.creator-data-dashboard.collector.plist"
PLIST_TARGET="$HOME/Library/LaunchAgents/com.lazydog.creator-data-dashboard.collector.plist"
LABEL="com.lazydog.creator-data-dashboard.collector"

if [[ "$(uname -s)" != "Darwin" ]]; then
  printf 'This installer only supports macOS.\n' >&2
  exit 2
fi

if ! mount | grep -Fq " on $NAS_MOUNT_PATH "; then
  osascript -e "mount volume \"$NAS_MOUNT_URL\""
fi
mount | grep -Fq " on $NAS_MOUNT_PATH " || { printf 'NAS mount failed.\n' >&2; exit 3; }

if [[ "${DASHBOARD_MAC_INSTALL_DRY_RUN:-0}" == "1" ]]; then
  printf 'runtime=%s\nplist=%s\nconfig_source_present=%s\n' \
    "$RUNTIME_ROOT" "$PLIST_TARGET" "$([[ -f "$NAS_CONFIG_FILE" ]] && echo yes || echo no)"
  exit 0
fi

mkdir -p "$RUNTIME_ROOT" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/CreatorDataDashboard"
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.reviews/' \
  --exclude 'dashboard.env' \
  --exclude 'data/secrets/' \
  --exclude 'data/private/' \
  --exclude 'data/logs/' \
  "$SOURCE_DIR/" "$RUNTIME_ROOT/"

git -C "$SOURCE_DIR" rev-parse HEAD > "$RUNTIME_ROOT/.source-version"

if [[ ! -f "$CONFIG_FILE" ]]; then
  [[ -f "$NAS_CONFIG_FILE" ]] || { printf 'NAS collector config is missing.\n' >&2; exit 4; }
  cp "$NAS_CONFIG_FILE" "$CONFIG_FILE"
fi
chmod 600 "$CONFIG_FILE"

cp "$PLIST_SOURCE" "$PLIST_TARGET"
chmod 644 "$PLIST_TARGET"
plutil -lint "$PLIST_TARGET"

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart "gui/$(id -u)/$LABEL"
launchctl print "gui/$(id -u)/$LABEL"
