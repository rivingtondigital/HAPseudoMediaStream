"""Relay backend protocol."""

from __future__ import annotations

from typing import Protocol

from .types import PathStatus


class RelayBackend(Protocol):
    """Interface for per-path ffmpeg relay management."""

    async def start_pseudo(self, path: str, image_path: str) -> None:
        """Publish a looping still image to the MediaMTX path."""

    async def start_relay(self, path: str, hls_url: str) -> None:
        """Publish a live HLS stream to the MediaMTX path."""

    async def stop_relay(self, path: str) -> PathStatus:
        """Stop live relay, capture last frame, and return to pseudo."""

    async def shutdown(self) -> None:
        """Stop all ffmpeg processes."""

    async def get_status(self, path: str) -> PathStatus:
        """Return current status for a path."""
