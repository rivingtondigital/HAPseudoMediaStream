"""Device registry helpers for Pseudo Camera."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_MEDIAMTX_HOST, CONF_MEDIAMTX_RTSP_PORT, DOMAIN


def async_register_hub_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create the config-entry hub device referenced by via_device."""
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Pseudo Camera",
        model="MediaMTX",
    )


def camera_device_info(entry: ConfigEntry, path: str) -> DeviceInfo:
    """Return device info for a camera path."""
    host = entry.data[CONF_MEDIAMTX_HOST]
    port = entry.data[CONF_MEDIAMTX_RTSP_PORT]
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{path}")},
        name=f"Pseudo Camera {path}",
        manufacturer="Pseudo Camera",
        model="MediaMTX Relay",
        via_device=(DOMAIN, entry.entry_id),
        configuration_url=f"rtsp://{host}:{port}/{path}",
    )
