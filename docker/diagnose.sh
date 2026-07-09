#!/bin/sh
# Quick diagnostics for the MediaMTX compose stack.
set -u

cd "$(dirname "$0")/.."

echo "=== compose services ==="
docker compose ps -a

echo ""
echo "=== mediamtx last log lines ==="
docker logs mediamtx 2>&1 | tail -20 || echo "(mediamtx container missing)"

echo ""
echo "=== mediamtx file log ==="
tail -10 logs/mediamtx.log 2>/dev/null || echo "(no logs/mediamtx.log)"

echo ""
echo "=== config on disk (alwaysAvailable / auth) ==="
grep -E 'alwaysAvailable|authInternalUsers|overridePublisher' -A6 docker/mediamtx.yml 2>/dev/null \
  || echo "(docker/mediamtx.yml missing)"

echo ""
echo "=== path status (API) ==="
curl -sf http://127.0.0.1:9997/v3/paths/get/stairs_over_door 2>/dev/null \
  || echo "(API auth failed or unreachable — check auth block was loaded; run: docker compose up -d --force-recreate mediamtx)"

echo ""
echo "=== path status (RTSP ffprobe) ==="
if ! ffprobe -v error -rtsp_transport tcp -timeout 3000000 \
  -show_entries stream=codec_name -of csv=p=0 \
  rtsp://127.0.0.1:8554/stairs_over_door 2>/dev/null; then
  docker run --rm --network camera-net alpine:3.20 \
    sh -c 'apk add --no-cache ffmpeg >/dev/null && ffprobe -v error -rtsp_transport tcp -timeout 3000000 -show_entries stream=codec_name -of csv=p=0 rtsp://mediamtx:8554/stairs_over_door' \
    2>/dev/null \
    || echo "(no stream yet — HA pseudo may be down; alwaysAvailable should still serve H264)"
fi

echo ""
echo "=== publisher check ==="
echo "HA should be the only RTMP publisher. alwaysAvailable fills brief gaps."
echo "Rapid 'closing existing publisher' during handoff is expected (make-before-break)."
echo "A third publisher IP fighting HA means something else is still publishing."

echo ""
echo "=== common failures ==="
echo "- Restart loop: old mediamtx.yml with readBufferCount or runOnInit (fixed in repo)"
echo "- Stale keepalive: docker rm -f mtx-keepalive"
echo "- Port in use:   ss -tlnp | grep -E '8554|1935'"
