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
    CONF_MEDIAMTX_RTSP_PORT,
    CONF_PATH,
    CONF_SOURCE_ENTITY,
    CONF_WAKE_DELAY,
    DOMAIN,
)
from .relay_manager import RelayManager
from .types import CameraConfig, IntegrationRuntimeData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.BINARY_SENSOR]


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

    relay_manager = RelayManager(
        mediamtx_host=entry.data[CONF_MEDIAMTX_HOST],
        mediamtx_rtsp_port=entry.data[CONF_MEDIAMTX_RTSP_PORT],
        frame_dir=entry.data[CONF_FRAME_DIR],
        cameras=cameras,
    )
    await relay_manager.async_setup()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = IntegrationRuntimeData(
        relay_manager=relay_manager,
        cameras=cameras,
    )

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
