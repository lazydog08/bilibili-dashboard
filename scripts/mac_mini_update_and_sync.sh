#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${DASHBOARD_REPO_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
RUNTIME_ROOT="${DASHBOARD_MAC_RUNTIME_ROOT:-$REPO_DIR}"
CONFIG_FILE="${DASHBOARD_ENV_FILE:-$RUNTIME_ROOT/dashboard.env}"
NAS_MOUNT_PATH="${DASHBOARD_NAS_MOUNT_PATH:-/Volumes/personal_folder}"
NAS_MOUNT_URL="${DASHBOARD_NAS_MOUNT_URL:-smb://192.168.31.67/personal_folder}"
NAS_REPO_DIR="${DASHBOARD_NAS_REPO_DIR:-$NAS_MOUNT_PATH/bilibili-dashboard}"
LOG_DIR="${DASHBOARD_MAC_LOG_DIR:-$HOME/Library/Logs/CreatorDataDashboard}"
LOG_FILE="$LOG_DIR/collector.log"
LOCK_DIR="$RUNTIME_ROOT/data/logs/mac-mini-collector.lock"
PYTHON_BIN="$RUNTIME_ROOT/.venv/bin/python"
FAILURE_NOTIFIED=0

mkdir -p "$LOG_DIR" "$RUNTIME_ROOT/data/logs"
touch "$LOG_FILE"

log() {
  printf '[%s] %s\n' "$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S %Z')" "$*" | tee -a "$LOG_FILE"
}

load_config() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    log "Collector configuration is missing: $CONFIG_FILE"
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  set +a
  export DASHBOARD_ENV_FILE="$CONFIG_FILE"
  export DASHBOARD_NAS_RUNNER_ID="mac-mini"
  export DASHBOARD_SOURCE_VERSION="$(cat "$RUNTIME_ROOT/.source-version" 2>/dev/null || true)"
  export DASHBOARD_REQUIRED_FRESH_PLATFORMS="${DASHBOARD_REQUIRED_FRESH_PLATFORMS:-bilibili,douyin}"
  export DASHBOARD_PLATFORM_STALE_MINUTES="${DASHBOARD_PLATFORM_STALE_MINUTES:-90}"
  export ENABLE_BILIBILI_FETCH="${ENABLE_BILIBILI_FETCH:-1}"
  export ENABLE_COMMENT_INSIGHTS="${ENABLE_COMMENT_INSIGHTS:-1}"
  export DISABLE_BARK=1
}

send_failure_bark() {
  local summary="$1"
  if [[ "$FAILURE_NOTIFIED" == "1" ]]; then
    return 0
  fi
  FAILURE_NOTIFIED=1
  local sender="python3"
  [[ -x "$PYTHON_BIN" ]] && sender="$PYTHON_BIN"
  DASHBOARD_ENV_FILE="$CONFIG_FILE" "$sender" -c \
    'from pathlib import Path; from scripts.noon_watchdog import load_env_files, send_bark; load_env_files(Path.cwd()); import sys; print(send_bark("Codex 项目结论", "【Mac mini】三平台数据采集失败：" + sys.argv[1] + "。上一版 NAS 数据已保留，需要小黑查看告警。", 20))' \
    "$summary" >> "$LOG_FILE" 2>&1 || true
}

on_error() {
  local exit_code="$1"
  local line="$2"
  log "Collector failed at line $line with exit code $exit_code."
  send_failure_bark "运行异常，退出码 $exit_code"
  exit "$exit_code"
}
trap 'on_error "$?" "$LINENO"' ERR

lock_mtime() {
  if stat -f %m "$1" >/dev/null 2>&1; then
    stat -f %m "$1"
  else
    stat -c %Y "$1"
  fi
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    return 0
  fi
  local modified now age
  modified="$(lock_mtime "$LOCK_DIR" 2>/dev/null || echo 0)"
  now="$(date +%s)"
  age=$((now - modified))
  if [[ "$age" -ge 7200 ]]; then
    log "Removing stale Mac mini collector lock."
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR"
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    return 0
  fi
  log "Another Mac mini collector instance is running; skipped."
  exit 0
}

mount_nas() {
  if mount | grep -Fq " on $NAS_MOUNT_PATH "; then
    return 0
  fi
  log "NAS home share is not mounted; attempting automatic mount."
  osascript -e "mount volume \"$NAS_MOUNT_URL\"" >> "$LOG_FILE" 2>&1
  mount | grep -Fq " on $NAS_MOUNT_PATH "
}

atomic_copy() {
  local source="$1"
  local destination="$2"
  [[ -f "$source" ]] || return 0
  mkdir -p "$(dirname -- "$destination")"
  local temporary="${destination}.tmp.$$"
  cp -X "$source" "$temporary"
  mv -f "$temporary" "$destination"
}

stage_from_nas() {
  atomic_copy "$NAS_REPO_DIR/data/history.json" "$RUNTIME_ROOT/data/history.json"
  atomic_copy "$NAS_REPO_DIR/data/manual_platform_metrics.json" "$RUNTIME_ROOT/data/manual_platform_metrics.json"
  atomic_copy "$NAS_REPO_DIR/data/private/comments.json" "$RUNTIME_ROOT/data/private/comments.json"
}

ensure_python() {
  if [[ ! -x "$PYTHON_BIN" ]]; then
    log "Creating Mac mini Python environment."
    python3 -m venv "$RUNTIME_ROOT/.venv" >> "$LOG_FILE" 2>&1
  fi
  local stamp="$RUNTIME_ROOT/.venv/.requirements.sha256"
  local current_hash
  current_hash="$(shasum -a 256 "$RUNTIME_ROOT/requirements.txt" | awk '{print $1}')"
  if [[ ! -f "$stamp" || "$(cat "$stamp" 2>/dev/null || true)" != "$current_hash" ]]; then
    log "Installing collector dependencies."
    "$PYTHON_BIN" -m pip install -r "$RUNTIME_ROOT/requirements.txt" >> "$LOG_FILE" 2>&1
    printf '%s' "$current_hash" > "$stamp"
  fi
}

publish_failure_status() {
  atomic_copy "$RUNTIME_ROOT/data/nas_status.json" "$NAS_REPO_DIR/data/nas_status.json"
  atomic_copy "$RUNTIME_ROOT/data/nas_status.json" "$NAS_REPO_DIR/dashboard/output/nas_status.json"
}

publish_success() {
  atomic_copy "$RUNTIME_ROOT/data/history.json" "$NAS_REPO_DIR/data/history.json"
  atomic_copy "$RUNTIME_ROOT/dashboard/output/index.html" "$NAS_REPO_DIR/dashboard/output/index.html"
  atomic_copy "$RUNTIME_ROOT/data/nas_status.json" "$NAS_REPO_DIR/data/nas_status.json"
  atomic_copy "$RUNTIME_ROOT/data/nas_status.json" "$NAS_REPO_DIR/dashboard/output/nas_status.json"
  atomic_copy "$RUNTIME_ROOT/data/private/comments.json" "$NAS_REPO_DIR/data/private/comments.json"
  atomic_copy "$LOG_FILE" "$NAS_REPO_DIR/data/logs/mac-mini-collector.log"
}

main() {
  cd "$RUNTIME_ROOT"
  load_config
  acquire_lock
  trap 'rm -rf "$LOCK_DIR"' EXIT
  mount_nas
  [[ -d "$NAS_REPO_DIR/.git" ]] || { log "NAS repository is unavailable: $NAS_REPO_DIR"; return 2; }

  if [[ "${DASHBOARD_MAC_DRY_RUN:-0}" == "1" ]]; then
    log "Dry run: Mac mini collector configuration and NAS mount are ready."
    return 0
  fi

  stage_from_nas
  ensure_python
  log "Mac mini platform collection started."
  if ! "$PYTHON_BIN" "$RUNTIME_ROOT/main.py" --live --no-feishu --no-bark >> "$LOG_FILE" 2>&1; then
    log "Primary platform render failed; preserving previous NAS data."
    send_failure_bark "主采集或渲染失败"
    return 1
  fi

  local comment_fetch_status="skipped"
  local comment_render_status="skipped"
  if [[ "${ENABLE_COMMENT_FETCH:-$ENABLE_COMMENT_INSIGHTS}" == "1" ]]; then
    if "$PYTHON_BIN" "$RUNTIME_ROOT/scripts/fetch_bilibili_comments.py" >> "$LOG_FILE" 2>&1; then
      comment_fetch_status="success"
    else
      comment_fetch_status="failed"
      log "Comment refresh failed; continuing with the previous private comment cache."
    fi
    if ENABLE_BILIBILI_FETCH=0 "$PYTHON_BIN" "$RUNTIME_ROOT/main.py" --cache --no-feishu --no-bark >> "$LOG_FILE" 2>&1; then
      comment_render_status="success"
    else
      comment_render_status="failed"
      log "Comment cache render failed; preserving primary output."
    fi
  fi

  "$PYTHON_BIN" "$RUNTIME_ROOT/scripts/write_nas_status.py" \
    --mode live \
    --dashboard-exit-code 0 \
    --timezone "${DASHBOARD_TIMEZONE:-Asia/Shanghai}" \
    --comment-fetch-status "$comment_fetch_status" \
    --comment-render-status "$comment_render_status" >> "$LOG_FILE" 2>&1

  local quality
  quality="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("data_quality_status", "failed"))' "$RUNTIME_ROOT/data/nas_status.json")"
  if [[ "$quality" == "failed" ]]; then
    publish_failure_status
    log "Required platform data is stale; previous NAS history and page were preserved."
    send_failure_bark "必需平台数据仍过期"
    return 3
  fi

  publish_success
  log "Mac mini collection finished and synced to NAS; data quality: $quality."
}

main "$@"
