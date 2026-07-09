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

from .const import DEFAULT_MEDIAMTX_RTMP_PORT
from .frame_utils import async_is_valid_jpeg, async_remove_invalid_frame
from .stream_utils import ffmpeg_stream_input_args
from .types import PathStatus

_LOGGER = logging.getLogger(__name__)

FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
CAPTURE_TIMEOUT = 15
LAST_FRAME_CAPTURE_TIMEOUT = 5
PROCESS_STOP_TIMEOUT = 10
WATCHDOG_INTERVAL = 30
PUBLISH_SETTLE_DELAY = 0.5
PUBLISHER_VERIFY_DELAY = 0.75
HANDOFF_GAP = 0.25

# Shared output profile keeps Frigate's decoder stable across pseudo/relay swaps.
OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
OUTPUT_FPS = 10
OUTPUT_GOP = OUTPUT_FPS

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
    stopping_pseudo: bool = False
    stopping_relay: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LocalFfmpegBackend:
    """Manage ffmpeg publishers for MediaMTX paths."""

    def __init__(
        self,
        mediamtx_host: str,
        mediamtx_rtsp_port: int,
        frame_dir: str,
        mediamtx_rtmp_port: int = DEFAULT_MEDIAMTX_RTMP_PORT,
        default_frame: str | None = None,
    ) -> None:
        self._host = mediamtx_host
        self._rtsp_port = mediamtx_rtsp_port
        self._rtmp_port = mediamtx_rtmp_port
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
                await self._stop_relay(state)
                await self._stop_pseudo(state)
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

    def _rtsp_read_url(self, path: str) -> str:
        """RTSP URL for reading from MediaMTX (frame capture, consumers)."""
        return f"rtsp://{self._host}:{self._rtsp_port}/{path}"

    def _rtmp_publish_url(self, path: str) -> str:
        """RTMP URL for publishing to MediaMTX (more stable than RTSP publish)."""
        return f"rtmp://{self._host}:{self._rtmp_port}/{path}"

    def _frame_path(self, path: str) -> Path:
        return self._frame_dir / f"{path}.jpg"

    async def _async_captured_frame(self, path: str) -> str | None:
        """Return a saved last-frame image for a path, if valid."""
        frame = self._frame_path(path)
        await async_remove_invalid_frame(frame)
        if await async_is_valid_jpeg(frame):
            return str(frame)
        return None

    async def ensure_frame_dir(self) -> None:
        """Create the frame storage directory."""
        await asyncio.to_thread(self._frame_dir.mkdir, parents=True, exist_ok=True)

    async def bootstrap_pseudo_streams(self, paths: list[str]) -> None:
        """Start pseudo publishers for all configured paths."""
        _LOGGER.info(
            "Using ffmpeg binary: %s (publish rtmp://%s:%s/<path>, read rtsp://%s:%s/<path>)",
            FFMPEG_BIN,
            self._host,
            self._rtmp_port,
            self._host,
            self._rtsp_port,
        )
        await self.ensure_frame_dir()
        for path in paths:
            self.register_path(path)
            await self.start_pseudo(path, fresh=True)
            status = await self.get_status(path)
            _LOGGER.info(
                "Path %s bootstrap: pseudo_active=%s relay_active=%s",
                path,
                status.pseudo_active,
                status.relay_active,
            )

    async def start_pseudo(self, path: str, *, fresh: bool = False) -> None:
        """Publish pseudo stream (last captured frame or gray lavfi fallback)."""
        state = self._paths[path]
        image_path = await self._async_captured_frame(path)
        async with state.lock:
            if not fresh:
                await self._stop_relay(state)
                await self._stop_pseudo(state)
                await asyncio.sleep(PUBLISH_SETTLE_DELAY)

            started = await self._start_pseudo_unlocked(
                state, image_path, settle=False, stop_existing=not fresh
            )
            if not started and image_path:
                _LOGGER.warning(
                    "Pseudo loop failed for %s using %s; falling back to lavfi",
                    path,
                    image_path,
                )
                await async_remove_invalid_frame(Path(image_path))
                started = await self._start_pseudo_unlocked(
                    state, None, settle=False, stop_existing=False
                )

            if not started:
                _LOGGER.error("Failed to start pseudo stream for %s", path)
            else:
                source = state.frame_path or "lavfi gray"
                _LOGGER.info("Started pseudo stream for %s using %s", path, source)
        self._notify(path)

    async def start_relay(self, path: str, hls_url: str) -> None:
        """Publish a live HLS stream to the MediaMTX path."""
        state = self._paths[path]
        async with state.lock:
            await self._stop_relay(state)
            # Stop pseudo before relay so Frigate gets a clean RTSP reconnect
            # instead of a mid-stream codec change on the same session.
            await self._stop_pseudo(state)
            await asyncio.sleep(HANDOFF_GAP)

            cmd = [
                FFMPEG_BIN,
                "-hide_banner",
                "-loglevel",
                "error",
                *ffmpeg_stream_input_args(hls_url),
                "-fflags",
                "+genpts",
                "-re",
                "-i",
                hls_url,
                *self._relay_video_args(),
                "-max_muxing_queue_size",
                "1024",
                "-f",
                "flv",
                self._rtmp_publish_url(path),
            ]
            state.relay_process = await self._spawn(cmd, f"relay:{path}")
            if not await self._verify_process(state.relay_process, f"relay:{path}"):
                await self._stop_relay(state)
                await self._restore_pseudo(state, None)
                raise RuntimeError(f"Relay ffmpeg exited immediately for {path}")
            self._monitor_relay(state)
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
                captured = await self._capture_last_frame(
                    path, timeout=LAST_FRAME_CAPTURE_TIMEOUT
                )
                if captured:
                    state.frame_path = str(captured)

                image_path = await self._async_captured_frame(path)
                await self._stop_relay(state)
                await asyncio.sleep(HANDOFF_GAP)
                started = await self._restore_pseudo(state, image_path)
                if not started:
                    _LOGGER.error("Failed to restore pseudo stream for %s", path)
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
                await self._stop_relay(state)
                await self._stop_pseudo(state)
                state.intended_mode = "pseudo"
                state.last_hls_url = None

    async def get_status(self, path: str) -> PathStatus:
        """Return current status for a path."""
        state = self._paths[path]
        async with state.lock:
            return self._status(state)

    async def _restore_pseudo(self, state: _PathState, image_path: str | None = None) -> bool:
        """Start pseudo, falling back to lavfi gray if needed."""
        started = await self._start_pseudo_unlocked(
            state,
            image_path,
            settle=False,
            stop_existing=False,
        )
        if started or not image_path:
            return started

        await async_remove_invalid_frame(Path(image_path))
        return await self._start_pseudo_unlocked(
            state, None, settle=False, stop_existing=False
        )

    async def _start_pseudo_unlocked(
        self,
        state: _PathState,
        image_path: str | None = None,
        *,
        settle: bool = True,
        stop_existing: bool = True,
    ) -> bool:
        if image_path is None:
            image_path = await self._async_captured_frame(state.path)

        if stop_existing:
            await self._stop_pseudo(state)
        if settle:
            await asyncio.sleep(PUBLISH_SETTLE_DELAY)

        cmd = self._pseudo_command(state.path, image_path)
        state.pseudo_process = await self._spawn(cmd, f"pseudo:{state.path}")
        started = await self._verify_process(state.pseudo_process, f"pseudo:{state.path}")
        if not started:
            await self._stop_pseudo(state)
            return False

        self._monitor_pseudo(state)
        state.frame_path = image_path
        state.intended_mode = "pseudo"
        state.last_hls_url = None
        return True

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
                await self._stop_relay(state)
                await asyncio.sleep(HANDOFF_GAP)
                await self._restore_pseudo(state, None)
            elif not status.pseudo_active:
                _LOGGER.warning("Pseudo stream for %s exited; restarting", path)
                await self._restore_pseudo(state, None)
        self._notify(path)

    async def _capture_last_frame(
        self, path: str, *, timeout: int = CAPTURE_TIMEOUT
    ) -> Path | None:
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
            self._rtsp_read_url(path),
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
                timeout=timeout,
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
            if not await async_is_valid_jpeg(output):
                _LOGGER.warning("Last-frame capture produced invalid JPEG for %s", path)
                await asyncio.to_thread(output.unlink, missing_ok=True)
                return None
            _LOGGER.info("Captured last frame for %s at %s", path, output)
            return output
        except TimeoutError:
            _LOGGER.warning("Frame capture timed out for %s", path)
            return None

    def _output_scale_filter(self, *, with_fps: bool = True) -> str:
        scale = (
            f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2"
        )
        if with_fps:
            return f"{scale},fps={OUTPUT_FPS}"
        return scale

    def _output_video_args(self, *, static: bool) -> list[str]:
        """Shared H.264 profile for pseudo and relay publishers."""
        args = [
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "baseline",
            "-preset",
            "ultrafast",
            "-bf",
            "0",
            "-sc_threshold",
            "0",
            "-r",
            str(OUTPUT_FPS),
            "-vsync",
            "cfr",
            "-g",
            str(OUTPUT_GOP),
            "-keyint_min",
            str(OUTPUT_GOP),
        ]
        if static:
            args.extend(["-tune", "stillimage", "-b:v", "600k"])
        else:
            args.extend(
                [
                    "-b:v",
                    "1500k",
                    "-force_key_frames",
                    "expr:gte(t,n_forced*1)",
                ]
            )
        return args

    def _relay_video_args(self) -> list[str]:
        return ["-vf", self._output_scale_filter(), *self._output_video_args(static=False)]

    def _pseudo_command(self, path: str, image_path: str | None) -> list[str]:
        cmd = [
            FFMPEG_BIN,
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
        ]
        if image_path:
            cmd.extend(
                [
                    "-framerate",
                    str(OUTPUT_FPS),
                    "-loop",
                    "1",
                    "-i",
                    image_path,
                    "-vf",
                    self._output_scale_filter(),
                ]
            )
        else:
            cmd.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=gray:s={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:r={OUTPUT_FPS}",
                ]
            )
        cmd.extend(
            [
                *self._output_video_args(static=True),
                "-max_muxing_queue_size",
                "1024",
                "-f",
                "flv",
                self._rtmp_publish_url(path),
            ]
        )
        return cmd

    async def _spawn(
        self, cmd: list[str], label: str
    ) -> asyncio.subprocess.Process:
        _LOGGER.info("Starting %s: %s", label, " ".join(cmd))
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    async def _verify_process(
        self, process: asyncio.subprocess.Process, label: str
    ) -> bool:
        await asyncio.sleep(PUBLISHER_VERIFY_DELAY)
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
            return False
        return True

    def _monitor_pseudo(self, state: _PathState) -> None:
        process = state.pseudo_process
        if process is None:
            return
        asyncio.create_task(self._monitor_process(process, f"pseudo:{state.path}", "pseudo", state))

    def _monitor_relay(self, state: _PathState) -> None:
        process = state.relay_process
        if process is None:
            return
        asyncio.create_task(self._monitor_process(process, f"relay:{state.path}", "relay", state))

    async def _monitor_process(
        self,
        process: asyncio.subprocess.Process,
        label: str,
        kind: Literal["pseudo", "relay"],
        state: _PathState,
    ) -> None:
        if process.stderr is None:
            returncode = await process.wait()
            stderr_text = ""
        else:
            stderr = await process.stderr.read()
            returncode = await process.wait()
            stderr_text = stderr.decode(errors="replace").strip()

        intentional = state.stopping_pseudo if kind == "pseudo" else state.stopping_relay
        active = state.pseudo_process if kind == "pseudo" else state.relay_process
        if active is not process:
            return

        if kind == "pseudo":
            state.pseudo_process = None
        else:
            state.relay_process = None

        if intentional:
            if returncode not in (0, -15, 255):
                _LOGGER.debug("%s stopped (code %s)", label, returncode)
            return

        if returncode != 0:
            _LOGGER.warning(
                "%s exited unexpectedly (code %s): %s",
                label,
                returncode,
                stderr_text,
            )

        if state.intended_mode != kind:
            return

        _LOGGER.info("Restarting %s for %s after publisher exit", kind, state.path)
        async with state.lock:
            if state.intended_mode != kind:
                return
            await asyncio.sleep(HANDOFF_GAP)
            if kind == "pseudo":
                await self._restore_pseudo(state, None)
            else:
                _LOGGER.warning(
                    "Relay for %s ended; restoring pseudo stream",
                    state.path,
                )
                await self._restore_pseudo(state, None)
                state.intended_mode = "pseudo"
                state.last_hls_url = None
        self._notify(state.path)

    async def _stop_pseudo(self, state: _PathState) -> None:
        state.stopping_pseudo = True
        try:
            await self._stop_process(state.pseudo_process)
        finally:
            state.stopping_pseudo = False
            state.pseudo_process = None

    async def _stop_relay(self, state: _PathState) -> None:
        state.stopping_relay = True
        try:
            await self._stop_process(state.relay_process)
        finally:
            state.stopping_relay = False
            state.relay_process = None

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
