#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${DASHBOARD_REPO_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
RUNTIME_ROOT="${DASHBOARD_MAC_RUNTIME_ROOT:-$REPO_DIR}"
CONFIG_FILE="${DASHBOARD_ENV_FILE:-$RUNTIME_ROOT/dashboard.env}"
LOG_DIR="${DASHBOARD_MAC_LOG_DIR:-$HOME/Library/Logs/CreatorDataDashboard}"
LOG_FILE="$LOG_DIR/collector.log"
LOCK_DIR="$RUNTIME_ROOT/data/logs/mac-mini-collector.lock"
PYTHON_BIN="$RUNTIME_ROOT/.venv/bin/python"
REMOTE_NAME="${DASHBOARD_CLOUD_REMOTE_NAME:-origin}"
BRANCH="${DASHBOARD_CLOUD_BRANCH:-main}"
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
  unset GIT_EXEC_PATH GIT_TEMPLATE_DIR GIT_SSH GIT_SSH_COMMAND
  export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
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

atomic_copy() {
  local source="$1"
  local destination="$2"
  [[ -f "$source" ]] || return 0
  mkdir -p "$(dirname -- "$destination")"
  local temporary="${destination}.tmp.$$"
  cp -X "$source" "$temporary"
  mv -f "$temporary" "$destination"
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

publish_to_cloud() {
  local mode="$1"
  atomic_copy "$RUNTIME_ROOT/data/nas_status.json" "$RUNTIME_ROOT/dashboard/output/nas_status.json"
  git add data/nas_status.json dashboard/output/nas_status.json
  if [[ "$mode" == "success" ]]; then
    git add data/history.json dashboard/output/index.html
  fi

  local path
  while IFS= read -r path; do
    case "$path" in
      data/history.json|data/nas_status.json|dashboard/output/index.html|dashboard/output/nas_status.json) ;;
      *) log "Refusing to publish unexpected staged path: $path"; return 4 ;;
    esac
  done < <(git diff --cached --name-only)

  if git diff --cached --quiet; then
    log "No public dashboard changes to publish."
  else
    local commit_time
    commit_time="$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M')"
    git commit -m "chore: update dashboard from Mac mini $commit_time" >> "$LOG_FILE" 2>&1
  fi
  if ! git push "$REMOTE_NAME" "HEAD:$BRANCH" >> "$LOG_FILE" 2>&1; then
    log "Cloud push was rejected; rebasing once onto the latest public branch."
    git fetch "$REMOTE_NAME" "$BRANCH" >> "$LOG_FILE" 2>&1
    git rebase "$REMOTE_NAME/$BRANCH" >> "$LOG_FILE" 2>&1
    git push "$REMOTE_NAME" "HEAD:$BRANCH" >> "$LOG_FILE" 2>&1
  fi
}

main() {
  cd "$RUNTIME_ROOT"
  load_config
  acquire_lock
  trap 'rm -rf "$LOCK_DIR"' EXIT
  [[ -d "$RUNTIME_ROOT/.git" ]] || { log "Local runtime Git repository is missing."; return 2; }

  if [[ "${DASHBOARD_MAC_DRY_RUN:-0}" == "1" ]]; then
    log "Dry run: Mac mini collector configuration and GitHub publishing are ready."
    return 0
  fi

  ensure_python
  log "Mac mini platform collection started."
  if ! "$PYTHON_BIN" "$RUNTIME_ROOT/main.py" --live --no-feishu --no-bark >> "$LOG_FILE" 2>&1; then
    log "Primary platform render failed; preserving the previous published history and page."
    "$PYTHON_BIN" "$RUNTIME_ROOT/scripts/write_nas_status.py" --mode live --dashboard-exit-code 1 \
      --timezone "${DASHBOARD_TIMEZONE:-Asia/Shanghai}" >> "$LOG_FILE" 2>&1 || true
    publish_to_cloud failure || true
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
    publish_to_cloud failure
    log "Required platform data is stale; previous published history and page were preserved."
    send_failure_bark "必需平台数据仍过期"
    return 3
  fi

  publish_to_cloud success
  log "Mac mini collection finished and published; NAS will pull it on its existing schedule. Data quality: $quality."
}

main "$@"
