"""The AlphaEss integration."""
from __future__ import annotations

import asyncio
import ipaddress

import voluptuous as vol

from alphaess import alphaess

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

import homeassistant.helpers.config_validation as cv

from .const import DEFAULT_POST_REQUEST_RESTRICTION, DOMAIN, PLATFORMS, add_inverter_to_list, increment_inverter_count
from .coordinator import AlphaESSDataUpdateCoordinator

SERVICE_BATTERY_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Required('serial'): cv.string,
        vol.Required('enabled'): cv.boolean,
        vol.Required('cp1start'): cv.string,
        vol.Required('cp1end'): cv.string,
        vol.Required('cp2start'): cv.string,
        vol.Required('cp2end'): cv.string,
        vol.Required('chargestopsoc'): vol.All(cv.positive_int, vol.Range(min=0, max=100)),
    }
)

SERVICE_BATTERY_DISCHARGE_SCHEMA = vol.Schema(
    {
        vol.Required('serial'): cv.string,
        vol.Required('enabled'): cv.boolean,
        vol.Required('dp1start'): cv.string,
        vol.Required('dp1end'): cv.string,
        vol.Required('dp2start'): cv.string,
        vol.Required('dp2end'): cv.string,
        vol.Required('dischargecutoffsoc'): vol.All(cv.positive_int, vol.Range(min=0, max=100)),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alpha ESS from a config entry."""

    ip_address = (entry.options.get("IPAddress", entry.data.get("IPAddress")) or "").strip()

    # Validate IP address - normalize blank/"0" to None
    if ip_address and ip_address != "0":
        try:
            ipaddress.ip_address(ip_address)
        except ValueError:
            ip_address = None
    else:
        ip_address = None

    verify_ssl = entry.options.get("Verify SSL Certificate", entry.data.get("Verify SSL Certificate"))

    client = alphaess.alphaess(entry.data["AppID"], entry.data["AppSecret"], ipaddress=ip_address, verify_ssl=verify_ssl)

    ess_list = await client.getESSList()
    for unit in ess_list:
        if "sysSn" in unit:
            name = unit["minv"]
            add_inverter_to_list(name)
            increment_inverter_count()

    await asyncio.sleep(1)

    _coordinator = AlphaESSDataUpdateCoordinator(hass, client=client, post_request_restriction=DEFAULT_POST_REQUEST_RESTRICTION)
    await _coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = _coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))

    async def async_battery_charge_handler(call):
        await client.updateChargeConfigInfo(call.data.get('serial'), call.data.get('chargestopsoc'),
                                            int(call.data.get('enabled') is True), call.data.get('cp1end'),
                                            call.data.get('cp2end'), call.data.get('cp1start'),
                                            call.data.get('cp2start'))

    async def async_battery_discharge_handler(call):
        await client.updateDisChargeConfigInfo(call.data.get('serial'), call.data.get('dischargecutoffsoc'),
                                               int(call.data.get('enabled') is True), call.data.get('dp1end'),
                                               call.data.get('dp2end'), call.data.get('dp1start'),
                                               call.data.get('dp2start'))

    hass.services.async_register(
        DOMAIN, 'setbatterycharge', async_battery_charge_handler, SERVICE_BATTERY_CHARGE_SCHEMA)

    hass.services.async_register(
        DOMAIN, 'setbatterydischarge', async_battery_discharge_handler, SERVICE_BATTERY_DISCHARGE_SCHEMA)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
