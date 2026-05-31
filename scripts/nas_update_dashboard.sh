#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${DASHBOARD_REPO_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
cd "$REPO_DIR"

DEFAULT_ENV_HOME="${HOME:-$REPO_DIR}"
ENV_FILE="${DASHBOARD_ENV_FILE:-$DEFAULT_ENV_HOME/.config/bilibili-dashboard/dashboard.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

TIMEZONE="${DASHBOARD_TIMEZONE:-Asia/Shanghai}"
LOG_FILE="${DASHBOARD_UPDATE_LOG:-$REPO_DIR/data/logs/nas-update.log}"
if [[ "$LOG_FILE" != /* ]]; then
  LOG_FILE="$REPO_DIR/$LOG_FILE"
fi
mkdir -p "$(dirname -- "$LOG_FILE")"

log() {
  timestamp="$(TZ="$TIMEZONE" date '+%Y-%m-%d %H:%M:%S %Z')"
  if [[ "$TIMEZONE" == "Asia/Shanghai" && "$timestamp" == *"UTC" ]]; then
    timestamp="$(TZ="CST-8" date '+%Y-%m-%d %H:%M:%S %Z')"
  fi
  printf '[%s] %s\n' "$timestamp" "$*" >> "$LOG_FILE"
}

LOCK_DIR="${DASHBOARD_LOCK_DIR:-$REPO_DIR/data/logs/nas-update.lock}"
LOCK_MAX_AGE_SECONDS="${DASHBOARD_LOCK_MAX_AGE_SECONDS:-7200}"
case "$LOCK_MAX_AGE_SECONDS" in
  ''|*[!0-9]*) LOCK_MAX_AGE_SECONDS=7200 ;;
esac

lock_mtime() {
  if stat -c %Y "$1" >/dev/null 2>&1; then
    stat -c %Y "$1"
  else
    stat -f %m "$1"
  fi
}

lock_pid_is_running() {
  local pid_file="$1/pid"
  local pid
  [[ -f "$pid_file" ]] || return 1
  pid="$(tr -dc '0-9' < "$pid_file")"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

lock_is_stale() {
  local modified
  local now
  local age
  [[ -d "$LOCK_DIR" ]] || return 1
  if lock_pid_is_running "$LOCK_DIR"; then
    return 1
  fi
  modified="$(lock_mtime "$LOCK_DIR" 2>/dev/null || echo 0)"
  now="$(date +%s)"
  age=$((now - modified))
  [[ "$age" -ge "$LOCK_MAX_AGE_SECONDS" ]]
}

write_lock_metadata() {
  printf '%s\n' "$$" > "$LOCK_DIR/pid" 2>/dev/null || true
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$LOCK_DIR/created_at" 2>/dev/null || true
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if lock_is_stale; then
    log "Removing stale dashboard update lock: $LOCK_DIR"
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" 2>/dev/null || {
      log "Another dashboard update is already running; skipped."
      exit 0
    }
  else
    log "Another dashboard update is already running; skipped."
    exit 0
  fi
fi
write_lock_metadata
trap 'rm -rf "$LOCK_DIR"' EXIT

log "Dashboard update started."
COMMENT_FETCH_STATUS="skipped"
COMMENT_RENDER_STATUS="skipped"
TESTS_STATUS="skipped"
PUBLISH_STATUS="skipped"
STATUS_HEARTBEAT_WRITTEN="0"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$REPO_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

if [[ ! -x "$REPO_DIR/.venv/bin/python" && "${AUTO_CREATE_VENV:-1}" == "1" ]]; then
  log "Creating Python virtual environment."
  "$PYTHON_BIN" -m venv "$REPO_DIR/.venv" >> "$LOG_FILE" 2>&1
  PYTHON_BIN="$REPO_DIR/.venv/bin/python"
fi

if [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_DIR/.venv/bin/python"
fi

REQ_STAMP="$REPO_DIR/.venv/.requirements.sha256"
REQ_HASH=""
if command -v shasum >/dev/null 2>&1; then
  REQ_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"
elif command -v sha256sum >/dev/null 2>&1; then
  REQ_HASH="$(sha256sum requirements.txt | awk '{print $1}')"
fi

if [[ ! -f "$REQ_STAMP" || -z "$REQ_HASH" || "$(cat "$REQ_STAMP" 2>/dev/null || true)" != "$REQ_HASH" ]]; then
  log "Installing Python dependencies."
  "$PYTHON_BIN" -m pip install -r requirements.txt >> "$LOG_FILE" 2>&1
  if [[ -n "$REQ_HASH" ]]; then
    printf '%s' "$REQ_HASH" > "$REQ_STAMP"
  fi
fi

export ENABLE_BILIBILI_FETCH="${ENABLE_BILIBILI_FETCH:-1}"
export ENABLE_COMMENT_INSIGHTS="${ENABLE_COMMENT_INSIGHTS:-1}"
export DASHBOARD_TIMEZONE="$TIMEZONE"

MODE="${DASHBOARD_MODE:-live}"
CMD=("$PYTHON_BIN" "main.py")
case "$MODE" in
  live)
    CMD+=("--live")
    ;;
  bilibili|bilibili-only)
    CMD+=("--bilibili-only")
    ;;
  fixture)
    CMD+=("--fixture")
    ;;
  cache|local)
    CMD+=("--cache")
    ;;
  *)
    log "Unknown DASHBOARD_MODE=$MODE; expected live, bilibili-only, cache, or fixture."
    exit 2
    ;;
esac

if [[ "${ENABLE_FEISHU_SYNC:-0}" != "1" ]]; then
  CMD+=("--no-feishu")
fi

if [[ "${DISABLE_BARK:-0}" == "1" ]]; then
  CMD+=("--no-bark")
fi

if [[ -n "${SNAPSHOT_DATE:-}" ]]; then
  CMD+=("--snapshot-date" "$SNAPSHOT_DATE")
fi

if "${CMD[@]}" >> "$LOG_FILE" 2>&1; then
  UPDATE_STATUS=0
  log "Dashboard render completed."
else
  UPDATE_STATUS=$?
  log "Dashboard render failed with exit code $UPDATE_STATUS."
fi

ENABLE_COMMENT_FETCH="${ENABLE_COMMENT_FETCH:-$ENABLE_COMMENT_INSIGHTS}"
if [[ "$UPDATE_STATUS" == "0" && "$MODE" != "fixture" && "$ENABLE_COMMENT_FETCH" == "1" ]]; then
  log "Fetching Bilibili comments."
  if "$PYTHON_BIN" "$REPO_DIR/scripts/fetch_bilibili_comments.py" >> "$LOG_FILE" 2>&1; then
    COMMENT_FETCH_STATUS="success"
    log "Comment fetch completed."
  else
    COMMENT_FETCH_STATUS="failed"
    log "Comment fetch failed; rendering with the latest available comment cache."
  fi

  log "Rendering dashboard with comment cache."
  if ENABLE_BILIBILI_FETCH=0 "$PYTHON_BIN" "$REPO_DIR/main.py" "--cache" "--no-feishu" "--no-bark" >> "$LOG_FILE" 2>&1; then
    COMMENT_RENDER_STATUS="success"
    log "Dashboard comment render completed."
  else
    COMMENT_RENDER_EXIT_CODE=$?
    COMMENT_RENDER_STATUS="failed"
    log "Dashboard comment render failed with exit code $COMMENT_RENDER_EXIT_CODE; keeping the primary dashboard output."
  fi
fi

if [[ "${RUN_DASHBOARD_TESTS:-0}" == "1" ]]; then
  log "Running tests."
  if "$PYTHON_BIN" -m pytest >> "$LOG_FILE" 2>&1; then
    TESTS_STATUS="success"
  else
    TESTS_STATUS="failed"
    log "Tests failed; dashboard output was still left in place."
  fi
fi

if [[ -n "${DASHBOARD_PUBLISH_DIR:-}" && -f "$REPO_DIR/dashboard/output/index.html" ]]; then
  mkdir -p "$DASHBOARD_PUBLISH_DIR"
  cp "$REPO_DIR/dashboard/output/index.html" "$DASHBOARD_PUBLISH_DIR/index.html"
  PUBLISH_STATUS="success"
  log "Copied dashboard output to publish directory."
fi

STATUS_GIT_PATH="${DASHBOARD_NAS_STATUS_PATH:-data/nas_status.json}"
if [[ "${DASHBOARD_NAS_STATUS_ENABLED:-1}" == "1" ]]; then
  log "Writing NAS status heartbeat."
  if "$PYTHON_BIN" "$REPO_DIR/scripts/write_nas_status.py" \
    --mode "$MODE" \
    --dashboard-exit-code "$UPDATE_STATUS" \
    --timezone "$TIMEZONE" \
    --comment-fetch-status "$COMMENT_FETCH_STATUS" \
    --comment-render-status "$COMMENT_RENDER_STATUS" \
    --tests-status "$TESTS_STATUS" \
    --publish-status "$PUBLISH_STATUS" >> "$LOG_FILE" 2>&1; then
    STATUS_HEARTBEAT_WRITTEN="1"
    log "NAS status heartbeat written."
  else
    STATUS_HEARTBEAT_WRITTEN="0"
    log "NAS status heartbeat failed."
  fi
fi

if [[ "$STATUS_HEARTBEAT_WRITTEN" == "1" && "$STATUS_GIT_PATH" != /* && -f "$REPO_DIR/$STATUS_GIT_PATH" && -d "$REPO_DIR/dashboard/output" ]]; then
  cp "$REPO_DIR/$STATUS_GIT_PATH" "$REPO_DIR/dashboard/output/nas_status.json"
  log "Copied NAS status heartbeat to dashboard output."
fi

if [[ "$STATUS_HEARTBEAT_WRITTEN" == "1" && -n "${DASHBOARD_PUBLISH_DIR:-}" && -f "$REPO_DIR/dashboard/output/nas_status.json" ]]; then
  cp "$REPO_DIR/dashboard/output/nas_status.json" "$DASHBOARD_PUBLISH_DIR/nas_status.json"
  log "Copied NAS status heartbeat to publish directory."
fi

if [[ "${DASHBOARD_GIT_PUSH:-0}" == "1" ]] && command -v git >/dev/null 2>&1 && [[ -d "$REPO_DIR/.git" ]]; then
  GIT_ADD_PATHS=()
  if [[ -f "$REPO_DIR/data/history.json" ]]; then
    GIT_ADD_PATHS+=(data/history.json)
  fi
  if [[ -f "$REPO_DIR/dashboard/output/index.html" ]]; then
    GIT_ADD_PATHS+=(dashboard/output/index.html)
  fi
  if [[ "$STATUS_HEARTBEAT_WRITTEN" == "1" && "$STATUS_GIT_PATH" != /* && -f "$REPO_DIR/$STATUS_GIT_PATH" ]]; then
    GIT_ADD_PATHS+=("$STATUS_GIT_PATH")
  fi
  if [[ "$STATUS_HEARTBEAT_WRITTEN" == "1" && -f "$REPO_DIR/dashboard/output/nas_status.json" ]]; then
    GIT_ADD_PATHS+=(dashboard/output/nas_status.json)
  fi
  if (( ${#GIT_ADD_PATHS[@]} > 0 )); then
    git add "${GIT_ADD_PATHS[@]}"
  fi
  if (( ${#GIT_ADD_PATHS[@]} > 0 )) && ! git diff --staged --quiet; then
    git commit -m "chore: update dashboard $(TZ="$TIMEZONE" date '+%Y-%m-%d %H:%M')" >> "$LOG_FILE" 2>&1 || true
    git push >> "$LOG_FILE" 2>&1 || log "Git push failed."
  else
    log "No dashboard changes to commit."
  fi
fi

log "Dashboard update finished."
exit "$UPDATE_STATUS"
