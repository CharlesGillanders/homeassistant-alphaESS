"""Config flow for AlphaEss integration."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from alphaess import alphaess
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_INVERTER_MODEL,
    CONF_IP_ADDRESS,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    SUBENTRY_TYPE_INVERTER,
)

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required("AppID", description="AppID"): str,
    vol.Required("AppSecret", description="AppSecret"): str,
    vol.Optional("Verify SSL Certificate", default=True): bool
})

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input and return discovered systems."""

    client = alphaess.alphaess(
        data["AppID"], data["AppSecret"],
        verify_ssl=data.get("Verify SSL Certificate", True)
    )

    try:
        await client.authenticate()
        await asyncio.sleep(1)
        ess_list = await client.getESSList()
        await asyncio.sleep(1)

    except aiohttp.ClientResponseError as e:
        if e.status == 401:
            raise InvalidAuth
        raise e
    except aiohttp.client_exceptions.ClientConnectorError:
        raise CannotConnect

    return {"title": data["AppID"], "ess_list": ess_list or []}


class AlphaESSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Alpha ESS."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> AlphaESSOptionsFlowHandler:
        return AlphaESSOptionsFlowHandler(config_entry)

    async def async_step_user(
            self, user_input: dict[str, Any] | None = None
    ):
        """Handle the initial step."""

        errors = {}

        if user_input:
            try:
                result = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            else:
                # Build subentries for discovered inverters and EV chargers
                subentries = []

                for unit in result["ess_list"]:
                    serial = unit.get("sysSn")
                    if not serial:
                        continue

                    model = unit.get("minv", "Unknown")

                    subentries.append({
                        "subentry_type": SUBENTRY_TYPE_INVERTER,
                        "title": f"{model} ({serial})",
                        "unique_id": f"{SUBENTRY_TYPE_INVERTER}_{serial}",
                        "data": {
                            CONF_SERIAL_NUMBER: serial,
                            CONF_INVERTER_MODEL: model,
                            CONF_IP_ADDRESS: "",
                        },
                    })

                return self.async_create_entry(
                    title=user_input["AppID"],
                    data={
                        "AppID": user_input["AppID"],
                        "AppSecret": user_input["AppSecret"],
                        "Verify SSL Certificate": user_input.get("Verify SSL Certificate", True),
                    },
                    subentries=subentries,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "open_api_url": "https://open.alphaess.com/",
                "issues_url": "https://github.com/CharlesGillanders/homeassistant-alphaESS?tab=readme-ov-file#issues-with-registering-systems-to-the-alphaess-openapi",
            },
        )


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class CannotConnect(HomeAssistantError):
    """Error to indicate there is a problem connecting."""


class AlphaESSOptionsFlowHandler(OptionsFlow):
    """AlphaESS options flow."""

    def __init__(self, config_entry: ConfigEntry):
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = {
            vol.Optional(
                "Verify SSL Certificate",
                default=self._config_entry.options.get(
                    "Verify SSL Certificate",
                    self._config_entry.data.get("Verify SSL Certificate", True),
                ),
            ): bool
        }

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
