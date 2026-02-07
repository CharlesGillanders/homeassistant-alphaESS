"""The AlphaEss integration."""
from __future__ import annotations

import asyncio
import ipaddress
import logging

import voluptuous as vol

from alphaess import alphaess

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant

import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_INVERTER_MODEL,
    CONF_IP_ADDRESS,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    PLATFORMS,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from .coordinator import AlphaESSDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

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


def _build_ip_address_map(entry: ConfigEntry) -> dict[str, str | None]:
    """Build a mapping of serial number to IP address from subentries."""
    ip_map: dict[str, str | None] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_INVERTER:
            serial = subentry.data.get(CONF_SERIAL_NUMBER)
            ip_addr = subentry.data.get(CONF_IP_ADDRESS, "")
            if serial:
                # Validate IP address
                if ip_addr and ip_addr != "0" and ip_addr.strip():
                    try:
                        ipaddress.ip_address(ip_addr)
                        ip_map[serial] = ip_addr
                    except ValueError:
                        ip_map[serial] = None
                else:
                    ip_map[serial] = None
    return ip_map


def _build_inverter_model_list(entry: ConfigEntry) -> list[str]:
    """Build a list of inverter models from subentries."""
    models = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_INVERTER:
            model = subentry.data.get(CONF_INVERTER_MODEL, "")
            if model:
                models.append(model)
    return models


def _has_inverter_subentries(entry: ConfigEntry) -> bool:
    """Check if entry has any inverter subentries."""
    return any(
        subentry.subentry_type == SUBENTRY_TYPE_INVERTER
        for subentry in entry.subentries.values()
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alpha ESS from a config entry."""

    verify_ssl = entry.options.get(
        "Verify SSL Certificate",
        entry.data.get("Verify SSL Certificate", True)
    )

    # Build per-inverter IP address mapping from subentries
    ip_address_map = _build_ip_address_map(entry)

    # Don't set a single IP on the client - the coordinator handles per-inverter IPs
    client = alphaess.alphaess(
        entry.data["AppID"],
        entry.data["AppSecret"],
        verify_ssl=verify_ssl
    )

    # Call getESSList to initialise the API client and discover systems
    # This is required before getdata() will work
    ess_list = await client.getESSList()

    # If no subentries exist (e.g. after migration from v1), auto-create them
    if not _has_inverter_subentries(entry) and ess_list:
        migrated_ip = entry.options.get("_migrated_ip", "")

        for idx, unit in enumerate(ess_list):
            serial = unit.get("sysSn")
            if not serial:
                continue

            model = unit.get("minv", "Unknown")
            # Assign migrated IP to the first inverter only
            ip_for_inverter = migrated_ip if idx == 0 and migrated_ip else ""

            subentry = ConfigSubentry(
                data={
                    CONF_SERIAL_NUMBER: serial,
                    CONF_INVERTER_MODEL: model,
                    CONF_IP_ADDRESS: ip_for_inverter,
                },
                subentry_type=SUBENTRY_TYPE_INVERTER,
                title=f"{model} ({serial})",
                unique_id=f"{SUBENTRY_TYPE_INVERTER}_{serial}",
            )
            hass.config_entries.async_add_subentry(entry, subentry)

        # Clear the temporary migrated IP from options
        if migrated_ip:
            hass.config_entries.async_update_entry(entry, options={})

        # Rebuild IP map now that subentries exist
        ip_address_map = _build_ip_address_map(entry)

    inverter_models = _build_inverter_model_list(entry)

    await asyncio.sleep(1)

    _coordinator = AlphaESSDataUpdateCoordinator(
        hass,
        client=client,
        ip_address_map=ip_address_map,
        inverter_models=inverter_models,
        entry=entry,
    )
    await _coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = _coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))

    # Register services (only once per domain)
    if not hass.services.has_service(DOMAIN, 'setbatterycharge'):
        async def async_battery_charge_handler(call):
            await client.updateChargeConfigInfo(
                call.data.get('serial'), call.data.get('chargestopsoc'),
                int(call.data.get('enabled') is True), call.data.get('cp1end'),
                call.data.get('cp2end'), call.data.get('cp1start'),
                call.data.get('cp2start')
            )

        async def async_battery_discharge_handler(call):
            await client.updateDisChargeConfigInfo(
                call.data.get('serial'), call.data.get('dischargecutoffsoc'),
                int(call.data.get('enabled') is True), call.data.get('dp1end'),
                call.data.get('dp2end'), call.data.get('dp1start'),
                call.data.get('dp2start')
            )

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


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry from version 1 to version 2."""
    _LOGGER.debug(
        "Migrating configuration from version %s",
        config_entry.version,
    )

    if config_entry.version > 2:
        return False

    if config_entry.version == 1:
        # Get old IP address from data or options
        old_ip = config_entry.options.get(
            "IPAddress",
            config_entry.data.get("IPAddress", "")
        )

        # Clean up entry data - remove IPAddress, keep credentials
        new_data = {
            "AppID": config_entry.data["AppID"],
            "AppSecret": config_entry.data["AppSecret"],
            "Verify SSL Certificate": config_entry.options.get(
                "Verify SSL Certificate",
                config_entry.data.get("Verify SSL Certificate", True)
            ),
        }

        # Store the old IP temporarily so async_setup_entry can assign it
        # to the first inverter when it auto-creates subentries
        new_options = {}
        if old_ip and old_ip != "0":
            new_options["_migrated_ip"] = old_ip

        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            options=new_options,
            version=2,
        )

        _LOGGER.info("Migration to version 2 successful")

    return True
