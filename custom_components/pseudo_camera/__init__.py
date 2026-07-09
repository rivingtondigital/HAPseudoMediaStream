"""The Pseudo Camera integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CAMERAS,
    CONF_FRAME_DIR,
    CONF_MEDIAMTX_HOST,
    CONF_MEDIAMTX_RTMP_PORT,
    CONF_MEDIAMTX_RTSP_PORT,
    CONF_PATH,
    CONF_SOURCE_ENTITY,
    CONF_WAKE_DELAY,
    DEFAULT_MEDIAMTX_RTMP_PORT,
    DOMAIN,
)
from .device import async_register_hub_device
from .frame_capture import async_capture_initial_frames
from .relay_manager import RelayManager
from .types import CameraConfig, IntegrationRuntimeData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.BINARY_SENSOR]


async def _async_restart_streams(hass: HomeAssistant) -> None:
    """Restart ffmpeg publishers for all config entries."""
    for runtime in hass.data.get(DOMAIN, {}).values():
        await runtime.relay_manager.async_restart_streams()


def _register_services(hass: HomeAssistant) -> None:
    """Register services once."""

    async def async_restart(_call) -> None:
        await _async_restart_streams(hass)

    hass.services.async_register(DOMAIN, "restart_streams", async_restart)


def _cameras_from_entry(entry: ConfigEntry) -> list[CameraConfig]:
    return [
        CameraConfig(
            path=camera[CONF_PATH],
            source_entity=camera[CONF_SOURCE_ENTITY],
            wake_delay=camera.get(CONF_WAKE_DELAY, 3),
        )
        for camera in entry.data[CONF_CAMERAS]
    ]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Pseudo Camera from a config entry."""
    cameras = _cameras_from_entry(entry)
    if not cameras:
        _LOGGER.error("Pseudo Camera entry has no camera mappings")
        return False

    _LOGGER.info(
        "Setting up Pseudo Camera for %s path(s) -> %s:%s",
        len(cameras),
        entry.data[CONF_MEDIAMTX_HOST],
        entry.data[CONF_MEDIAMTX_RTSP_PORT],
    )

    async_register_hub_device(hass, entry)

    capture_results = await async_capture_initial_frames(
        hass,
        entry.data[CONF_FRAME_DIR],
        cameras,
    )
    for path, captured in capture_results.items():
        if captured:
            _LOGGER.info("Initial frame ready for %s", path)
        else:
            _LOGGER.warning(
                "No initial frame for %s; pseudo stream will use lavfi until first relay ends",
                path,
            )

    mediamtx_host = entry.data[CONF_MEDIAMTX_HOST]
    mediamtx_rtsp_port = int(entry.data[CONF_MEDIAMTX_RTSP_PORT])
    mediamtx_rtmp_port = int(
        entry.data.get(CONF_MEDIAMTX_RTMP_PORT, DEFAULT_MEDIAMTX_RTMP_PORT)
    )
    _LOGGER.info(
        "Starting pseudo streams -> publish rtmp://%s:%s/<path>, read rtsp://%s:%s/<path>",
        mediamtx_host,
        mediamtx_rtmp_port,
        mediamtx_host,
        mediamtx_rtsp_port,
    )

    relay_manager = RelayManager(
        mediamtx_host=mediamtx_host,
        mediamtx_rtsp_port=mediamtx_rtsp_port,
        mediamtx_rtmp_port=mediamtx_rtmp_port,
        frame_dir=entry.data[CONF_FRAME_DIR],
        cameras=cameras,
    )
    try:
        await relay_manager.async_setup()
    except Exception:
        _LOGGER.exception("Failed to start pseudo camera ffmpeg publishers")
        return False

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = IntegrationRuntimeData(
        relay_manager=relay_manager,
        cameras=cameras,
    )

    if not hass.services.has_service(DOMAIN, "restart_streams"):
        _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.info("Pseudo Camera initialized with %s camera(s)", len(cameras))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime: IntegrationRuntimeData = hass.data[DOMAIN].pop(entry.entry_id)
        await runtime.relay_manager.async_shutdown()
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when camera mappings change."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
