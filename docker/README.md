# Docker networking for pseudo_camera

## The pattern

When you have **multiple independent compose stacks** (HA, Frigate, Scrypted, MediaMTX), they don't share a network automatically. The standard approach:

1. **One stack creates the network** with a fixed `name:`
2. **Other stacks join it** with `external: true`

This repo's `docker-compose.yaml` creates `camera-net`. Start it first.

```bash
docker compose up -d
```

## Joining from your other stacks

Add `camera-net` to each service that needs RTSP access. Keep your existing `default` network so intra-stack communication (e.g. HA ↔ Mosquitto) still works.

### Home Assistant

```yaml
services:
  homeassistant:
    # ... image, volumes, environment, etc. ...
    networks:
      - default
      - camera-net

networks:
  camera-net:
    external: true
    name: camera-net
```

HA Pseudo Camera integration:

| Field | Value |
|-------|-------|
| MediaMTX host | `mediamtx` |
| RTSP port | `8554` |

### Frigate

```yaml
services:
  frigate:
    # ... existing config ...
    networks:
      - default
      - camera-net

networks:
  camera-net:
    external: true
    name: camera-net
```

Frigate input:

```yaml
cameras:
  stairs:
    ffmpeg:
      inputs:
        - path: rtsp://mediamtx:8554/stairs
          input_args: preset-rtsp-restream
```

### Scrypted

```yaml
services:
  scrypted:
    # ... existing config ...
    networks:
      - default
      - camera-net

networks:
  camera-net:
    external: true
    name: camera-net
```

RTSP URL in Scrypted: `rtsp://mediamtx:8554/stairs`

## Why `name: camera-net` matters

Without a fixed name, Compose prefixes the network with the project name (e.g. `pseudo_camera_camera-net`). Other stacks wouldn't find it.

```yaml
# Creator stack (this repo's docker-compose.yaml)
networks:
  camera-net:
    name: camera-net      # exact name on the host
    driver: bridge

# Joiner stacks (HA, Frigate, Scrypted)
networks:
  camera-net:
    external: true
    name: camera-net      # must match
```

## Alternative: create the network manually

If you prefer no stack to "own" the network:

```bash
docker network create camera-net
```

Then mark it external in **every** stack, including MediaMTX:

```yaml
networks:
  camera-net:
    external: true
    name: camera-net
```

Both approaches are valid. This repo uses the "MediaMTX stack creates it" approach so `docker compose up -d` is a one-step start.

## Verify

```bash
# Network exists
docker network inspect camera-net

# MediaMTX is on it
docker inspect mediamtx --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'

# From HA container
docker exec -it <ha-container> nc -zv mediamtx 8554

# From Frigate container
docker exec -it <frigate-container> nc -zv mediamtx 8554

# From Scrypted container
docker exec -it <scrypted-container> nc -zv mediamtx 8554
```

## Host IP fallback

Port `8554` is published to the host, so LAN clients can also use:

```text
rtsp://<docker-host-ip>:8554/stairs
```

Containers on `camera-net` should prefer `rtsp://mediamtx:8554/stairs` — it doesn't depend on host IP and works across reboots.
