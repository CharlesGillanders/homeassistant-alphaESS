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
            for invertor in jsondata:
                index = int(datetime.date.today().strftime("%d")) - 1
                inverterdata: dict[str, any] = {}
                inverterdata.update({"Model": invertor.get("minv")})
                _stats = invertor.get("statistics", {})

                # statistics
                inverterdata.update({"Solar Production": _stats.get("EpvT")})
                inverterdata.update({"Solar to Battery": _stats.get("Epvcharge")})
                inverterdata.update({"Solar to Grid": _stats.get("Eout")})
                inverterdata.update({"Solar to Load": _stats.get("Epv2load")})
                inverterdata.update({"Total Load": _stats.get("EHomeLoad")})
                inverterdata.update({"Grid to Load": _stats.get("EGrid2Load")})
                inverterdata.update({"Grid to Battery": _stats.get("EGridCharge")})
                inverterdata.update({"State of Charge": _stats.get("Soc")})

                # system statistics
                _sysstats = invertor.get("system_statistics", {})
                inverterdata.update({"Charge": _sysstats.get("ECharge", [])[index]})
                inverterdata.update(
                    {"Discharge": _sysstats.get("EDischarge", [])[index]}
                )
                inverterdata.update({"EV Charger": _stats.get("EChargingPile")})

                # powerdata
                _powerdata = invertor.get("powerdata", {})
                if _powerdata is None:
                    _powerdata = {
                        "pmeter_l1": 0,
                        "pmeter_l2": 0,
                        "pmeter_l3": 0,
                        "ppv1": 0,
                        "ppv2": 0,
                        "pbat": 0,
                        "soc": 0,
                        "pmeter_dc": 0,
                    }
                _l1 = _powerdata.get("pmeter_l1", 0)
                _l2 = _powerdata.get("pmeter_l2", 0)
                _l3 = _powerdata.get("pmeter_l3", 0)  # unit?
                _ppv1 = _powerdata.get("ppv1")
                _ppv2 = _powerdata.get("ppv2")
                _dc = _powerdata.get("pmeter_dc")
                _soc = _powerdata.get("soc")
                _bat = _powerdata.get("pbat")
                inverterdata.update({"Instantaneous Grid I/O L1": _l1})
                inverterdata.update({"Instantaneous Grid I/O L2": _l2})
                inverterdata.update({"Instantaneous Grid I/O L3": _l3})
                inverterdata.update({"Instantaneous Generation": _ppv1 + _ppv2 + _dc})
                inverterdata.update({"Instantaneous Battery SOC": _soc})
                inverterdata.update({"Instantaneous Battery I/O": _bat})
                inverterdata.update({"Instantaneous Grid I/O Total": _l1 + _l2 + _l3})
                inverterdata.update(
                    {"Instantaneous Load": _ppv1 + _ppv2 + _dc + _bat + _l1 + _l2 + _l3}
                )
                inverterdata.update({"Instantaneous PPV1": _ppv1})
                inverterdata.update({"Instantaneous PPV2": _ppv2})
                
                # more accurate home load
                
                """unable to calculate battToGrid from alpha ess web site
                Eout is both PV feed in and battery feed in - TODO find a way to calculate battToGrid from alpha web site
                at this point assume there is no battery to grid - means weird number when Virtual Power Plant sell to grid occurs
                """
                battToGrid = 0
                                
                """ EHomeLoad looks to be Eeff (self-consumption) + Einput (draw from grid) - this is not load consumed by house hold
                appliances as it includes charging battery with grid and solar (and thus remains constant during battery discharge)
                
                this alternative calculation derrives load of house hold appliances by summing solar to load, battery to load and grid to load. 
                where battery to load is calculated by discharge - battery to grid *see note on battToGrid :(
                """
                inverterdata.update({"Home Load": _stats.get("Epv2load") + (_stats.get("EDischarge", [])[index] - battToGrid) + _stats.get("EGrid2Load")})
                inverterdata.update({"Battery to Load": _stats.get("EDischarge", [])[index] - battToGrid})
                inverterdata.update({"Battery Stored": _stats.get("Ebat") })                
                
                self.data.update({invertor["sys_sn"]: inverterdata})
            return self.data
        except (
            aiohttp.client_exceptions.ClientConnectorError,
            aiohttp.ClientResponseError,
        ) as error:
            raise UpdateFailed(error) from error
