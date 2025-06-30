"""Coordinator for AlphaEss integration."""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Union

import aiohttp
from alphaess import alphaess

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    SCAN_INTERVAL,
    THROTTLE_MULTIPLIER,
    get_inverter_count,
    set_throttle_count_lower,
    get_inverter_list,
    LOWER_INVERTER_API_CALL_LIST
)

_LOGGER: logging.Logger = logging.getLogger(__package__)


class DataProcessor:
    """Helper class for data processing utilities."""

    @staticmethod
    async def process_value(value: Any, default: Any = None) -> Any:
        """Process and validate a value, returning default if empty."""
        if value is None or (isinstance(value, str) and value.strip() == ''):
            return default
        return value

    @staticmethod
    async def safe_get(dictionary: Optional[Dict], key: str, default: Any = None) -> Any:
        """Safely get a value from a dictionary."""
        if dictionary is None:
            return default
        return await DataProcessor.process_value(dictionary.get(key), default)

    @staticmethod
    async def safe_calculate(val1: Optional[float], val2: Optional[float]) -> Optional[float]:
        """Safely calculate difference between two values."""
        if val1 is None or val2 is None:
            return None
        return val1 - val2


class TimeHelper:
    """Helper class for time-related operations."""

    @staticmethod
    async def get_rounded_time() -> str:
        """Get time rounded to next 15-minute interval."""
        now = datetime.now()

        if now.minute > 45:
            rounded_time = now + timedelta(hours=1)
            rounded_time = rounded_time.replace(minute=0, second=0, microsecond=0)
        else:
            rounded_time = now + timedelta(minutes=15 - (now.minute % 15))
            rounded_time = rounded_time.replace(second=0, microsecond=0)

        return rounded_time.strftime("%H:%M")

    @staticmethod
    def calculate_time_window(time_period_minutes: int) -> tuple[str, str]:
        """Calculate start and end time for a given period."""
        now = datetime.now()
        start_time_str = TimeHelper.get_rounded_time()
        start_time = datetime.strptime(start_time_str, "%H:%M").replace(
            year=now.year, month=now.month, day=now.day
        )
        end_time = start_time + timedelta(minutes=time_period_minutes)
        return start_time.strftime("%H:%M"), end_time.strftime("%H:%M")


class InverterDataParser:
    """Parse inverter data into structured format."""

    def __init__(self, data_processor: DataProcessor):
        self.dp = data_processor

    async def parse_basic_info(self, invertor: Dict) -> Dict[str, Any]:
        """Parse basic inverter information."""
        return {
            "Model": await self.dp.process_value(invertor.get("minv")),
            "Battery Model": await self.dp.process_value(invertor.get("mbat")),
            "Inverter nominal Power": await self.dp.process_value(invertor.get("poinv")),
            "Pv nominal Power": await self.dp.process_value(invertor.get("popv")),
            "EMS Status": await self.dp.process_value(invertor.get("emsStatus")),
            "Maximum Battery Capacity": await self.dp.process_value(invertor.get("usCapacity")),
            "Current Capacity": await self.dp.process_value(invertor.get("surplusCobat")),
            "Installed Capacity": await self.dp.process_value(invertor.get("cobat")),
        }

    async def parse_local_ip_data(self, local_ip_data: Dict) -> Dict[str, Any]:
        """Parse local IP system data."""
        if not local_ip_data:
            return {}

        status = local_ip_data.get("status", {})
        device_info = local_ip_data.get("device_info", {})

        return {
            "Local IP": local_ip_data.get("ip"),
            "Device Status": await self.dp.safe_get(status, "devstatus"),
            "Cloud Connection Status": await self.dp.safe_get(status, "serverstatus"),
            "WiFi Status": await self.dp.safe_get(status, "wifistatus"),
            "Connected SSID": await self.dp.safe_get(status, "connssid"),
            "WiFi DHCP": await self.dp.safe_get(status, "wifidhcp"),
            "WiFi IP": await self.dp.safe_get(status, "wifiip"),
            "WiFi Mask": await self.dp.safe_get(status, "wifimask"),
            "WiFi Gateway": await self.dp.safe_get(status, "wifigateway"),
            "Device Serial Number": await self.dp.safe_get(device_info, "sn"),
            "Device Key": await self.dp.safe_get(device_info, "key"),
            "Hardware Version": await self.dp.safe_get(device_info, "hw"),
            "Software Version": await self.dp.safe_get(device_info, "sw"),
            "APN": await self.dp.safe_get(device_info, "apn"),
            "Username": await self.dp.safe_get(device_info, "username"),
            "Password": await self.dp.safe_get(device_info, "password"),
            "Ethernet Module": await self.dp.safe_get(device_info, "ethmoudle"),
            "4G Module": await self.dp.safe_get(device_info, "g4moudle"),
        }

    async def parse_ev_data(self, ev_data: Optional[Dict], invertor: Dict) -> Dict[str, Any]:
        """Parse EV charger data."""
        if not ev_data:
            return {}

        ev_data = ev_data[0] if isinstance(ev_data, list) else ev_data
        ev_status = invertor.get("EVStatus", {})
        ev_current = invertor.get("EVCurrent", {})

        return {
            "EV Charger S/N": await self.dp.safe_get(ev_data, "evchargerSn"),
            "EV Charger Model": await self.dp.safe_get(ev_data, "evchargerModel"),
            "EV Charger Status": await self.dp.safe_get(ev_status, "evchargerStatus"),
            "EV Charger Status Raw": await self.dp.safe_get(ev_status, "evchargerStatus"),
            "Household current setup": await self.dp.safe_get(ev_current, "currentsetting"),
        }

    async def parse_summary_data(self, sum_data: Dict) -> Dict[str, Any]:
        """Parse summary statistics."""
        data = {
            "Total Load": await self.dp.safe_get(sum_data, "eload"),
            "Total Income": await self.dp.safe_get(sum_data, "totalIncome"),
            "Total Generation": await self.dp.safe_get(sum_data, "epvtotal"),
            "Trees Planted": await self.dp.safe_get(sum_data, "treeNum"),
            "Co2 Reduction": await self.dp.safe_get(sum_data, "carbonNum"),
            "Currency": await self.dp.safe_get(sum_data, "moneyType"),
        }

        # Handle self consumption and sufficiency correctly
        self_consumption = await self.dp.safe_get(sum_data, "eselfConsumption")
        self_sufficiency = await self.dp.safe_get(sum_data, "eselfSufficiency")

        data["Self Consumption"] = self_consumption * 100 if self_consumption is not None else None
        data["Self Sufficiency"] = self_sufficiency * 100 if self_sufficiency is not None else None

        return data

    async def parse_energy_data(self, energy_data: Dict) -> Dict[str, Any]:
        """Parse daily energy flow data."""
        pv = await self.dp.safe_get(energy_data, "epv")
        feedin = await self.dp.safe_get(energy_data, "eOutput")
        gridcharge = await self.dp.safe_get(energy_data, "eGridCharge")
        charge = await self.dp.safe_get(energy_data, "eCharge")

        return {
            "Solar Production": pv,
            "Solar to Load": await self.dp.safe_calculate(pv, feedin),
            "Solar to Grid": feedin,
            "Solar to Battery": await self.dp.safe_calculate(charge, gridcharge),
            "Grid to Load": await self.dp.safe_get(energy_data, "eInput"),
            "Grid to Battery": gridcharge,
            "Charge": charge,
            "Discharge": await self.dp.safe_get(energy_data, "eDischarge"),
            "EV Charger": await self.dp.safe_get(energy_data, "eChargingPile"),
        }

    async def parse_power_data(self, power_data: Dict, one_day_power: Optional[list]) -> Dict[str, Any]:
        """Parse instantaneous power data."""
        soc = await self.dp.safe_get(power_data, "soc")
        grid_details = power_data.get("pgridDetail", {})
        pv_details = power_data.get("ppvDetail", {})
        ev_details = power_data.get("pevDetail", {})

        data = {
            "Instantaneous Battery SOC": soc,
            "Instantaneous Battery I/O": await self.dp.safe_get(power_data, "pbat"),
            "Instantaneous Load": await self.dp.safe_get(power_data, "pload"),
            "Instantaneous Generation": await self.dp.safe_get(power_data, "ppv"),
            "Instantaneous Grid I/O Total": await self.dp.safe_get(power_data, "pgrid"),
            "pev": await self.dp.safe_get(power_data, "pev"),
            "PrealL1": await self.dp.safe_get(power_data, "prealL1"),
            "PrealL2": await self.dp.safe_get(power_data, "prealL2"),
            "PrealL3": await self.dp.safe_get(power_data, "prealL3"),
        }

        # PV string data
        for i in range(1, 5):
            data[f"Instantaneous PPV{i}"] = await self.dp.safe_get(pv_details, f"ppv{i}")

        data["pmeterDc"] = await self.dp.safe_get(pv_details, "pmeterDc")

        # Grid phase data
        for i in range(1, 4):
            data[f"Instantaneous Grid I/O L{i}"] = await self.dp.safe_get(grid_details, f"pmeterL{i}")

        # EV power data
        for i in range(1, 5):
            key = ["One", "Two", "Three", "Four"][i - 1]
            data[f"Electric Vehicle Power {key}"] = await self.dp.safe_get(ev_details, f"ev{i}Power")

        # Fallback SOC from daily data
        if one_day_power and soc == 0:
            first_entry = one_day_power[0]
            cbat = first_entry.get("cbat")
            if cbat is not None:
                data["State of Charge"] = cbat

        return data

    async def parse_charge_config(self, config: Dict) -> Dict[str, Any]:
        """Parse charge configuration."""
        data = {}
        for key in ["gridCharge", "batHighCap"]:
            data[key] = await self.dp.safe_get(config, key)

        # Parse time slots with the correct key names
        time_start_1 = await self.dp.safe_get(config, "timeChaf1")
        time_end_1 = await self.dp.safe_get(config, "timeChae1")
        time_start_2 = await self.dp.safe_get(config, "timeChaf2")
        time_end_2 = await self.dp.safe_get(config, "timeChae2")

        # Format as "HH:MM - HH:MM" to match expected format
        if time_start_1 and time_end_1:
            data["Charge Time 1"] = f"{time_start_1} - {time_end_1}"
        else:
            data["Charge Time 1"] = "00:00 - 00:00"

        if time_start_2 and time_end_2:
            data["Charge Time 2"] = f"{time_start_2} - {time_end_2}"
        else:
            data["Charge Time 2"] = "00:00 - 00:00"

        # Also keep the raw values for compatibility
        data["charge_timeChaf1"] = time_start_1
        data["charge_timeChae1"] = time_end_1
        data["charge_timeChaf2"] = time_start_2
        data["charge_timeChae2"] = time_end_2

        return data

    async def parse_discharge_config(self, config: Dict) -> Dict[str, Any]:
        """Parse discharge configuration."""
        data = {}
        for key in ["ctrDis", "batUseCap"]:
            data[key] = await self.dp.safe_get(config, key)

        # Parse time slots with the correct key names
        time_start_1 = await self.dp.safe_get(config, "timeDisf1")
        time_end_1 = await self.dp.safe_get(config, "timeDise1")
        time_start_2 = await self.dp.safe_get(config, "timeDisf2")
        time_end_2 = await self.dp.safe_get(config, "timeDise2")

        # Format as "HH:MM - HH:MM" to match expected format
        if time_start_1 and time_end_1:
            data["Discharge Time 1"] = f"{time_start_1} - {time_end_1}"
        else:
            data["Discharge Time 1"] = "00:00 - 00:00"

        if time_start_2 and time_end_2:
            data["Discharge Time 2"] = f"{time_start_2} - {time_end_2}"
        else:
            data["Discharge Time 2"] = "00:00 - 00:00"

        # Also keep the raw values for compatibility
        data["discharge_timeDisf1"] = time_start_1
        data["discharge_timeDise1"] = time_end_1
        data["discharge_timeDisf2"] = time_start_2
        data["discharge_timeDise2"] = time_end_2

        return data


class AlphaESSDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(self, hass: HomeAssistant, client: alphaess.alphaess) -> None:
        """Initialize coordinator."""
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = client
        self.hass = hass
        self.data: dict[str, dict[str, float]] = {}

        # Initialize helpers
        self.data_processor = DataProcessor()
        self.time_helper = TimeHelper()
        self.parser = InverterDataParser(self.data_processor)

        # Configure based on inverter types
        self._configure_inverter_settings()

    def _configure_inverter_settings(self) -> None:
        """Configure settings based on inverter types."""
        self.model_list = get_inverter_list()
        self.inverter_count = get_inverter_count()
        self.LOCAL_INVERTER_COUNT = 0 if self.inverter_count <= 1 else self.inverter_count

        # Check if we need reduced API calls
        self.has_throttle = True
        if (all(inverter not in self.model_list for inverter in LOWER_INVERTER_API_CALL_LIST)
                and len(self.model_list) > 0):
            self.has_throttle = False
            set_throttle_count_lower()

    async def control_ev(self, serial: str, ev_serial: str, direction: str) -> None:
        """Control EV charger."""
        result = await self.api.remoteControlEvCharger(serial, ev_serial, direction)
        _LOGGER.info(
            f"Control EV Charger: {ev_serial} for serial: {serial} "
            f"Direction: {direction} - Result: {result}"
        )

    async def reset_config(self, serial: str) -> None:
        """Reset charge and discharge configuration."""
        bat_use_cap = self.hass.data[DOMAIN][serial].get("batUseCap", 10)
        bat_high_cap = self.hass.data[DOMAIN][serial].get("batHighCap", 90)

        results = await self._reset_charge_discharge_config(serial, bat_high_cap, bat_use_cap)
        _LOGGER.info(
            f"Reset Charge and Discharge configuration - "
            f"Charge: {results['charge']}, Discharge: {results['discharge']}"
        )

    async def _reset_charge_discharge_config(
            self, serial: str, bat_high_cap: int, bat_use_cap: int
    ) -> Dict[str, Any]:
        """Internal method to reset configurations."""
        charge_result = await self.api.updateChargeConfigInfo(
            serial, bat_high_cap, 1, "00:00", "00:00", "00:00", "00:00"
        )
        discharge_result = await self.api.updateDisChargeConfigInfo(
            serial, bat_use_cap, 1, "00:00", "00:00", "00:00", "00:00"
        )
        return {"charge": charge_result, "discharge": discharge_result}

    async def update_discharge(self, name: str, serial: str, time_period: int) -> None:
        """Update discharge configuration for specified time period."""
        bat_use_cap = self.hass.data[DOMAIN][serial].get(name)
        start_time, end_time = self.time_helper.calculate_time_window(time_period)

        result = await self.api.updateDisChargeConfigInfo(
            serial, bat_use_cap, 1, end_time, "00:00", start_time, "00:00"
        )

        _LOGGER.info(
            f"Updated discharge config - Capacity: {bat_use_cap}, "
            f"Period: {start_time} to {end_time}, Result: {result}"
        )

    async def update_charge(self, name: str, serial: str, time_period: int) -> None:
        """Update charge configuration for specified time period."""
        bat_high_cap = self.hass.data[DOMAIN][serial].get(name)
        start_time, end_time = self.time_helper.calculate_time_window(time_period)

        result = await self.api.updateChargeConfigInfo(
            serial, bat_high_cap, 1, end_time, "00:00", start_time, "00:00"
        )

        _LOGGER.info(
            f"Updated charge config - Capacity: {bat_high_cap}, "
            f"Period: {start_time} to {end_time}, Result: {result}"
        )

    async def _async_update_data(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """Update data via library."""
        try:
            throttle_factor = THROTTLE_MULTIPLIER * self.LOCAL_INVERTER_COUNT
            jsondata = await self.api.getdata(True, True, throttle_factor)

            if jsondata is None:
                return self.data

            for invertor in jsondata:
                serial = invertor.get("sysSn")
                if not serial:
                    continue

                # Parse all data sections
                inverter_data = await self._parse_inverter_data(invertor)
                self.data[serial] = inverter_data

            return self.data

        except (aiohttp.ClientConnectorError, aiohttp.ClientResponseError) as error:
            _LOGGER.error(f"Error fetching data: {error}")
            self.data = None
            return self.data

    async def _parse_inverter_data(self, invertor: Dict) -> Dict[str, Any]:
        """Parse all data for a single inverter."""
        # Start with basic info
        data = await self.parser.parse_basic_info(invertor)

        # Add LocalIPData if available
        local_ip_data = invertor.get("LocalIPData", {})
        if local_ip_data:
            data.update(await self.parser.parse_local_ip_data(local_ip_data))

        # Add EV data if available
        ev_data = invertor.get("EVData", {})
        if ev_data:
            data.update(await self.parser.parse_ev_data(ev_data, invertor))

        # Add summary data
        sum_data = invertor.get("SumData", {})
        if sum_data:
            data.update(await self.parser.parse_summary_data(sum_data))

        # Add energy data
        energy_data = invertor.get("OneDateEnergy", {})
        if energy_data:
            data.update(await self.parser.parse_energy_data(energy_data))

        # Add power data
        power_data = invertor.get("LastPower", {})
        if power_data:
            one_day_power = invertor.get("OneDayPower", {})
            data.update(await self.parser.parse_power_data(power_data, one_day_power))

        # Add configuration data
        charge_config = invertor.get("ChargeConfig", {})
        if charge_config:
            data.update(await self.parser.parse_charge_config(charge_config))

        discharge_config = invertor.get("DisChargeConfig", {})
        if discharge_config:
            data.update(await self.parser.parse_discharge_config(discharge_config))

        # Add Charging Range (combining charge and discharge data)
        if charge_config or discharge_config:
            bat_high_cap = charge_config.get("batHighCap", 90) if charge_config else 90
            bat_use_cap = discharge_config.get("batUseCap", 10) if discharge_config else 10
            data["Charging Range"] = f"{bat_use_cap}% - {bat_high_cap}%"

        return data
