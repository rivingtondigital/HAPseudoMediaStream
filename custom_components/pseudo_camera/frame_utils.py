"""Shared helpers for stored pseudo-camera frames."""

from __future__ import annotations

import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

MIN_FRAME_BYTES = 100


def is_valid_jpeg(path: Path) -> bool:
    """Return True if the file looks like a complete JPEG."""
    try:
        data = path.read_bytes()
    except OSError:
        return False

    if len(data) < MIN_FRAME_BYTES:
        return False
    if not data.startswith(b"\xff\xd8"):
        return False
    # EOI marker should appear near the end of a well-formed JPEG.
    if b"\xff\xd9" not in data[-65536:]:
        return False
    return True


def remove_invalid_frame(path: Path) -> None:
    """Delete a frame file if it exists but is not a valid JPEG."""
    if not path.is_file():
        return
    if is_valid_jpeg(path):
        return
    _LOGGER.warning("Removing invalid frame file %s", path)
    path.unlink(missing_ok=True)
