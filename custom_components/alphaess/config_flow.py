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
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_EV_CHARGER_MODEL,
    CONF_INVERTER_MODEL,
    CONF_IP_ADDRESS,
    CONF_PARENT_INVERTER,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required("AppID", description="AppID"): str,
    vol.Required("AppSecret", description="AppSecret"): str,
    vol.Optional("Verify SSL Certificate", default=True): bool
})

INVERTER_RECONFIGURE_SCHEMA = vol.Schema({
    vol.Optional(CONF_IP_ADDRESS, default=""): str,
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
        """Return subentries supported by this integration."""
        return {
            SUBENTRY_TYPE_INVERTER: InverterSubentryFlowHandler,
            SUBENTRY_TYPE_EV_CHARGER: EVChargerSubentryFlowHandler,
        }

    async def async_step_user(
            self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
                subentries: list[ConfigSubentryData] = []

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
    ) -> ConfigFlowResult:
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


class InverterSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for adding and modifying an inverter."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """User flow to add a new inverter."""
        errors: dict[str, str] = {}

        if user_input is not None:
            serial = user_input[CONF_SERIAL_NUMBER]
            unique_id = f"{SUBENTRY_TYPE_INVERTER}_{serial}"

            for existing_subentry in self._get_entry().subentries.values():
                if existing_subentry.unique_id == unique_id:
                    errors[CONF_SERIAL_NUMBER] = "already_configured"

            if not errors:
                return self.async_create_entry(
                    title=f"Inverter ({serial})",
                    data=user_input,
                    unique_id=unique_id,
                )

        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema({
                vol.Required(CONF_SERIAL_NUMBER): str,
                vol.Optional(CONF_INVERTER_MODEL, default=""): str,
                vol.Optional(CONF_IP_ADDRESS, default=""): str,
            }),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing inverter (edit IP address)."""
        subconfig_entry = self._get_reconfigure_subentry()

        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subconfig_entry,
                data_updates=user_input,
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                INVERTER_RECONFIGURE_SCHEMA,
                subconfig_entry.data,
            ),
            description_placeholders={
                CONF_SERIAL_NUMBER: subconfig_entry.data[CONF_SERIAL_NUMBER]
            },
        )


class EVChargerSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for adding and modifying an EV charger."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """User flow to add a new EV charger."""
        errors: dict[str, str] = {}

        if user_input is not None:
            serial = user_input[CONF_SERIAL_NUMBER]
            unique_id = f"{SUBENTRY_TYPE_EV_CHARGER}_{serial}"

            for existing_subentry in self._get_entry().subentries.values():
                if existing_subentry.unique_id == unique_id:
                    errors[CONF_SERIAL_NUMBER] = "already_configured"

            if not errors:
                return self.async_create_entry(
                    title=f"EV Charger ({serial})",
                    data=user_input,
                    unique_id=unique_id,
                )

        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema({
                vol.Required(CONF_SERIAL_NUMBER): str,
                vol.Optional(CONF_EV_CHARGER_MODEL, default=""): str,
                vol.Required(CONF_PARENT_INVERTER): str,
            }),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing EV charger."""
        subconfig_entry = self._get_reconfigure_subentry()

        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subconfig_entry,
                data_updates=user_input,
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({
                    vol.Optional(CONF_EV_CHARGER_MODEL, default=""): str,
                    vol.Required(CONF_PARENT_INVERTER): str,
                }),
                subconfig_entry.data,
            ),
            description_placeholders={
                CONF_SERIAL_NUMBER: subconfig_entry.data[CONF_SERIAL_NUMBER]
            },
        )
