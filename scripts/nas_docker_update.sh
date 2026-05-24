#!/bin/sh
set -eu

APP_DIR="${DASHBOARD_NAS_APP_DIR:-/root/bilibili-dashboard}"
IMAGE="${DASHBOARD_DOCKER_IMAGE:-python:3.11-slim}"
LOG_FILE="${DASHBOARD_DOCKER_LOG:-$APP_DIR/data/logs/nas-docker.log}"
HOME_DIR="${HOME:-/root}"
ENV_FILE="${DASHBOARD_ENV_FILE:-$HOME_DIR/.config/bilibili-dashboard/dashboard.env}"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  timezone="${DASHBOARD_TIMEZONE:-Asia/Shanghai}"
  timestamp="$(TZ="$timezone" date '+%Y-%m-%d %H:%M:%S %Z')"
  if [ "$timezone" = "Asia/Shanghai" ] && echo "$timestamp" | grep -q "UTC"; then
    timestamp="$(TZ="CST-8" date '+%Y-%m-%d %H:%M:%S %Z')"
  fi
  printf '[%s] %s\n' "$timestamp" "$*" >> "$LOG_FILE"
}

if ! command -v docker >/dev/null 2>&1; then
  log "Docker is not available on this NAS environment."
  exit 1
fi

if [ ! -d "$APP_DIR" ]; then
  log "Dashboard directory not found: $APP_DIR"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  log "Environment file not found: $ENV_FILE"
  exit 1
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  log "Pulling Docker image: $IMAGE"
  docker pull "$IMAGE" >> "$LOG_FILE" 2>&1
fi

log "Starting dashboard update container."
docker run --rm \
  --network host \
  -v "$APP_DIR:/app" \
  -w /app \
  --env-file "$ENV_FILE" \
  "$IMAGE" \
  /bin/bash scripts/nas_update_dashboard.sh >> "$LOG_FILE" 2>&1
log "Dashboard update container finished."
