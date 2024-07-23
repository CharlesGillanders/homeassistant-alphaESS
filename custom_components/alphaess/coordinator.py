"""Coordinator for AlphaEss integration."""
import logging

import aiohttp
from alphaess import alphaess

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL, THROTTLE_MULTIPLIER, get_inverter_count

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def process_value(value):
    if value is None or (isinstance(value, str) and value.strip() == ''):
        return 0
    return value


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

        try:
            jsondata = await self.api.getdata(THROTTLE_MULTIPLIER * LOCAL_INVERTER_COUNT)
            if jsondata is not None:
                for invertor in jsondata:

                    inverterdata = {}
                    if invertor.get("minv") is not None:
                        inverterdata["Model"] = await process_value(invertor.get("minv"))
                    inverterdata["EMS Status"] = await process_value(invertor.get("emsStatus"))

                    # data from summary data API
                    _sumdata = invertor.get("SumData", {})
                    # data from one date energy API
                    _onedateenergy = invertor.get("OneDateEnergy", {})
                    # data from last power data API
                    _powerdata = invertor.get("LastPower", {})

                    if _sumdata is not None:
                        inverterdata["Total Load"] = await process_value(_sumdata.get("eload"))
                        inverterdata["Total Income"] = await process_value(_sumdata.get("totalIncome"))
                        inverterdata["Self Consumption"] = await process_value(_sumdata.get("eselfConsumption") * 100)
                        inverterdata["Self Sufficiency"] = await process_value(_sumdata.get("eselfSufficiency") * 100)

                    if _onedateenergy is not None:
                        _pv = await process_value(_onedateenergy.get("epv"))
                        _feedin = await process_value(_onedateenergy.get("eOutput"))
                        _gridcharge = await process_value(_onedateenergy.get("eGridCharge"))
                        _charge = await process_value(_onedateenergy.get("eCharge"))

                        inverterdata["Solar Production"] = _pv
                        inverterdata["Solar to Load"] = await process_value(_pv - _feedin)
                        inverterdata["Solar to Grid"] = _feedin
                        inverterdata["Solar to Battery"] = await process_value(_charge - _gridcharge)
                        inverterdata["Grid to Load"] = await process_value(_onedateenergy.get("eInput"))
                        inverterdata["Grid to Battery"] = _gridcharge
                        inverterdata["Charge"] = _charge
                        inverterdata["Discharge"] = await process_value(_onedateenergy.get("eDischarge"))
                        inverterdata["EV Charger"] = await process_value(_onedateenergy.get("eChargingPile"))

                    if _powerdata is not None:
                        _soc = await process_value(_powerdata.get("soc"))
                        _gridpowerdetails = _powerdata.get("pgridDetail", {})
                        _pvpowerdetails = _powerdata.get("ppvDetail", {})

                        inverterdata["Instantaneous Battery SOC"] = _soc
                        inverterdata["State of Charge"] = _soc
                        inverterdata["Instantaneous Battery I/O"] = await process_value(_powerdata.get("pbat"))
                        inverterdata["Instantaneous Load"] = await process_value(_powerdata.get("pload"))
                        # pv power generation details
                        inverterdata["Instantaneous Generation"] = await process_value(_powerdata.get("ppv"))
                        inverterdata["Instantaneous PPV1"] = await process_value(_pvpowerdetails.get("ppv1"))
                        inverterdata["Instantaneous PPV2"] = await process_value(_pvpowerdetails.get("ppv2"))
                        inverterdata["Instantaneous PPV3"] = await process_value(_pvpowerdetails.get("ppv3"))
                        inverterdata["Instantaneous PPV4"] = await process_value(_pvpowerdetails.get("ppv4"))
                        # grid power usage details
                        inverterdata["Instantaneous Grid I/O Total"] = await process_value(_powerdata.get("pgrid"))
                        inverterdata["Instantaneous Grid I/O L1"] = await process_value(_gridpowerdetails.get("pmeterL1"))
                        inverterdata["Instantaneous Grid I/O L2"] = await process_value(_gridpowerdetails.get("pmeterL2"))
                        inverterdata["Instantaneous Grid I/O L3"] = await process_value(_gridpowerdetails.get("pmeterL3"))


                    self.data.update({invertor["sysSn"]: inverterdata})

            return self.data
        except (
                aiohttp.client_exceptions.ClientConnectorError,
                aiohttp.ClientResponseError,
        ) as error:
            raise UpdateFailed(error) from error
