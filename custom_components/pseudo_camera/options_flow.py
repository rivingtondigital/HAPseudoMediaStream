"""Options flow for Pseudo Camera."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.helpers import selector

from .const import (
    CONF_CAMERAS,
    CONF_PATH,
    CONF_SOURCE_ENTITY,
    CONF_WAKE_DELAY,
    DEFAULT_WAKE_DELAY,
    DOMAIN,
)


class PseudoCameraOptionsFlowHandler(OptionsFlow):
    """Handle options for Pseudo Camera."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage camera mappings."""
        if user_input is not None:
            action = user_input["action"]
            if action == "add_camera":
                return await self.async_step_add_camera()
            return await self.async_step_remove_camera()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("action"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value="add_camera",
                                    label="Add camera",
                                ),
                                selector.SelectOptionDict(
                                    value="remove_camera",
                                    label="Remove camera",
                                ),
                            ],
                            custom_value=False,
                        )
                    )
                }
            ),
        )

    async def async_step_add_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add another camera mapping."""
        errors: dict[str, str] = {}
        cameras = list(self.config_entry.data[CONF_CAMERAS])

        if user_input is not None:
            path = user_input[CONF_PATH].strip().lower().replace(" ", "_")
            if not path:
                errors[CONF_PATH] = "invalid_path"
            elif any(camera[CONF_PATH] == path for camera in cameras):
                errors[CONF_PATH] = "path_exists"
            else:
                cameras.append(
                    {
                        CONF_PATH: path,
                        CONF_SOURCE_ENTITY: user_input[CONF_SOURCE_ENTITY],
                        CONF_WAKE_DELAY: user_input[CONF_WAKE_DELAY],
                    }
                )
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_CAMERAS: cameras},
                )
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="add_camera",
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

    async def async_step_remove_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a camera mapping."""
        cameras = list(self.config_entry.data[CONF_CAMERAS])

        if not cameras:
            return self.async_abort(reason="no_cameras")

        if user_input is not None:
            path = user_input[CONF_PATH]
            updated = [camera for camera in cameras if camera[CONF_PATH] != path]
            if len(updated) == len(cameras):
                return self.async_abort(reason="camera_not_found")
            if not updated:
                return self.async_abort(reason="last_camera")

            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_CAMERAS: updated},
            )
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="remove_camera",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PATH): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=camera[CONF_PATH],
                                    label=camera[CONF_PATH],
                                )
                                for camera in cameras
                            ],
                            custom_value=False,
                        )
                    )
                }
            ),
        )
