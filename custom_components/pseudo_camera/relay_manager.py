"""Relay manager used by HA entities."""

from __future__ import annotations

import logging
from collections.abc import Callable

from .local_ffmpeg_backend import LocalFfmpegBackend
from .types import CameraConfig, PathStatus

_LOGGER = logging.getLogger(__name__)

StatusListener = Callable[[str, PathStatus], None]


class RelayManager:
    """High-level relay orchestration for configured cameras."""

    def __init__(
        self,
        mediamtx_host: str,
        mediamtx_rtsp_port: int,
        frame_dir: str,
        cameras: list[CameraConfig],
    ) -> None:
        self._backend = LocalFfmpegBackend(
            mediamtx_host=mediamtx_host,
            mediamtx_rtsp_port=mediamtx_rtsp_port,
            frame_dir=frame_dir,
        )
        self._cameras = {camera.path: camera for camera in cameras}

    @property
    def cameras(self) -> list[CameraConfig]:
        return list(self._cameras.values())

    def get_camera(self, path: str) -> CameraConfig | None:
        return self._cameras.get(path)

    def async_add_status_listener(self, listener: StatusListener) -> None:
        """Register a callback for path status changes."""
        self._backend.add_status_listener(listener)

    async def async_setup(self) -> None:
        """Initialize pseudo streams for all configured paths."""
        await self._backend.bootstrap_pseudo_streams(list(self._cameras))
        self._backend.start_watchdog()

    async def async_update_cameras(self, cameras: list[CameraConfig]) -> None:
        """Apply an updated camera list after config changes."""
        self._cameras = {camera.path: camera for camera in cameras}
        await self._backend.sync_paths(list(self._cameras))

    async def async_shutdown(self) -> None:
        """Stop all ffmpeg publishers."""
        await self._backend.shutdown()

    async def start_relay(self, path: str, hls_url: str) -> None:
        """Start live relay for a path."""
        if path not in self._cameras:
            raise ValueError(f"Unknown path: {path}")
        await self._backend.start_relay(path, hls_url)

    async def stop_relay(self, path: str) -> PathStatus:
        """Stop live relay and restore pseudo stream."""
        if path not in self._cameras:
            raise ValueError(f"Unknown path: {path}")
        return await self._backend.stop_relay(path)

    async def get_status(self, path: str) -> PathStatus:
        """Return runtime status for a path."""
        return await self._backend.get_status(path)
