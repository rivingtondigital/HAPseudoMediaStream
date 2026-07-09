"""Media player sink for camera.play_stream."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_FRAME_PATH,
    ATTR_RELAY_ACTIVE,
    ATTR_SOURCE_ENTITY,
    DOMAIN,
)
from .stream_utils import ha_stream_needs_auth
from .relay_manager import RelayManager
from .types import CameraConfig, IntegrationRuntimeData, PathStatus

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Pseudo Camera media players."""
    runtime: IntegrationRuntimeData = hass.data[DOMAIN][entry.entry_id]
    relay_manager: RelayManager = runtime.relay_manager

    entities = [
        PseudoCameraMediaPlayer(entry, relay_manager, camera)
        for camera in relay_manager.cameras
    ]
    async_add_entities(entities)

    @callback
    def handle_status_update(path: str, status: PathStatus) -> None:
        for entity in entities:
            if entity.path == path:
                entity.async_set_status(status)

    relay_manager.async_add_status_listener(handle_status_update)


class PseudoCameraMediaPlayer(MediaPlayerEntity):
    """Receive camera.play_stream HLS and relay it to MediaMTX."""

    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.STOP
    )
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        relay_manager: RelayManager,
        camera: CameraConfig,
    ) -> None:
        self._entry = entry
        self._relay_manager = relay_manager
        self._camera = camera
        self._attr_unique_id = f"{entry.entry_id}_{camera.path}"
        self._attr_name = f"MediaMTX {camera.path}"
        self._attr_has_entity_name = True
        self._attr_device_info = camera_device_info(entry, camera.path)
        self._relay_active = False
        self._frame_path: str | None = None

    @property
    def path(self) -> str:
        """MediaMTX path for this sink."""
        return self._camera.path

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity attributes."""
        return {
            ATTR_SOURCE_ENTITY: self._camera.source_entity,
            ATTR_RELAY_ACTIVE: self._relay_active,
            ATTR_FRAME_PATH: self._frame_path,
        }

    @property
    def state(self) -> MediaPlayerState:
        """Return PLAYING while relay is active."""
        if self._relay_active:
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    async def async_added_to_hass(self) -> None:
        """Initialize state from the relay manager."""
        status = await self._relay_manager.get_status(self.path)
        self.async_set_status(status)

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """Start relaying the provided HLS playlist to MediaMTX."""
        _LOGGER.info(
            "Relay play_media for %s (type=%s): %s",
            self.path,
            media_type,
            media_id,
        )
        access_token = None
        if ha_stream_needs_auth(media_id):
            access_token = self.hass.auth.async_create_access_token(expire_hours=1)
        try:
            await self._relay_manager.start_relay(
                self.path, media_id, access_token=access_token
            )
        except Exception:
            _LOGGER.exception("Failed to start relay for %s", self.path)
            raise

    async def async_media_stop(self) -> None:
        """Stop relay and restore pseudo stream."""
        if not self._relay_active:
            return
        _LOGGER.info("Stopping relay for %s", self.path)
        self._relay_active = False
        self.async_write_ha_state()
        await self._relay_manager.stop_relay(self.path)

    @callback
    def async_set_status(self, status: PathStatus) -> None:
        """Update entity state from relay status."""
        self._relay_active = status.relay_active
        self._frame_path = status.frame_path
        self.async_write_ha_state()
