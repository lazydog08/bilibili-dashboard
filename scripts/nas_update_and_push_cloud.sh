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

LOCK_DIR="${DASHBOARD_CLOUD_LOCK_DIR:-$REPO_DIR/data/logs/nas-cloud-update.lock}"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "Another NAS cloud update is already running; skipped."
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

BRANCH="${DASHBOARD_CLOUD_BRANCH:-main}"
REMOTE_NAME="${DASHBOARD_CLOUD_REMOTE_NAME:-origin}"
REMOTE_URL="${DASHBOARD_CLOUD_REMOTE_URL:-}"

log "NAS cloud update started."

ensure_cloud_remote() {
  local current_remote_url
  if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    current_remote_url="$(git remote get-url "$REMOTE_NAME")"
    if [[ -n "$REMOTE_URL" && "$current_remote_url" != "$REMOTE_URL" ]]; then
      log "Git remote '$REMOTE_NAME' does not match configured cloud remote; updating it."
      git remote set-url "$REMOTE_NAME" "$REMOTE_URL" >> "$LOG_FILE" 2>&1
    fi
    return
  fi

  if [[ -z "$REMOTE_URL" ]]; then
    log "Git remote '$REMOTE_NAME' is missing. Set DASHBOARD_CLOUD_REMOTE_URL."
    exit 1
  fi
  git remote add "$REMOTE_NAME" "$REMOTE_URL" >> "$LOG_FILE" 2>&1
}

if ! command -v git >/dev/null 2>&1; then
  log "Git is not available; cannot push dashboard to cloud."
  exit 1
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  if [[ -z "$REMOTE_URL" ]]; then
    log "This project directory is not a Git repository. Set DASHBOARD_CLOUD_REMOTE_URL or clone the GitHub repo on the NAS."
    exit 1
  fi
  log "Initializing Git repository for cloud publishing."
  git init >> "$LOG_FILE" 2>&1
  git branch -M "$BRANCH" >> "$LOG_FILE" 2>&1 || true
  git remote add "$REMOTE_NAME" "$REMOTE_URL" >> "$LOG_FILE" 2>&1
  git fetch "$REMOTE_NAME" "$BRANCH" >> "$LOG_FILE" 2>&1 || log "Initial Git fetch failed; continuing with local state."
  if git rev-parse --verify "refs/remotes/$REMOTE_NAME/$BRANCH" >/dev/null 2>&1; then
    git reset --mixed "refs/remotes/$REMOTE_NAME/$BRANCH" >> "$LOG_FILE" 2>&1
  fi
fi

ensure_cloud_remote

git config user.email "${DASHBOARD_GIT_EMAIL:-nas-dashboard@local}" >> "$LOG_FILE" 2>&1
git config user.name "${DASHBOARD_GIT_NAME:-UGREEN NAS Dashboard Bot}" >> "$LOG_FILE" 2>&1

sync_cloud_state() {
  log "Synchronizing latest cloud repository state."
  git fetch "$REMOTE_NAME" "$BRANCH" >> "$LOG_FILE" 2>&1 || {
    log "Git fetch failed; aborting to avoid pushing over unknown cloud state."
    exit 1
  }
  if git rev-parse --verify "refs/remotes/$REMOTE_NAME/$BRANCH" >/dev/null 2>&1; then
    git -c rebase.autoStash=true rebase "refs/remotes/$REMOTE_NAME/$BRANCH" >> "$LOG_FILE" 2>&1 || {
      log "Git rebase failed; aborting to avoid overwriting cloud data."
      git rebase --abort >> "$LOG_FILE" 2>&1 || true
      exit 1
    }
  fi
}

if [[ "${DASHBOARD_GIT_PULL_BEFORE_PUSH:-1}" == "1" ]]; then
  sync_cloud_state
fi

if [[ "${DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH:-1}" == "1" ]]; then
  DASHBOARD_GIT_PUSH=0 "$SCRIPT_DIR/nas_update_dashboard.sh"
else
  log "Skipping dashboard refresh before cloud push; using latest local output."
fi

if [[ ! -f "$REPO_DIR/data/history.json" || ! -f "$REPO_DIR/dashboard/output/index.html" ]]; then
  log "Dashboard output is missing; run nas_update_dashboard.sh before pushing."
  exit 1
fi

STATUS_GIT_PATH="${DASHBOARD_NAS_STATUS_PATH:-data/nas_status.json}"
git add data/history.json dashboard/output/index.html
if [[ "$STATUS_GIT_PATH" != /* && -f "$REPO_DIR/$STATUS_GIT_PATH" ]]; then
  git add "$STATUS_GIT_PATH"
fi
if [[ -f "$REPO_DIR/dashboard/output/nas_status.json" ]]; then
  git add dashboard/output/nas_status.json
fi
if git diff --staged --quiet; then
  log "No dashboard changes to push."
  log "NAS cloud update finished."
  exit 0
fi

commit_time="$(TZ="$TIMEZONE" date '+%Y-%m-%d %H:%M')"
git commit -m "chore: update dashboard from NAS $commit_time" >> "$LOG_FILE" 2>&1

log "Pushing dashboard update to cloud."
if ! git push "$REMOTE_NAME" "HEAD:$BRANCH" >> "$LOG_FILE" 2>&1; then
  log "Git push failed; synchronizing cloud state and retrying once."
  sync_cloud_state
  git push "$REMOTE_NAME" "HEAD:$BRANCH" >> "$LOG_FILE" 2>&1 || {
    log "Git push failed after retry."
    exit 1
  }
fi

log "NAS cloud update finished."
