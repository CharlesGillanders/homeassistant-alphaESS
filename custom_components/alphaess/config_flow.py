"""Config flow for AlphaEss integration."""
from __future__ import annotations

import asyncio
import ipaddress
from typing import Any

import aiohttp
from alphaess import alphaess
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_DISABLE_NOTIFICATIONS,
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

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry types supported by this integration."""
        return {SUBENTRY_TYPE_INVERTER: AlphaESSInverterSubentryFlowHandler}

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
                            CONF_DISABLE_NOTIFICATIONS: True,
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


class AlphaESSInverterSubentryFlowHandler(ConfigSubentryFlow):
    """Handle inverter subentry flow for reconfiguration."""

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguration of an inverter subentry."""
        subentry = self._get_reconfigure_subentry()
        errors = {}

        if user_input is not None:
            ip = (user_input.get(CONF_IP_ADDRESS) or "").strip()
            user_input[CONF_IP_ADDRESS] = ip
            if ip and ip != "0":
                try:
                    ipaddress.ip_address(ip)
                except ValueError:
                    errors["base"] = "invalid_ip"

            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    data={
                        **subentry.data,
                        CONF_IP_ADDRESS: user_input[CONF_IP_ADDRESS],
                        CONF_DISABLE_NOTIFICATIONS: user_input[CONF_DISABLE_NOTIFICATIONS],
                    },
                )

        schema = vol.Schema({
            vol.Optional(
                CONF_IP_ADDRESS,
                default=subentry.data.get(CONF_IP_ADDRESS, ""),
            ): str,
            vol.Optional(
                CONF_DISABLE_NOTIFICATIONS,
                default=subentry.data.get(CONF_DISABLE_NOTIFICATIONS, True),
            ): bool,
        })

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "serial_number": subentry.data.get(CONF_SERIAL_NUMBER, ""),
            },
        )
