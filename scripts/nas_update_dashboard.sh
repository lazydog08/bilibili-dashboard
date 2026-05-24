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
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "Another dashboard update is already running; skipped."
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

log "Dashboard update started."

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
export DASHBOARD_TIMEZONE="$TIMEZONE"

MODE="${DASHBOARD_MODE:-live}"
CMD=("$PYTHON_BIN" "main.py")
case "$MODE" in
  live)
    CMD+=("--live")
    ;;
  fixture)
    CMD+=("--fixture")
    ;;
  cache|local)
    ;;
  *)
    log "Unknown DASHBOARD_MODE=$MODE; expected live, cache, or fixture."
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

if [[ "${RUN_DASHBOARD_TESTS:-0}" == "1" ]]; then
  log "Running tests."
  "$PYTHON_BIN" -m pytest >> "$LOG_FILE" 2>&1 || log "Tests failed; dashboard output was still left in place."
fi

if [[ -n "${DASHBOARD_PUBLISH_DIR:-}" && -f "$REPO_DIR/dashboard/output/index.html" ]]; then
  mkdir -p "$DASHBOARD_PUBLISH_DIR"
  cp "$REPO_DIR/dashboard/output/index.html" "$DASHBOARD_PUBLISH_DIR/index.html"
  log "Copied dashboard output to publish directory."
fi

if [[ "${DASHBOARD_GIT_PUSH:-0}" == "1" ]] && command -v git >/dev/null 2>&1 && [[ -d "$REPO_DIR/.git" ]]; then
  if ! git diff --quiet -- data/history.json dashboard/output/index.html; then
    git add data/history.json dashboard/output/index.html
    git commit -m "chore: update dashboard $(TZ="$TIMEZONE" date '+%Y-%m-%d %H:%M')" >> "$LOG_FILE" 2>&1 || true
    git push >> "$LOG_FILE" 2>&1 || log "Git push failed."
  else
    log "No dashboard changes to commit."
  fi
fi

log "Dashboard update finished."
exit "$UPDATE_STATUS"
