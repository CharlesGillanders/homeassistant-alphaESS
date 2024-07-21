"""Coordinator for AlphaEss integration."""
import json
import logging

import aiohttp
from alphaess import alphaess

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL, THROTTLE_MULTIPLIER, get_inverter_count

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

        inverter_count = get_inverter_count()
        if inverter_count == 1:
            LOCAL_INVERTER_COUNT = 0
        else:
            LOCAL_INVERTER_COUNT = inverter_count

        _LOGGER.info(f"INVERTER COUNT {inverter_count}")

        try:
            jsondata: json = await self.api.getdata(THROTTLE_MULTIPLIER * LOCAL_INVERTER_COUNT)
            if jsondata is not None:
                for invertor in jsondata:

                    inverterdata: dict[str, any] = {}
                    if invertor.get("minv") is not None:
                        inverterdata.update({"Model": invertor.get("minv")})

                    # data from summary data API
                    _sumdata = invertor.get("SumData", {})
                    # data from one date energy API
                    _onedateenergy = invertor.get("OneDateEnergy", {})
                    # data from last power data API
                    _powerdata = invertor.get("LastPower", {})

                    if _sumdata is not None:
                        #   Still will keep in, but will be provided with the timezone difference
                        inverterdata.update({"Total Load": _sumdata.get("eload")})
                        inverterdata.update({"Total Income": _sumdata.get("totalIncome")})
                        inverterdata.update({"Self Consumption": (_sumdata.get("eselfConsumption") * 100)})
                        inverterdata.update({"Self Sufficiency": (_sumdata.get("eselfSufficiency") * 100)})

                    if _onedateenergy is not None:
                        _pv = _onedateenergy.get("epv")

                        _feedin = _onedateenergy.get("eOutput")
                        _gridcharge = _onedateenergy.get("eGridCharge")
                        _charge = _onedateenergy.get("eCharge")

                        inverterdata.update({"Solar Production": _pv})
                        inverterdata.update({"Solar to Load": _pv - _feedin})
                        inverterdata.update({"Solar to Grid": _feedin})
                        inverterdata.update({"Solar to Battery": _charge - _gridcharge})

                        inverterdata.update({"Grid to Load": _onedateenergy.get("eInput")})
                        inverterdata.update({"Grid to Battery": _gridcharge})

                        inverterdata.update({"Charge": _charge})
                        inverterdata.update({"Discharge": _onedateenergy.get("eDischarge")})

                        inverterdata.update({"EV Charger": _onedateenergy.get("eChargingPile")})

                    if _powerdata is not None:
                        _soc = _powerdata.get("soc")
                        _gridpowerdetails = _powerdata.get("pgridDetail", {})
                        _pvpowerdetails = _powerdata.get("ppvDetail", {})

                        # wonder why do we have this twice
                        inverterdata.update({"Instantaneous Battery SOC": _soc})
                        inverterdata.update({"State of Charge": _soc})

                        inverterdata.update({"Instantaneous Battery I/O": _powerdata.get("pbat")})
                        inverterdata.update({"Instantaneous Load": _powerdata.get("pload")})

                        inverterdata.update({"Instantaneous Generation": _powerdata.get("ppv")})
                        # pv power generation details
                        inverterdata.update({"Instantaneous PPV1": _pvpowerdetails.get("ppv1")})
                        inverterdata.update({"Instantaneous PPV2": _pvpowerdetails.get("ppv2")})
                        inverterdata.update({"Instantaneous PPV3": _pvpowerdetails.get("ppv3")})
                        inverterdata.update({"Instantaneous PPV4": _pvpowerdetails.get("ppv4")})

                        inverterdata.update({"Instantaneous Grid I/O Total": _powerdata.get("pgrid")})
                        # grid power usage details
                        inverterdata.update({"Instantaneous Grid I/O L1": _gridpowerdetails.get("pmeterL1")})
                        inverterdata.update({"Instantaneous Grid I/O L2": _gridpowerdetails.get("pmeterL2")})
                        inverterdata.update({"Instantaneous Grid I/O L3": _gridpowerdetails.get("pmeterL3")})

                    self.data.update({invertor["sysSn"]: inverterdata})

            return self.data
        except (
                aiohttp.client_exceptions.ClientConnectorError,
                aiohttp.ClientResponseError,
        ) as error:
            raise UpdateFailed(error) from error
