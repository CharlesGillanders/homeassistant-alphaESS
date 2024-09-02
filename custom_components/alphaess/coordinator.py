"""Coordinator for AlphaEss integration."""
import logging

import aiohttp
from alphaess import alphaess

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL, THROTTLE_MULTIPLIER, get_inverter_count, set_throttle_count_lower, \
    get_inverter_list

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def process_value(value, default=None):
    if value is None or (isinstance(value, str) and value.strip() == ''):
        return default
    return value


async def safe_get(dictionary, key, default=None):
    if dictionary is None:
        return default
    return await process_value(dictionary.get(key), default)


async def safe_calculate(val1, val2):
    if val1 is None or val2 is None:
        return None
    else:
        return val1 - val2


class AlphaESSDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(self, hass: HomeAssistant, client: alphaess.alphaess) -> None:
        """Initialize."""
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = client
        self.update_method = self._async_update_data
        self.has_throttle = True
        self.data: dict[str, dict[str, float]] = {}
        self.LOCAL_INVERTER_COUNT = 0

    async def _async_update_data(self):
        """Update data via library."""

        model_list = get_inverter_list()
        inverter_count = get_inverter_count()

        if "Storion-S5" not in model_list and len(model_list) > 0:
            self.has_throttle = False
            set_throttle_count_lower()

        if inverter_count == 1:
            LOCAL_INVERTER_COUNT = 0
        else:
            LOCAL_INVERTER_COUNT = inverter_count

        try:
            jsondata = await self.api.getdata(self.has_throttle, THROTTLE_MULTIPLIER * LOCAL_INVERTER_COUNT)
            if jsondata is not None:
                for invertor in jsondata:

                    # data from system list data
                    inverterdata = {}
                    if invertor.get("minv") is not None:
                        inverterdata["Model"] = await process_value(invertor.get("minv"))

                    inverterdata["EMS Status"] = await process_value(invertor.get("emsStatus"))
                    inverterdata["Maximum Battery Capacity"] = await process_value(invertor.get("usCapacity"))
                    inverterdata["Current Capacity"] = await process_value(invertor.get("surplusCobat"))
                    inverterdata["Installed Capacity"] = await process_value(invertor.get("cobat"))

                    _sumdata = invertor.get("SumData", {})
                    _onedateenergy = invertor.get("OneDateEnergy", {})
                    _powerdata = invertor.get("LastPower", {})
                    _onedatepower = invertor.get("OneDayPower", {})

                    inverterdata["Total Load"] = await safe_get(_sumdata, "eload")
                    inverterdata["Total Income"] = await safe_get(_sumdata, "totalIncome")
                    inverterdata["Self Consumption"] = await safe_get(_sumdata, "eselfConsumption", default=0) * 100
                    inverterdata["Self Sufficiency"] = await safe_get(_sumdata, "eselfSufficiency", default=0) * 100

                    _pv = await safe_get(_onedateenergy, "epv")
                    _feedin = await safe_get(_onedateenergy, "eOutput")
                    _gridcharge = await safe_get(_onedateenergy, "eGridCharge")
                    _charge = await safe_get(_onedateenergy, "eCharge")

                    inverterdata["Solar Production"] = _pv
                    inverterdata["Solar to Load"] = await safe_calculate(_pv, _feedin)
                    inverterdata["Solar to Grid"] = _feedin
                    inverterdata["Solar to Battery"] = await safe_calculate(_charge, _gridcharge)
                    inverterdata["Grid to Load"] = await safe_get(_onedateenergy, "eInput")
                    inverterdata["Grid to Battery"] = _gridcharge
                    inverterdata["Charge"] = _charge
                    inverterdata["Discharge"] = await safe_get(_onedateenergy, "eDischarge")
                    inverterdata["EV Charger"] = await safe_get(_onedateenergy, "eChargingPile")

                    _soc = await safe_get(_powerdata, "soc")
                    _gridpowerdetails = _powerdata.get("pgridDetail", {})
                    _pvpowerdetails = _powerdata.get("ppvDetail", {})

                    inverterdata["Instantaneous Battery SOC"] = _soc

                    if _onedatepower and _soc == 0:
                        first_entry = _onedatepower[0]
                        _cbat = first_entry.get("cbat", 0)
                        inverterdata["State of Charge"] = _cbat

                    inverterdata["Instantaneous Battery I/O"] = await safe_get(_powerdata, "pbat")
                    inverterdata["Instantaneous Load"] = await safe_get(_powerdata, "pload")
                    inverterdata["Instantaneous Generation"] = await safe_get(_powerdata, "ppv")
                    inverterdata["Instantaneous PPV1"] = await safe_get(_pvpowerdetails, "ppv1")
                    inverterdata["Instantaneous PPV2"] = await safe_get(_pvpowerdetails, "ppv2")
                    inverterdata["Instantaneous PPV3"] = await safe_get(_pvpowerdetails, "ppv3")
                    inverterdata["Instantaneous PPV4"] = await safe_get(_pvpowerdetails, "ppv4")
                    inverterdata["Instantaneous Grid I/O Total"] = await safe_get(_powerdata, "pgrid")
                    inverterdata["Instantaneous Grid I/O L1"] = await safe_get(_gridpowerdetails, "pmeterL1")
                    inverterdata["Instantaneous Grid I/O L2"] = await safe_get(_gridpowerdetails, "pmeterL2")
                    inverterdata["Instantaneous Grid I/O L3"] = await safe_get(_gridpowerdetails, "pmeterL3")

                    self.data.update({invertor["sysSn"]: inverterdata})

                return self.data
        except (
                aiohttp.client_exceptions.ClientConnectorError,
                aiohttp.ClientResponseError,
        ) as error:
            raise UpdateFailed(error) from error
