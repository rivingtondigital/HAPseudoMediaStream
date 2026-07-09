"""Local ffmpeg-based relay backend."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .types import PathStatus

_LOGGER = logging.getLogger(__name__)

FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
CAPTURE_TIMEOUT = 15
PROCESS_STOP_TIMEOUT = 10
WATCHDOG_INTERVAL = 30

IntendedMode = Literal["pseudo", "relay"]
StatusListener = Callable[[str, PathStatus], None]


@dataclass
class _PathState:
    path: str
    relay_process: asyncio.subprocess.Process | None = None
    pseudo_process: asyncio.subprocess.Process | None = None
    frame_path: str | None = None
    intended_mode: IntendedMode = "pseudo"
    last_hls_url: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LocalFfmpegBackend:
    """Manage ffmpeg publishers for MediaMTX paths."""

    def __init__(
        self,
        mediamtx_host: str,
        mediamtx_rtsp_port: int,
        frame_dir: str,
        default_frame: str | None = None,
    ) -> None:
        self._host = mediamtx_host
        self._port = mediamtx_rtsp_port
        self._frame_dir = Path(frame_dir)
        self._default_frame = default_frame or str(self._frame_dir / "default.jpg")
        self._paths: dict[str, _PathState] = {}
        self._listeners: list[StatusListener] = []
        self._watchdog_task: asyncio.Task | None = None

    def add_status_listener(self, listener: StatusListener) -> None:
        """Register a callback for path status changes."""
        self._listeners.append(listener)

    def start_watchdog(self) -> None:
        """Start periodic process health checks."""
        if self._watchdog_task is None:
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop_watchdog(self) -> None:
        """Stop periodic process health checks."""
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None

    async def sync_paths(self, paths: list[str]) -> None:
        """Register configured paths and stop any removed paths."""
        path_set = set(paths)
        for path in paths:
            self.register_path(path)

        for path, state in list(self._paths.items()):
            if path in path_set:
                continue
            async with state.lock:
                await self._stop_process(state.relay_process)
                await self._stop_process(state.pseudo_process)
                state.relay_process = None
                state.pseudo_process = None
            del self._paths[path]

        for path in paths:
            status = await self.get_status(path)
            if not status.relay_active and not status.pseudo_active:
                await self.start_pseudo(path)

    def register_path(self, path: str) -> None:
        """Register a MediaMTX path for management."""
        if path not in self._paths:
            self._paths[path] = _PathState(path=path)

    def _notify(self, path: str) -> None:
        status = self._status(self._paths[path])
        for listener in self._listeners:
            listener(path, status)

    def _rtsp_url(self, path: str) -> str:
        return f"rtsp://{self._host}:{self._port}/{path}"

    def _frame_path(self, path: str) -> Path:
        return self._frame_dir / f"{path}.jpg"

    def _captured_frame(self, path: str) -> str | None:
        """Return a saved last-frame image for a path, if valid."""
        frame = self._frame_path(path)
        if frame.is_file() and frame.stat().st_size > 100:
            return str(frame)
        return None

    async def ensure_frame_dir(self) -> None:
        """Create the frame storage directory."""
        await asyncio.to_thread(self._frame_dir.mkdir, parents=True, exist_ok=True)

    async def ensure_default_frame(self) -> None:
        """Create a fallback frame if none exists yet."""
        await self.ensure_frame_dir()
        default = Path(self._default_frame)
        if default.is_file() and default.stat().st_size > 0:
            return

        cmd = [
            FFMPEG_BIN,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=1280x720",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(default),
        ]
        _LOGGER.info("Creating default pseudo frame at %s", default)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            _LOGGER.error(
                "Failed to create default frame %s: %s",
                default,
                stderr.decode(errors="replace").strip(),
            )

    async def bootstrap_pseudo_streams(self, paths: list[str]) -> None:
        """Start pseudo publishers for all configured paths."""
        _LOGGER.info("Using ffmpeg binary: %s", FFMPEG_BIN)
        await self.ensure_frame_dir()
        for path in paths:
            self.register_path(path)
            await self.start_pseudo(path)
            status = await self.get_status(path)
            _LOGGER.info(
                "Path %s bootstrap: pseudo_active=%s relay_active=%s",
                path,
                status.pseudo_active,
                status.relay_active,
            )

    async def start_pseudo(self, path: str) -> None:
        """Publish pseudo stream (last captured frame or gray lavfi fallback)."""
        state = self._paths[path]
        image_path = self._captured_frame(path)
        async with state.lock:
            await self._stop_process(state.pseudo_process)
            await self._stop_process(state.relay_process)
            state.relay_process = None

            cmd = self._pseudo_command(path, image_path)
            state.pseudo_process = await self._spawn(cmd, f"pseudo:{path}")
            await self._verify_process(state.pseudo_process, f"pseudo:{path}")
            state.frame_path = image_path
            state.intended_mode = "pseudo"
            state.last_hls_url = None
            source = image_path or "lavfi gray"
            _LOGGER.info("Started pseudo stream for %s using %s", path, source)
        self._notify(path)

    async def start_relay(self, path: str, hls_url: str) -> None:
        """Publish a live HLS stream to the MediaMTX path."""
        state = self._paths[path]
        async with state.lock:
            await self._stop_process(state.pseudo_process)
            state.pseudo_process = None
            await self._stop_process(state.relay_process)

            cmd = [
                FFMPEG_BIN,
                "-hide_banner",
                "-loglevel",
                "error",
                "-re",
                "-i",
                hls_url,
                "-c",
                "copy",
                "-f",
                "rtsp",
                "-rtsp_transport",
                "tcp",
                self._rtsp_url(path),
            ]
            state.relay_process = await self._spawn(cmd, f"relay:{path}")
            await self._verify_process(state.relay_process, f"relay:{path}")
            state.intended_mode = "relay"
            state.last_hls_url = hls_url
            _LOGGER.info("Started relay for %s", path)
        self._notify(path)

    async def stop_relay(self, path: str) -> PathStatus:
        """Stop live relay, capture last frame, and return to pseudo."""
        state = self._paths[path]
        async with state.lock:
            if state.relay_process is None:
                status = self._status(state)
            else:
                captured = await self._capture_last_frame(path)
                if captured:
                    state.frame_path = str(captured)

                await self._stop_process(state.relay_process)
                state.relay_process = None

                await self._start_pseudo_unlocked(state)
                state.intended_mode = "pseudo"
                state.last_hls_url = None
                status = self._status(state)
        self._notify(path)
        return status

    async def shutdown(self) -> None:
        """Stop all ffmpeg processes."""
        await self.stop_watchdog()
        for state in self._paths.values():
            async with state.lock:
                await self._stop_process(state.relay_process)
                await self._stop_process(state.pseudo_process)
                state.relay_process = None
                state.pseudo_process = None
                state.intended_mode = "pseudo"
                state.last_hls_url = None

    async def get_status(self, path: str) -> PathStatus:
        """Return current status for a path."""
        state = self._paths[path]
        async with state.lock:
            return self._status(state)

    async def _start_pseudo_unlocked(self, state: _PathState) -> None:
        image_path = self._captured_frame(state.path)
        await self._stop_process(state.pseudo_process)

        cmd = self._pseudo_command(state.path, image_path)
        state.pseudo_process = await self._spawn(cmd, f"pseudo:{state.path}")
        await self._verify_process(state.pseudo_process, f"pseudo:{state.path}")
        state.frame_path = image_path
        state.intended_mode = "pseudo"

    async def _watchdog_loop(self) -> None:
        """Restart ffmpeg publishers that exit unexpectedly."""
        try:
            while True:
                await asyncio.sleep(WATCHDOG_INTERVAL)
                for path, state in list(self._paths.items()):
                    await self._check_path_health(path, state)
        except asyncio.CancelledError:
            raise

    async def _check_path_health(self, path: str, state: _PathState) -> None:
        async with state.lock:
            status = self._status(state)
            if state.intended_mode == "relay":
                if status.relay_active:
                    return
                _LOGGER.warning(
                    "Relay for %s exited unexpectedly; restoring pseudo stream",
                    path,
                )
                state.relay_process = None
                await self._start_pseudo_unlocked(state)
                state.intended_mode = "pseudo"
                state.last_hls_url = None
            elif not status.pseudo_active:
                _LOGGER.warning("Pseudo stream for %s exited; restarting", path)
                await self._start_pseudo_unlocked(state)
        self._notify(path)

    async def _capture_last_frame(self, path: str) -> Path | None:
        """Grab one frame from the live MediaMTX path before stopping relay."""
        await self.ensure_frame_dir()
        output = self._frame_path(path)
        tmp_output = output.with_suffix(".tmp.jpg")

        cmd = [
            FFMPEG_BIN,
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            self._rtsp_url(path),
            "-frames:v",
            "1",
            "-update",
            "1",
            "-q:v",
            "2",
            "-y",
            str(tmp_output),
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=CAPTURE_TIMEOUT,
            )
            if process.returncode != 0:
                _LOGGER.warning(
                    "Frame capture failed for %s: %s",
                    path,
                    stderr.decode(errors="replace").strip(),
                )
                await asyncio.to_thread(tmp_output.unlink, missing_ok=True)
                return None

            await asyncio.to_thread(tmp_output.replace, output)
            _LOGGER.info("Captured last frame for %s at %s", path, output)
            return output
        except TimeoutError:
            _LOGGER.warning("Frame capture timed out for %s", path)
            return None

    def _pseudo_command(self, path: str, image_path: str | None) -> list[str]:
        cmd = [
            FFMPEG_BIN,
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
        ]
        if image_path:
            cmd.extend(["-loop", "1", "-i", image_path])
        else:
            cmd.extend(["-f", "lavfi", "-i", "color=c=gray:s=1280x720:r=5"])
        cmd.extend(
            [
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "ultrafast",
                "-b:v",
                "600k",
                "-g",
                "25",
                "-max_muxing_queue_size",
                "1024",
                "-f",
                "rtsp",
                "-rtsp_transport",
                "tcp",
                self._rtsp_url(path),
            ]
        )
        return cmd

    async def _spawn(
        self, cmd: list[str], label: str
    ) -> asyncio.subprocess.Process:
        _LOGGER.info("Starting %s: %s", label, " ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        asyncio.create_task(self._monitor_process(process, label))
        return process

    async def _verify_process(
        self, process: asyncio.subprocess.Process, label: str
    ) -> None:
        await asyncio.sleep(0.5)
        if process.returncode is not None:
            stderr = b""
            if process.stderr is not None:
                stderr = await process.stderr.read()
            _LOGGER.error(
                "%s exited immediately (code %s): %s",
                label,
                process.returncode,
                stderr.decode(errors="replace").strip(),
            )

    async def _monitor_process(
        self, process: asyncio.subprocess.Process, label: str
    ) -> None:
        if process.stderr is None:
            await process.wait()
            return
        stderr = await process.stderr.read()
        returncode = await process.wait()
        if returncode != 0:
            _LOGGER.error(
                "%s exited (code %s): %s",
                label,
                returncode,
                stderr.decode(errors="replace").strip(),
            )

    async def _stop_process(self, process: asyncio.subprocess.Process | None) -> None:
        if process is None:
            return
        if process.returncode is not None:
            return

        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        try:
            await asyncio.wait_for(process.wait(), timeout=PROCESS_STOP_TIMEOUT)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            await process.wait()

    def _status(self, state: _PathState) -> PathStatus:
        relay_running = state.relay_process is not None and state.relay_process.returncode is None
        pseudo_running = (
            state.pseudo_process is not None and state.pseudo_process.returncode is None
        )
        return PathStatus(
            path=state.path,
            relay_active=relay_running,
            pseudo_active=pseudo_running,
            frame_path=state.frame_path,
        )
