"""Binary sensors for Pseudo Camera relay state."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device import camera_device_info
from .relay_manager import RelayManager
from .types import CameraConfig, IntegrationRuntimeData, PathStatus


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Pseudo Camera binary sensors."""
    runtime: IntegrationRuntimeData = hass.data[DOMAIN][entry.entry_id]
    relay_manager: RelayManager = runtime.relay_manager

    entities = [
        PseudoCameraRelayBinarySensor(entry, relay_manager, camera)
        for camera in relay_manager.cameras
    ]
    async_add_entities(entities)

    @callback
    def handle_status_update(path: str, status: PathStatus) -> None:
        for entity in entities:
            if entity.path == path:
                entity.async_set_status(status)

    relay_manager.async_add_status_listener(handle_status_update)


class PseudoCameraRelayBinarySensor(BinarySensorEntity):
    """Indicate whether a live relay is active for a path."""

    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        relay_manager: RelayManager,
        camera: CameraConfig,
    ) -> None:
        self._relay_manager = relay_manager
        self._camera = camera
        self._attr_unique_id = f"{entry.entry_id}_{camera.path}_relay"
        self._attr_name = "Live relay"
        self._attr_has_entity_name = True
        self._attr_is_on = False
        self._attr_device_info = camera_device_info(entry, camera.path)

    @property
    def path(self) -> str:
        """MediaMTX path for this sensor."""
        return self._camera.path

    async def async_added_to_hass(self) -> None:
        """Initialize state from the relay manager."""
        status = await self._relay_manager.get_status(self.path)
        self.async_set_status(status)

    @callback
    def async_set_status(self, status: PathStatus) -> None:
        """Update sensor state from relay status."""
        self._attr_is_on = status.relay_active
        self.async_write_ha_state()
