"""Config flow for Alpha ESS integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from alphaess import alphaess

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})

# class PlaceholderHub:
#     """Placeholder class to make tests pass.

#     TODO Remove this placeholder class and replace with things from your PyPI package.
#     """

#     def __init__(self, host: str) -> None:
#         """Initialize."""
#         self.host = host

#     async def authenticate(self, username: str, password: str) -> bool:
#         """Test if we can authenticate with the host."""
#         return True


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""

    client = alphaess.alphaess()

    
    if not await client.authenticate(data["username"],data["password"]):
        raise InvalidAuth 
    return {"AlphaESS": data["username"]}


class AlphaESSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Alpha ESS."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA 
            )

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(title=user_input["username"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
