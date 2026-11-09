"""Config flow for the Epson EF Projector integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import EpsonApi, EpsonApiError
from .const import (
    CONF_POLL_INTERVAL,
    CONF_PORT,
    DEFAULT_NAME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
)


class Ef50ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> Ef50OptionsFlow:
        return Ef50OptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            api = EpsonApi(user_input[CONF_HOST], user_input[CONF_PORT])
            try:
                product = await api.get_product_name()
            except EpsonApiError:
                product = None
            finally:
                await api.close()

            if product is not None:
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME, DEFAULT_NAME), data=user_input
                )
            errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(
                    CONF_POLL_INTERVAL, default=15
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user point an existing entry at a new IP (DHCP changes)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            api = EpsonApi(user_input[CONF_HOST], user_input[CONF_PORT])
            try:
                product = await api.get_product_name()
            except EpsonApiError:
                product = None
            finally:
                await api.close()

            if product is not None:
                # Update the entry and let the update listener schedule the
                # reload (avoids a duplicate reload and matches HA's guidance).
                return self.async_update_and_abort(
                    entry, data_updates=user_input
                )
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST, default=entry.data.get(CONF_HOST)
                    ): str,
                    vol.Required(
                        CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)
                    ): int,
                }
            ),
            errors=errors,
        )


class Ef50OptionsFlow(config_entries.OptionsFlow):
    """Allow changing poll interval later."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        entry = self.config_entry
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        # Show the effective value: options win over the
                        # value captured when the entry was created.
                        default=entry.options.get(
                            CONF_POLL_INTERVAL,
                            entry.data.get(
                                CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                            ),
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300))
                }
            ),
        )
