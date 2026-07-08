# Pseudo Camera

Home Assistant integration that relays Tapo battery camera streams to stable MediaMTX RTSP paths, with last-frame pseudo feeds when idle.

## What it does

- Creates one `media_player` sink per camera path for `camera.play_stream`
- Relays live HLS from a Tapo Control Direct camera entity to MediaMTX
- On stop, captures the last live frame and loops it as the pseudo stream
- Keeps NVR motion detection happy while the battery camera sleeps

## Project layout

```
pseudo_camera/
├── custom_components/pseudo_camera/   # HA integration
├── mediamtx.yml.example               # MediaMTX path template
├── mediamtx.py                        # Legacy single-camera MQTT prototype
└── README.md
```

## Build and install steps

### Phase 1: MediaMTX

1. Install and run [MediaMTX](https://github.com/bluenviron/mediamtx) on a host reachable from Home Assistant.
2. Copy `mediamtx.yml.example` and add one `paths:` entry per camera.
3. Confirm each path is publishable, e.g. `rtsp://<host>:8554/stairs`.

### Phase 2: Home Assistant prerequisites

1. Install **Tapo: Cameras Control** from HACS.
2. Add your Tapo C402 (or other battery camera) and use a **Direct** stream entity.
3. Enable **Use Stream from Home Assistant** on that camera entity. Required for `camera.play_stream`.
4. Install **ffmpeg** on the Home Assistant host.

### Phase 3: Install this integration

1. Copy `custom_components/pseudo_camera/` into your HA `config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration → Pseudo Camera**.
4. Configure:
   - MediaMTX host and RTSP port
   - Frame storage directory, e.g. `/config/pseudo_camera/frames`
   - First camera path and source entity

### Phase 4: Fallback frame

Before the first live session, pseudo mode needs an image:

```bash
# On the HA host, create a neutral fallback frame
mkdir -p /config/pseudo_camera/frames
ffmpeg -f lavfi -i color=c=gray:s=1280x720 -frames:v 1 /config/pseudo_camera/frames/default.jpg
```

After the first relay ends, each path uses its own `<path>.jpg` last-frame file.

### Phase 5: Automations

Start relay when motion/input turns on:

```yaml
alias: "Stairs: Start relay"
mode: single
triggers:
  - trigger: state
    entity_id: input_boolean.stairs
    to: "on"
actions:
  - delay: "00:00:03"
  - action: camera.play_stream
    target:
      entity_id: camera.tapo_stairs_sd_stream_direct
    data:
      media_player: media_player.mediamtx_stairs
      format: hls
```

Stop relay when motion/input turns off:

```yaml
alias: "Stairs: Stop relay"
mode: single
triggers:
  - trigger: state
    entity_id: input_boolean.stairs
    to: "off"
actions:
  - action: media_player.media_stop
    target:
      entity_id: media_player.mediamtx_stairs
```

Repeat per camera with different path names and entities.

### Phase 6: NVR / Generic Camera

Point consumers at the stable MediaMTX URLs:

```
rtsp://<mediamtx-host>:8554/stairs
rtsp://<mediamtx-host>:8554/south
```

These URLs never change. Content switches between live relay and last-frame pseudo automatically.

## Managing multiple cameras

During initial setup you can add multiple cameras before finishing.

After installation:

1. Go to **Settings → Devices & Services → Pseudo Camera → Configure**
2. Choose **Add camera** or **Remove camera**
3. The integration reloads automatically and creates entities for the new path

Each camera gets:

| Entity | Example |
|--------|---------|
| Media player sink | `media_player.mediamtx_stairs` |
| Live relay sensor | `binary_sensor.stairs_live_relay` |
| Stable RTSP URL | `rtsp://<host>:8554/stairs` |

## Current status

| Done | Planned |
|------|---------|
| Core ffmpeg relay manager | HACS release packaging |
| Last-frame capture on stop | Tests |
| Media player sink per path | Reconfigure MediaMTX host via options |
| Options flow for add/remove cameras | |
| Live relay binary sensor per path | |
| Watchdog auto-restart for ffmpeg | |
| Multi-camera initial config flow | |

## Development roadmap

1. **Now**: install and test one camera end-to-end
2. **Next**: options flow for additional camera mappings
3. **Then**: health sensors and auto-restart if ffmpeg exits unexpectedly
4. **Later**: HACS repo + CI tests

## Legacy prototype

`mediamtx.py` is the original single-camera MQTT proof of concept using direct Tapo RTSP. It is superseded by this integration for battery cameras and multi-path setups.
