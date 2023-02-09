"""The AlphaEss integration."""
from __future__ import annotations

import voluptuous as vol

from alphaess import alphaess

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import AlphaESSDataUpdateCoordinator

SERVICE_BATTERY_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Required('serial'): cv.string,
        vol.Required('enabled'): cv.boolean,
        vol.Required('cp1start'): cv.string,
        vol.Required('cp1end'): cv.string,
        vol.Required('cp2start'): cv.string,
        vol.Required('cp2end'): cv.string,
        vol.Required('chargestopsoc'): cv.positive_int,
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
        vol.Required('dischargecutoffsoc'): cv.positive_int,
    }
)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alpha ESS from a config entry."""
    

    client = alphaess.alphaess()
    client.username = entry.data[CONF_USERNAME]
    client.password = entry.data[CONF_PASSWORD]

    coordinator = AlphaESSDataUpdateCoordinator(hass, client=client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))
    
    async def async_battery_charge_handler(call):
        await client.setbatterycharge(call.data.get('serial'), call.data.get('enabled'), call.data.get('cp1start'), call.data.get('cp1end'), call.data.get('cp2start'), call.data.get('cp2end'), call.data.get('chargestopsoc'))
    
    async def async_battery_discharge_handler(call):
        await client.setbatterydischarge(call.data.get('serial'), call.data.get('enabled'), call.data.get('dp1start'), call.data.get('dp1end'), call.data.get('dp2start'), call.data.get('dp2end'), call.data.get('dischargecutoffsoc'))

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
