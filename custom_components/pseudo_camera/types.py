"""Typed configuration models for Pseudo Camera."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class CameraConfigDict(TypedDict):
    """Serialized camera mapping stored in config entry data."""

    path: str
    source_entity: str
    wake_delay: int


class PseudoCameraConfigDict(TypedDict):
    """Serialized config entry data."""

    mediamtx_host: str
    mediamtx_rtsp_port: int
    frame_dir: str
    cameras: list[CameraConfigDict]


@dataclass(slots=True)
class CameraConfig:
    """Runtime camera mapping."""

    path: str
    source_entity: str
    wake_delay: int = 3


@dataclass(slots=True)
class PathStatus:
    """Runtime status for a MediaMTX path."""

    path: str
    relay_active: bool
    pseudo_active: bool
    frame_path: str | None = None
    error: str | None = None


@dataclass(slots=True)
class IntegrationRuntimeData:
    """Objects stored in hass.data for a config entry."""

    relay_manager: object
    cameras: list[CameraConfig]
