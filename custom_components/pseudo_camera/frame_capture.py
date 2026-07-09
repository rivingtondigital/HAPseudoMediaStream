"""Capture initial frames from Home Assistant camera entities."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from homeassistant.components.camera import async_get_image, async_get_stream_source
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .local_ffmpeg_backend import FFMPEG_BIN

_LOGGER = logging.getLogger(__name__)

CAPTURE_TIMEOUT = 30
MIN_FRAME_BYTES = 100


def frame_path_for(frame_dir: str, path: str) -> Path:
    """Return the stored JPEG path for a MediaMTX path."""
    return Path(frame_dir) / f"{path}.jpg"


async def async_capture_initial_frames(
    hass: HomeAssistant,
    frame_dir: str,
    cameras: list,
) -> dict[str, bool]:
    """Capture initial frames for cameras that do not have one yet."""
    results: dict[str, bool] = {}
    for camera in cameras:
        output_path = frame_path_for(frame_dir, camera.path)
        if output_path.is_file() and output_path.stat().st_size >= MIN_FRAME_BYTES:
            results[camera.path] = True
            continue
        results[camera.path] = await async_capture_camera_frame(
            hass,
            camera.source_entity,
            output_path,
            camera.wake_delay,
        )
    return results


async def async_capture_camera_frame(
    hass: HomeAssistant,
    source_entity: str,
    output_path: Path,
    wake_delay: int = 3,
) -> bool:
    """Capture one frame from a camera entity and save it as a JPEG."""
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)

    if wake_delay > 0:
        _LOGGER.info(
            "Waiting %ss for %s to wake before capture",
            wake_delay,
            source_entity,
        )
        await asyncio.sleep(wake_delay)

    if await _capture_via_snapshot(hass, source_entity, output_path):
        return True

    if await _capture_via_stream(hass, source_entity, output_path):
        return True

    _LOGGER.warning(
        "Initial frame capture failed for %s; pseudo stream will use lavfi fallback",
        source_entity,
    )
    return False


async def _capture_via_snapshot(
    hass: HomeAssistant,
    source_entity: str,
    output_path: Path,
) -> bool:
    """Try a camera snapshot via the Camera integration API."""
    try:
        image = await async_get_image(hass, source_entity)
    except HomeAssistantError as err:
        _LOGGER.debug("Snapshot API failed for %s: %s", source_entity, err)
        return False
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Unexpected snapshot error for %s: %s", source_entity, err)
        return False

    if not image.content or len(image.content) < MIN_FRAME_BYTES:
        _LOGGER.debug("Snapshot for %s was empty", source_entity)
        return False

    await asyncio.to_thread(output_path.write_bytes, image.content)
    return _validate_frame(output_path)


async def _capture_via_stream(
    hass: HomeAssistant,
    source_entity: str,
    output_path: Path,
) -> bool:
    """Try grabbing one frame from the camera stream URL via ffmpeg."""
    try:
        stream_source = await async_get_stream_source(hass, source_entity)
    except HomeAssistantError as err:
        _LOGGER.debug("Stream source unavailable for %s: %s", source_entity, err)
        return False

    if not stream_source:
        _LOGGER.debug("No stream source for %s", source_entity)
        return False

    tmp_output = output_path.with_suffix(".tmp.jpg")
    cmd = [
        FFMPEG_BIN,
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file,http,https,tcp,tls,crypto,rtsp",
        "-i",
        stream_source,
        "-frames:v",
        "1",
        "-update",
        "1",
        "-y",
        str(tmp_output),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=CAPTURE_TIMEOUT,
        )
        if process.returncode != 0:
            _LOGGER.debug(
                "ffmpeg frame capture failed for %s: %s",
                source_entity,
                stderr.decode(errors="replace").strip(),
            )
            await asyncio.to_thread(tmp_output.unlink, missing_ok=True)
            return False

        await asyncio.to_thread(tmp_output.replace, output_path)
        return _validate_frame(output_path)
    except TimeoutError:
        _LOGGER.debug("ffmpeg frame capture timed out for %s", source_entity)
        return False


def _validate_frame(output_path: Path) -> bool:
    """Return True if the saved frame looks usable."""
    if not output_path.is_file():
        return False
    if output_path.stat().st_size < MIN_FRAME_BYTES:
        _LOGGER.debug("Captured frame too small: %s", output_path)
        return False
    _LOGGER.info("Captured initial frame at %s", output_path)
    return True
