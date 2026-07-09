"""Helpers for reading Home Assistant stream URLs with ffmpeg."""

from __future__ import annotations


def ha_stream_needs_auth(url: str) -> bool:
    """Return True if the URL is an authenticated Home Assistant API stream."""
    return url.startswith(("http://", "https://")) and "/api/" in url


def ffmpeg_stream_input_args(url: str, access_token: str | None = None) -> list[str]:
    """Return ffmpeg input args for HTTP/HLS streams, including HA auth when needed."""
    args = [
        "-protocol_whitelist",
        "file,http,https,tcp,tls,crypto,rtsp",
    ]
    if access_token and ha_stream_needs_auth(url):
        args.extend(["-headers", f"Authorization: Bearer {access_token}\r\n"])
    return args
