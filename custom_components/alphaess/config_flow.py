"""Config flow for AlphaEss integration."""
from __future__ import annotations

from typing import Any

import aiohttp
from alphaess import alphaess
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_CLIENT_ID): str, vol.Required(CONF_CLIENT_SECRET): str}
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""

    client = alphaess.alphaess(data[CONF_CLIENT_ID], data[CONF_CLIENT_SECRET])

    try:
        await client.getESSList()

    except aiohttp.ClientResponseError as e:
        if e.status == 401:
            raise InvalidAuth
        else:
            raise e
    except aiohttp.client_exceptions.ClientConnectorError:
        raise CannotConnect

    else:
        return {"AlphaESS": data[CONF_CLIENT_ID]}


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
                    title=user_input[CONF_CLIENT_ID], data=user_input
            )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class CannotConnect(HomeAssistantError):
    """Error to indicate there is a problem connecting."""
