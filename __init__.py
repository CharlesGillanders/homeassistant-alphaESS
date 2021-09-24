"""The Alpha ESS integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from datetime import timedelta
import logging 

import asyncio
from alphaess import alphaess

from .const import (
    DOMAIN,
    STARTUP_MESSAGE
)


PLATFORMS = ["sensor"]

SCAN_INTERVAL = timedelta(minutes=5)

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alpha ESS from a config entry."""

    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})
        _LOGGER.info(STARTUP_MESSAGE)

    client = alphaess.alphaess()
    client.username = entry.data.get("username")
    client.password = entry.data.get("password")
#    session = async_get_clientsession(hass)


    coordinator = AlphaESSDataUpdateCoordinator(hass, client=client)
    await coordinator.async_config_entry_first_refresh()

    if not coordinator.last_update_success:
        raise ConfigEntryNotReady


    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    hass.config_entries.async_setup_platforms(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    # for platform in PLATFORMS:
    #     if entry.options.get(platform, True):
    #         coordinator.platforms.append(platform)
    #         hass.async_add_job(
    #             hass.config_entries.async_forward_entry_setup(entry, platform)
    #         )

    #entry.add_update_listener(async_reload_entry)

    return True


class AlphaESSDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(
        self, hass: HomeAssistant, client: alphaess.alphaess
    ) -> None:
        """Initialize."""
        self.api = client
        self.platforms = ['sensor']
        self.update_method = self._async_update_data

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)

    async def _async_update_data(self):
        """Update data via library."""
        try:
            _LOGGER.info("Trying to query AlphaESS api data")
            return await self.api.getdata()
        except Exception as exception:
            raise UpdateFailed() from exception

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

# async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
#     """Handle removal of an entry."""
#     coordinator = hass.data[DOMAIN][entry.entry_id]
#     unloaded = all(
#         await asyncio.gather(
#             *[
#                 hass.config_entries.async_forward_entry_unload(entry, platform)
#                 for platform in PLATFORMS
#                 if platform in coordinator.platforms
#             ]
#         )
#     )
#     if unloaded:
#         hass.data[DOMAIN].pop(entry.entry_id)

#     return unloaded

# async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
#     """Reload config entry."""
#     await async_unload_entry(hass, entry)
#     await async_setup_entry(hass, entry)

async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)