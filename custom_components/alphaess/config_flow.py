"""Config flow for AlphaEss integration."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
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

_LOGGER = logging.getLogger(__name__)


async def _notify(hass: HomeAssistant, message: str, title: str = "AlphaESS") -> None:
    """Create a persistent notification."""
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {"title": title, "message": message},
        blocking=True,
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
            await self.async_set_unique_id(user_input["AppID"])
            self._abort_if_unique_id_configured()

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
            ): bool,
        }

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))


class AlphaESSInverterSubentryFlowHandler(ConfigSubentryFlow):
    """Handle inverter subentry flow for bind/unbind."""

    def __init__(self) -> None:
        """Initialize the subentry flow."""
        super().__init__()
        self._sysSn: str | None = None

    def _get_api(self):
        """Get the API client from the coordinator."""
        entry = self._get_entry()
        coordinator = self.hass.data[DOMAIN][entry.entry_id]
        return coordinator.api

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 1: Ask for serial number and check code to request verification."""
        errors = {}

        if user_input is not None:
            sys_sn = user_input["serial_number"].strip()
            check_code = user_input["check_code"].strip()

            try:
                api = self._get_api()
                result = await api.getVerificationCode(sys_sn, check_code)
                _LOGGER.info(
                    "Requested verification code for %s - Result: %s",
                    sys_sn, result,
                )
            except Exception as e:
                _LOGGER.error("Failed to get verification code for %s: %s", sys_sn, e)
                errors["base"] = "verification_request_failed"
                await _notify(self.hass, f"Failed to request verification code for {sys_sn}: {e}", "AlphaESS Bind Failed")
            else:
                if result is None:
                    errors["base"] = "verification_request_failed"
                    await _notify(self.hass, f"Failed to request verification code for {sys_sn}. Please check the serial number and check code.", "AlphaESS Bind Failed")
                else:
                    await _notify(self.hass, f"Verification code requested for {sys_sn}. Please check your email or phone for the code.", "AlphaESS Verification Sent")
                    self._sysSn = sys_sn
                    return await self.async_step_verify()

        schema = vol.Schema({
            vol.Required("serial_number"): str,
            vol.Required("check_code"): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2: Ask for verification code and bind the system."""
        errors = {}

        if user_input is not None:
            code = user_input["verification_code"].strip()

            try:
                api = self._get_api()
                result = await api.bindSn(self._sysSn, code)
                _LOGGER.info(
                    "Bind system %s - Result: %s",
                    self._sysSn, result,
                )
            except Exception as e:
                _LOGGER.error("Failed to bind system %s: %s", self._sysSn, e)
                errors["base"] = "bind_failed"
                await _notify(self.hass, f"Failed to bind inverter {self._sysSn}: {e}", "AlphaESS Bind Failed")
            else:
                if result is None:
                    errors["base"] = "bind_failed"
                    await _notify(self.hass, f"Failed to bind inverter {self._sysSn}. Please check the verification code.", "AlphaESS Bind Failed")
                else:
                    await _notify(self.hass, f"Inverter {self._sysSn} has been successfully bound to your account. The integration will reload to discover the new system.", "AlphaESS Bind Successful")

                    # Schedule reload so the new system is discovered
                    entry = self._get_entry()
                    self.hass.async_create_task(
                        self.hass.config_entries.async_reload(entry.entry_id)
                    )

                    return self.async_create_entry(
                        title=f"Inverter ({self._sysSn})",
                        data={
                            CONF_SERIAL_NUMBER: self._sysSn,
                            CONF_INVERTER_MODEL: "Unknown",
                            CONF_IP_ADDRESS: "",
                            CONF_DISABLE_NOTIFICATIONS: True,
                        },
                    )

        schema = vol.Schema({
            vol.Required("verification_code"): str,
        })

        return self.async_show_form(
            step_id="verify",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "serial_number": self._sysSn or "",
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguration (IP, notifications) and unbinding of an inverter."""
        subentry = self._get_reconfigure_subentry()
        serial = subentry.data.get(CONF_SERIAL_NUMBER, "")
        errors = {}

        if user_input is not None:
            # If unbind is requested, handle that first
            if user_input.get("confirm_unbind"):
                try:
                    api = self._get_api()
                    result = await api.unBindSn(serial)
                    _LOGGER.info(
                        "Unbind system %s - Result: %s",
                        serial, result,
                    )
                except Exception as e:
                    _LOGGER.error("Failed to unbind system %s: %s", serial, e)
                    errors["base"] = "unbind_failed"
                    await _notify(self.hass, f"Failed to unbind inverter {serial}: {e}", "AlphaESS Unbind Failed")
                else:
                    if result is None:
                        errors["base"] = "unbind_failed"
                        await _notify(self.hass, f"Failed to unbind inverter {serial}. Please try again.", "AlphaESS Unbind Failed")
                    else:
                        await _notify(self.hass, f"Inverter {serial} has been successfully unbound from your account. The integration will reload.", "AlphaESS Unbind Successful")

                        entry = self._get_entry()
                        self.hass.async_create_task(
                            self.hass.config_entries.async_reload(entry.entry_id)
                        )

                        return self.async_remove_and_abort(
                            self._get_entry(),
                            subentry,
                        )
            else:
                # Save IP address and notification settings
                ip = (user_input.get(CONF_IP_ADDRESS) or "").strip()
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
                            CONF_IP_ADDRESS: ip,
                            CONF_DISABLE_NOTIFICATIONS: user_input.get(CONF_DISABLE_NOTIFICATIONS, True),
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
            vol.Optional("confirm_unbind", default=False): bool,
        })

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "serial_number": serial,
            },
        )
