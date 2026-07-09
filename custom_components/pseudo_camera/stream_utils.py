"""Helpers for reading Home Assistant stream URLs with ffmpeg."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from yarl import URL

STREAM_URL_TTL = timedelta(hours=1)


def ha_stream_needs_auth(url: str) -> bool:
    """Return True if the URL is an authenticated Home Assistant API stream."""
    return url.startswith(("http://", "https://")) and "/api/" in url


def prepare_ha_stream_url(hass: HomeAssistant, url: str) -> str:
    """Return a URL ffmpeg can read, signing HA API URLs when needed."""
    if not ha_stream_needs_auth(url):
        return url

    parsed = URL(url)
    path = str(parsed.path)
    if parsed.query_string:
        path = f"{path}?{parsed.query_string}"

    signed = async_sign_path(
        hass,
        path,
        STREAM_URL_TTL,
        use_content_user=True,
    )
    return f"{parsed.origin()}{signed}"


def ffmpeg_stream_input_args(url: str) -> list[str]:
    """Return ffmpeg input args for HTTP/HLS streams."""
    return [
        "-protocol_whitelist",
        "file,http,https,tcp,tls,crypto,rtsp",
    ]
