#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${DASHBOARD_REPO_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
RUNTIME_ROOT="${DASHBOARD_MAC_RUNTIME_ROOT:-$REPO_DIR}"
CONFIG_FILE="${DASHBOARD_ENV_FILE:-$RUNTIME_ROOT/dashboard.env}"
LOG_DIR="${DASHBOARD_MAC_LOG_DIR:-$HOME/Library/Logs/CreatorDataDashboard}"
LOG_FILE="$LOG_DIR/collector.log"
LOCK_DIR="$RUNTIME_ROOT/data/logs/mac-mini-collector.lock"
BILIBILI_AUTH_ALERT_STAMP="$RUNTIME_ROOT/data/private/bilibili-auth-alert.stamp"
PAUSE_STAMP="$RUNTIME_ROOT/data/private/mac-mini-collector.pause"
HISTORY_GUARD_SCRIPT="$RUNTIME_ROOT/scripts/history_integrity_guard.py"
HISTORY_GUARD_BASELINE="$RUNTIME_ROOT/data/private/history-baseline.$$.json"
MAC_PYTHON_BASE="${DASHBOARD_MAC_PYTHON:-/opt/homebrew/bin/python3}"
[[ -x "$MAC_PYTHON_BASE" ]] || MAC_PYTHON_BASE="/usr/bin/python3"
MAC_VENV_ROOT="$RUNTIME_ROOT/.venv-mac"
MAC_PYTHON_BIN="$MAC_VENV_ROOT/bin/python"
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
  [[ -x "$MAC_PYTHON_BIN" ]] && sender="$MAC_PYTHON_BIN"
  DASHBOARD_ENV_FILE="$CONFIG_FILE" "$sender" -c \
    'from pathlib import Path; from scripts.noon_watchdog import load_env_files, send_bark; load_env_files(Path.cwd()); import sys; print(send_bark("Codex 项目结论", "【Mac mini】三平台看板运行失败：" + sys.argv[1] + "。完整的上一版数据已保留，后台采集已暂停，避免重复覆盖。", 20))' \
    "$summary" >> "$LOG_FILE" 2>&1 || true
}

send_bilibili_auth_bark() {
  local sender="python3"
  [[ -x "$MAC_PYTHON_BIN" ]] && sender="$MAC_PYTHON_BIN"
  DASHBOARD_ENV_FILE="$CONFIG_FILE" "$sender" -c \
    'from pathlib import Path; from scripts.noon_watchdog import load_env_files, send_bark; load_env_files(Path.cwd()); print(send_bark("Codex 项目结论", "【Mac mini】B站创作中心登录已失效，CTR、平均播放时长和完播率暂停刷新；公开数据仍在更新。请在这台 Mac mini 的 Edge 完成一次 B站登录，采集器之后会自动续期。", 20))' \
    >> "$LOG_FILE" 2>&1 || true
}

send_bilibili_refresh_error_bark() {
  local sender="python3"
  [[ -x "$MAC_PYTHON_BIN" ]] && sender="$MAC_PYTHON_BIN"
  DASHBOARD_ENV_FILE="$CONFIG_FILE" "$sender" -c \
    'from pathlib import Path; from scripts.noon_watchdog import load_env_files, send_bark; load_env_files(Path.cwd()); print(send_bark("Codex 项目结论", "【Mac mini】B站自动续期运行异常：Edge Cookie、macOS 钥匙串或本地配置暂不可用；公开数据仍在更新，创作中心指标不会假报。需要小黑查看 Mac mini 采集日志。", 20))' \
    >> "$LOG_FILE" 2>&1 || true
}

mark_collector_paused() {
  local reason="$1"
  mkdir -p "$(dirname -- "$PAUSE_STAMP")"
  local temporary="${PAUSE_STAMP}.tmp.$$"
  {
    printf 'paused_at=%s\n' "$(TZ=Asia/Shanghai date '+%Y-%m-%dT%H:%M:%S%z')"
    printf 'reason=%s\n' "$reason"
    printf 'head=%s\n' "$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  } > "$temporary"
  mv -f "$temporary" "$PAUSE_STAMP"
  log "Collector paused: $reason"
}

on_error() {
  local exit_code="$1"
  local line="$2"
  log "Collector failed at line $line with exit code $exit_code."
  if [[ ! -f "$PAUSE_STAMP" ]]; then
    mark_collector_paused "unexpected_error_exit_${exit_code}_line_${line}"
  fi
  send_failure_bark "运行异常，退出码 $exit_code"
  exit "$exit_code"
}
trap 'on_error "$?" "$LINENO"' ERR

cleanup() {
  rm -rf "$LOCK_DIR"
  rm -f "$HISTORY_GUARD_BASELINE"
}

lock_mtime() {
  if stat -f %m "$1" >/dev/null 2>&1; then
    stat -f %m "$1"
  else
    stat -c %Y "$1"
  fi
}

acquire_lock() {
  if [[ "${DASHBOARD_MAC_LOCK_HELD:-0}" == "1" ]] && \
    [[ "$(cat "$LOCK_DIR/pid" 2>/dev/null || true)" == "$$" ]]; then
    return 0
  fi
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

repository_operation_in_progress() {
  local git_dir
  git_dir="$(git rev-parse --git-dir 2>/dev/null || true)"
  [[ -n "$git_dir" ]] || return 0
  [[ -d "$git_dir/rebase-merge" || -d "$git_dir/rebase-apply" ]] && return 0
  local marker
  for marker in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG; do
    [[ -e "$git_dir/$marker" ]] && return 0
  done
  return 1
}

fail_repository_preflight() {
  local reason="$1"
  log "Repository preflight failed: $reason"
  mark_collector_paused "repository_preflight_failed:$reason"
  send_failure_bark "Git 仓库状态异常（${reason}）"
  return 10
}

preflight_repository() {
  [[ -d "$RUNTIME_ROOT/.git" ]] || {
    fail_repository_preflight "missing_git_repository"
    return
  }
  if repository_operation_in_progress; then
    fail_repository_preflight "unfinished_git_operation"
    return
  fi

  local current_branch
  current_branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  if [[ "$current_branch" != "$BRANCH" ]]; then
    fail_repository_preflight "expected_${BRANCH}_got_${current_branch:-detached_head}"
    return
  fi
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    fail_repository_preflight "tracked_worktree_not_clean"
    return
  fi

  if ! git fetch "$REMOTE_NAME" "$BRANCH" >> "$LOG_FILE" 2>&1; then
    fail_repository_preflight "cloud_fetch_failed"
    return
  fi
  local remote_ref="refs/remotes/$REMOTE_NAME/$BRANCH"
  if ! git rev-parse --verify "$remote_ref" >/dev/null 2>&1; then
    fail_repository_preflight "missing_remote_branch"
    return
  fi

  local ahead behind
  read -r ahead behind < <(git rev-list --left-right --count "HEAD...$remote_ref")
  if [[ "$ahead" != "0" ]]; then
    fail_repository_preflight "local_history_ahead_or_diverged"
    return
  fi
  if [[ "$behind" != "0" ]]; then
    local script_before script_after
    script_before="$(git rev-parse "HEAD:scripts/mac_mini_update_and_sync.sh" 2>/dev/null || true)"
    log "Fast-forwarding the runtime from the complete cloud baseline before collection."
    if ! git merge --ff-only "$remote_ref" >> "$LOG_FILE" 2>&1; then
      fail_repository_preflight "cloud_fast_forward_failed"
      return
    fi
    script_after="$(git rev-parse "HEAD:scripts/mac_mini_update_and_sync.sh" 2>/dev/null || true)"
    if [[ "$script_before" != "$script_after" ]]; then
      log "Collector source changed during fast-forward; restarting with the updated script."
      export DASHBOARD_MAC_LOCK_HELD=1
      exec /bin/bash "$RUNTIME_ROOT/scripts/mac_mini_update_and_sync.sh"
    fi
  fi
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

capture_history_baseline() {
  "$MAC_PYTHON_BASE" "$HISTORY_GUARD_SCRIPT" capture \
    "$RUNTIME_ROOT/data/history.json" \
    --output "$HISTORY_GUARD_BASELINE" >> "$LOG_FILE" 2>&1
}

verify_history_integrity() {
  "$MAC_PYTHON_BASE" "$HISTORY_GUARD_SCRIPT" verify \
    --baseline "$HISTORY_GUARD_BASELINE" \
    --candidate "$RUNTIME_ROOT/data/history.json" >> "$LOG_FILE" 2>&1
}

restore_published_history() {
  git restore --worktree -- data/history.json dashboard/output/index.html
}

ensure_python() {
  if [[ ! -x "$MAC_PYTHON_BIN" ]]; then
    log "Creating Mac mini Python environment."
    "$MAC_PYTHON_BASE" -m venv "$MAC_VENV_ROOT" >> "$LOG_FILE" 2>&1
  fi
  local stamp="$MAC_VENV_ROOT/.requirements.sha256"
  local current_hash
  current_hash="$(shasum -a 256 "$RUNTIME_ROOT/requirements.txt" | awk '{print $1}')"
  if [[ ! -f "$stamp" || "$(cat "$stamp" 2>/dev/null || true)" != "$current_hash" ]] || \
    ! "$MAC_PYTHON_BIN" -c 'import browser_cookie3, dateutil, httpx, jinja2' >/dev/null 2>&1; then
    log "Installing collector dependencies."
    "$MAC_PYTHON_BIN" -m pip install -r "$RUNTIME_ROOT/requirements.txt" >> "$LOG_FILE" 2>&1
    printf '%s' "$current_hash" > "$stamp"
  fi
}

refresh_bilibili_browser_cookie() {
  if [[ "${BILIBILI_BROWSER_COOKIE_REFRESH_ENABLED:-1}" != "1" ]]; then
    log "Bilibili Edge credential refresh is disabled."
    return 0
  fi

  local command=(
    "$MAC_PYTHON_BIN"
    "$RUNTIME_ROOT/scripts/refresh_bilibili_browser_cookie.py"
    --env-file "$CONFIG_FILE"
    --account-id "${BILIBILI_ACCOUNT_ID:-516185777}"
  )
  if [[ -n "${BILIBILI_BROWSER_COOKIE_FILE:-}" ]]; then
    command+=(--cookie-file "$BILIBILI_BROWSER_COOKIE_FILE")
  fi

  local refresh_exit
  if "${command[@]}" >> "$LOG_FILE" 2>&1; then
    load_config
    log "Bilibili Edge credential refreshed after account and creator-center validation."
    return 0
  else
    refresh_exit=$?
  fi

  log "Bilibili Edge credential refresh returned $refresh_exit; continuing with public-data fallback."
  mkdir -p "$(dirname -- "$BILIBILI_AUTH_ALERT_STAMP")"
  local now modified age
  now="$(date +%s)"
  modified="$(lock_mtime "$BILIBILI_AUTH_ALERT_STAMP" 2>/dev/null || echo 0)"
  age=$((now - modified))
  if [[ ! -f "$BILIBILI_AUTH_ALERT_STAMP" || "$age" -ge 86400 ]]; then
    if [[ "$refresh_exit" == "2" ]]; then
      send_bilibili_auth_bark
    else
      send_bilibili_refresh_error_bark
    fi
    touch "$BILIBILI_AUTH_ALERT_STAMP"
  fi
  return 0
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
    log "Cloud push was rejected; no automatic rebase will be attempted."
    git fetch "$REMOTE_NAME" "$BRANCH" >> "$LOG_FILE" 2>&1 || true
    mark_collector_paused "cloud_push_rejected"
    send_failure_bark "GitHub 同步冲突，未执行自动 rebase"
    return 12
  fi
}

main() {
  cd "$RUNTIME_ROOT"
  load_config
  acquire_lock
  trap cleanup EXIT
  if [[ -f "$PAUSE_STAMP" && "${DASHBOARD_MAC_IGNORE_PAUSE:-0}" != "1" ]]; then
    log "Collector remains paused by $PAUSE_STAMP; skipped without another alert."
    return 0
  fi
  if ! preflight_repository; then
    return 10
  fi

  if [[ "${DASHBOARD_MAC_DRY_RUN:-0}" == "1" ]]; then
    log "Dry run: Mac mini collector configuration and GitHub publishing are ready."
    return 0
  fi

  capture_history_baseline
  ensure_python
  refresh_bilibili_browser_cookie
  log "Mac mini platform collection started."
  if ! "$MAC_PYTHON_BIN" "$RUNTIME_ROOT/main.py" --live --no-feishu --no-bark >> "$LOG_FILE" 2>&1; then
    log "Primary platform render failed; preserving the previous published history and page."
    restore_published_history
    "$MAC_PYTHON_BIN" "$RUNTIME_ROOT/scripts/write_nas_status.py" --mode live --dashboard-exit-code 1 \
      --timezone "${DASHBOARD_TIMEZONE:-Asia/Shanghai}" >> "$LOG_FILE" 2>&1 || true
    publish_to_cloud failure || true
    mark_collector_paused "primary_collection_or_render_failed"
    send_failure_bark "主采集或渲染失败"
    return 1
  fi

  local comment_fetch_status="skipped"
  local comment_render_status="skipped"
  if [[ "${ENABLE_COMMENT_FETCH:-$ENABLE_COMMENT_INSIGHTS}" == "1" ]]; then
    if "$MAC_PYTHON_BIN" "$RUNTIME_ROOT/scripts/fetch_bilibili_comments.py" >> "$LOG_FILE" 2>&1; then
      comment_fetch_status="success"
    else
      comment_fetch_status="failed"
      log "Comment refresh failed; continuing with the previous private comment cache."
    fi
    if ENABLE_BILIBILI_FETCH=0 "$MAC_PYTHON_BIN" "$RUNTIME_ROOT/main.py" --cache --no-feishu --no-bark >> "$LOG_FILE" 2>&1; then
      comment_render_status="success"
    else
      comment_render_status="failed"
      log "Comment cache render failed; preserving primary output."
    fi
  fi

  if ! verify_history_integrity; then
    log "History integrity guard rejected the generated data; restoring the previous published history and page."
    restore_published_history
    "$MAC_PYTHON_BIN" "$RUNTIME_ROOT/scripts/write_nas_status.py" --mode live --dashboard-exit-code 2 \
      --timezone "${DASHBOARD_TIMEZONE:-Asia/Shanghai}" >> "$LOG_FILE" 2>&1 || true
    publish_to_cloud failure || true
    mark_collector_paused "history_integrity_guard_failed"
    send_failure_bark "历史完整性校验失败，已阻止覆盖"
    return 2
  fi

  "$MAC_PYTHON_BIN" "$RUNTIME_ROOT/scripts/write_nas_status.py" \
    --mode live \
    --dashboard-exit-code 0 \
    --timezone "${DASHBOARD_TIMEZONE:-Asia/Shanghai}" \
    --comment-fetch-status "$comment_fetch_status" \
    --comment-render-status "$comment_render_status" >> "$LOG_FILE" 2>&1

  local quality
  quality="$("$MAC_PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("data_quality_status", "failed"))' "$RUNTIME_ROOT/data/nas_status.json")"
  if [[ "$quality" == "failed" ]]; then
    restore_published_history
    publish_to_cloud failure
    log "Required platform data is stale; previous published history and page were preserved."
    mark_collector_paused "required_platform_data_stale"
    send_failure_bark "必需平台数据仍过期"
    return 3
  fi

  publish_to_cloud success
  log "Mac mini collection finished and published; NAS will pull it on its existing schedule. Data quality: $quality."
}

main "$@"
