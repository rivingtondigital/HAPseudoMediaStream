"""Config flow for Pseudo Camera."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CAMERAS,
    CONF_FRAME_DIR,
    CONF_MEDIAMTX_HOST,
    CONF_MEDIAMTX_RTSP_PORT,
    CONF_PATH,
    CONF_SOURCE_ENTITY,
    CONF_WAKE_DELAY,
    DEFAULT_FRAME_DIR,
    DEFAULT_MEDIAMTX_HOST,
    DEFAULT_MEDIAMTX_RTSP_PORT,
    DEFAULT_WAKE_DELAY,
    DOMAIN,
)
from .frame_capture import async_capture_camera_frame, frame_path_for
from .options_flow import PseudoCameraOptionsFlowHandler


class PseudoCameraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pseudo Camera."""

    VERSION = 1

    def __init__(self) -> None:
        self._mediamtx_host = DEFAULT_MEDIAMTX_HOST
        self._mediamtx_rtsp_port = DEFAULT_MEDIAMTX_RTSP_PORT
        self._frame_dir = DEFAULT_FRAME_DIR
        self._cameras: list[dict[str, Any]] = []
        self._last_capture_ok = True
        self._last_capture_path = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return PseudoCameraOptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the MediaMTX server."""
        if user_input is not None:
            self._mediamtx_host = user_input[CONF_MEDIAMTX_HOST]
            self._mediamtx_rtsp_port = int(user_input[CONF_MEDIAMTX_RTSP_PORT])
            self._frame_dir = user_input[CONF_FRAME_DIR]
            return await self.async_step_camera()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MEDIAMTX_HOST, default=DEFAULT_MEDIAMTX_HOST): str,
                    vol.Required(
                        CONF_MEDIAMTX_RTSP_PORT, default=DEFAULT_MEDIAMTX_RTSP_PORT
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=65535,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(CONF_FRAME_DIR, default=DEFAULT_FRAME_DIR): str,
                }
            ),
        )

    async def async_step_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a camera mapping."""
        errors: dict[str, str] = {}

        if user_input is not None:
            path = user_input[CONF_PATH].strip().lower().replace(" ", "_")
            if not path:
                errors[CONF_PATH] = "invalid_path"
            elif any(camera[CONF_PATH] == path for camera in self._cameras):
                errors[CONF_PATH] = "path_exists"
            else:
                camera_config = {
                    CONF_PATH: path,
                    CONF_SOURCE_ENTITY: user_input[CONF_SOURCE_ENTITY],
                    CONF_WAKE_DELAY: int(user_input[CONF_WAKE_DELAY]),
                }
                self._cameras.append(camera_config)
                self._last_capture_path = path
                self._last_capture_ok = await async_capture_camera_frame(
                    self.hass,
                    camera_config[CONF_SOURCE_ENTITY],
                    frame_path_for(self._frame_dir, path),
                    camera_config[CONF_WAKE_DELAY],
                )
                return await self.async_step_camera_menu()

        return self.async_show_form(
            step_id="camera",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PATH): str,
                    vol.Required(CONF_SOURCE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="camera")
                    ),
                    vol.Required(CONF_WAKE_DELAY, default=DEFAULT_WAKE_DELAY): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=30,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_camera_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose whether to add another camera or finish setup."""
        if user_input is not None:
            if user_input["action"] == "add_camera":
                return await self.async_step_camera()
            return self.async_create_entry(
                title=f"Pseudo Camera ({self._mediamtx_host})",
                data={
                    CONF_MEDIAMTX_HOST: self._mediamtx_host,
                    CONF_MEDIAMTX_RTSP_PORT: self._mediamtx_rtsp_port,
                    CONF_FRAME_DIR: self._frame_dir,
                    CONF_CAMERAS: self._cameras,
                },
            )

        return self.async_show_form(
            step_id="camera_menu",
            description_placeholders={
                "count": str(len(self._cameras)),
                "path": self._last_capture_path,
                "capture_status": (
                    "Initial frame captured successfully."
                    if self._last_capture_ok
                    else "Initial frame capture failed; a gray fallback will be used until the first live relay ends."
                ),
            },
            data_schema=vol.Schema(
                {
                    vol.Required("action"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value="add_camera",
                                    label="Add another camera",
                                ),
                                selector.SelectOptionDict(
                                    value="finish",
                                    label="Finish setup",
                                ),
                            ],
                            custom_value=False,
                        )
                    )
                }
            ),
        )
