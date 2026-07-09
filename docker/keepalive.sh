#!/bin/sh
# Publish gray RTMP fallback when a MediaMTX path has no active publisher.

set -u

MEDIAMTX_API="${MEDIAMTX_API:-http://mediamtx:9997}"
RTMP_BASE="${RTMP_BASE:-rtmp://mediamtx:1935}"
PATHS="${KEEPALIVE_PATHS:-stairs_over_door}"
POLL_INTERVAL="${POLL_INTERVAL:-1}"
LOG_DIR="${LOG_DIR:-/logs}"

path_has_publisher() {
  path="$1"
  json="$(curl -sf "${MEDIAMTX_API}/v3/paths/get/${path}" 2>/dev/null)" || return 1
  case "$json" in
    *'"source":null'* | *'"source": null'*) return 1 ;;
    *) return 0 ;;
  esac
}

publish_fallback() {
  path="$1"
  ffmpeg -hide_banner -loglevel warning -re \
    -f lavfi -i color=c=gray:s=1280x720:r=10 \
    -an -c:v libx264 -pix_fmt yuv420p -profile:v baseline -preset ultrafast \
    -tune stillimage -bf 0 -g 10 -r 10 -vsync cfr -b:v 400k \
    -f flv "${RTMP_BASE}/${path}"
}

watch_path() {
  path="$1"
  echo "keepalive: watching ${path} via ${MEDIAMTX_API}"
  while true; do
    if path_has_publisher "$path"; then
      sleep "$POLL_INTERVAL"
      continue
    fi
    echo "$(date '+%Y-%m-%dT%H:%M:%SZ') keepalive: no publisher on ${path}, starting gray fallback"
    publish_fallback "$path" || true
    sleep "$POLL_INTERVAL"
  done
}

run() {
  for path in $(echo "$PATHS" | tr ',' ' '); do
    [ -n "$path" ] || continue
    watch_path "$path" &
  done
  wait
}

mkdir -p "$LOG_DIR"
if [ -w "$LOG_DIR" ]; then
  run 2>&1 | tee -a "${LOG_DIR}/keepalive.log"
else
  run
fi
