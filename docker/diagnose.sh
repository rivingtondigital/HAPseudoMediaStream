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
echo "=== keepalive last log lines ==="
docker logs mtx-keepalive 2>&1 | tail -10 || echo "(keepalive container missing)"

echo ""
echo "=== path status (API) ==="
curl -sf http://127.0.0.1:9997/v3/paths/get/stairs_over_door 2>/dev/null \
  || docker exec mediamtx wget -qO- http://127.0.0.1:9997/v3/paths/get/stairs_over_door 2>/dev/null \
  || echo "(API unreachable — is mediamtx running?)"

echo ""
echo "=== common failures ==="
echo "- Restart loop: old mediamtx.yml with readBufferCount or runOnInit (fixed in repo)"
echo "- Stale image:   docker compose down && docker rmi pseudo_camera-mediamtx 2>/dev/null; docker compose up -d --build"
echo "- Port in use:   ss -tlnp | grep -E '8554|1935'"
