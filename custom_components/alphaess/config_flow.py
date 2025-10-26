"""Config flow for AlphaEss integration."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from alphaess import alphaess
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, add_inverter_to_list, increment_inverter_count

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required("AppID", description="AppID"): str,
    vol.Required("AppSecret", description="AppSecret"): str,
    vol.Optional("IPAddress", default='0'): vol.Any(None, str),
    vol.Optional("Verify SSL Certificate", default=True): bool
})


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""

    client = alphaess.alphaess(data["AppID"], data["AppSecret"], ipaddress=data["IPAddress"], verify_ssl=data["Verify SSL Certificate"])

    try:
        await client.authenticate()
        await asyncio.sleep(1)
        ESSList = await client.getESSList()
        for unit in ESSList:
            if "sysSn" in unit:
                name = unit["minv"]
                add_inverter_to_list(name)
                increment_inverter_count()

        await asyncio.sleep(1)

    except aiohttp.ClientResponseError as e:
        if e.status == 401:
            raise InvalidAuth
        else:
            raise e
    except aiohttp.client_exceptions.ClientConnectorError:
        raise CannotConnect

    else:
        return {"AlphaESS": data["AppID"]}


class AlphaESSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Alpha ESS."""

    VERSION = 1

    async def async_step_user(
            self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""

        errors = {}

        if user_input:

            try:
                await validate_input(self.hass, user_input)

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"

            return self.async_create_entry(
                title=user_input["AppID"], data=user_input
            )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AlphaESSOptionsFlowHandler:
        return AlphaESSOptionsFlowHandler(config_entry)


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class CannotConnect(HomeAssistantError):
    """Error to indicate there is a problem connecting."""


class AlphaESSOptionsFlowHandler(config_entries.OptionsFlow):
    """AlphaESS options flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = {
            vol.Optional(
                "IPAddress",
                default=self._config_entry.options.get(
                    "IPAddress",
                    self._config_entry.data.get("IPAddress", ""),
                ),
            ): str,
            vol.Optional(
                "Verify SSL Certificate",
                default=self._config_entry.options.get(
                    "Verify SSL Certificate",
                    self._config_entry.data.get("Verify SSL Certificate", True),
                ),
            ): bool
        }

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
