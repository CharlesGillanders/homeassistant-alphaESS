"""Coordinator for AlphaEss integration."""
import datetime
import json
import logging

import aiohttp
from alphaess import alphaess

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL

_LOGGER: logging.Logger = logging.getLogger(__package__)


class AlphaESSDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(self, hass: HomeAssistant, client: alphaess.alphaess) -> None:
        """Initialize."""
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = client
        self.update_method = self._async_update_data
        self.data: dict[str, dict[str, float]] = {}

    async def _async_update_data(self):
        """Update data via library."""
        try:
            jsondata: json = await self.api.getdata()
            for inverter in jsondata:
                #index = int(datetime.date.today().strftime("%d")) - 1
                inverterdata: dict[str, any] = {}
                inverterdata.update({"Model": inverter.get("minv")})
                _daily_stats = inverter.get("OneDayEnergy", {})

                # statistics
                inverterdata.update({"Solar Production": _daily_stats.get("epv")})
                inverterdata.update({"Solar to Battery": _daily_stats.get("eCharge")})
                inverterdata.update({"Solar to Grid": _daily_stats.get("eOutput")})
                inverterdata.update({"Solar to Load": _daily_stats.get("epv") - _daily_stats.get("eOutput") - _daily_stats.get("eCharge")})
                inverterdata.update({"Total Load": _daily_stats.get("eInput") + _daily_stats.get("epv") - _daily_stats.get("eCharge") + _daily_stats.get("eDischarge") - _daily_stats.get("eOutput") - _daily_stats.get("eGridCharge")})
                inverterdata.update({"Grid to Load": _daily_stats.get("eInput")})
                inverterdata.update({"Grid to Battery": _daily_stats.get("eGridCharge")})
                #inverterdata.update({"State of Charge": _daily_stats.get("Soc")})

                # system statistics
                #_sysstats = invertor.get("system_statistics", {})
                inverterdata.update({"Charge": _daily_stats.get("eCharge")})
                inverterdata.update({"Discharge": _daily_stats.get("eDischarge")})
                inverterdata.update({"EV Charger": _daily_stats.get("eChargingPile")})

                # powerdata
                _powerdata = inverter.get("RealTimePower", {})
                if _powerdata is None:
                    _powerdata = {
                        "ppv": 0,
                        "pload": 0,
                        "soc": 0,
                        "pgrid": 0,
                        "pbat": 0,
                        "pev": 0,
                    }
                _p_pv = _powerdata.get("ppv", 0)
                _p_load = _powerdata.get("pload", 0)
                _soc = _powerdata.get("soc", 0)  # unit?
                _p_grid = _powerdata.get("pgrid")
                _p_bat = _powerdata.get("pbat")
                _p_ev = _powerdata.get("pev")
                inverterdata.update({"Instantaneous PV power": _p_pv})
                inverterdata.update({"Instantaneous load power": _p_load})
                inverterdata.update({"Instantaneous battery soc": _soc})
                inverterdata.update({"Instantaneous grid power": _p_grid})
                inverterdata.update({"Instantaneous battery power": _p_bat})
                inverterdata.update({"Instantaneous EV power": _p_ev})
                #inverterdata.update({"Instantaneous Grid I/O Total": _l1 + _l2 + _l3})
                #inverterdata.update(
                #    {"Instantaneous Load": _ppv1 + _ppv2 + _dc + _bat + _l1 + _l2 + _l3}
                #)
                #inverterdata.update({"Instantaneous PPV1": _ppv1})
                #inverterdata.update({"Instantaneous PPV2": _ppv2})
                self.data.update({inverter["sysSn"]: inverterdata})
            return self.data
        except (
            aiohttp.client_exceptions.ClientConnectorError,
            aiohttp.ClientResponseError,
        ) as error:
            raise UpdateFailed(error) from error
