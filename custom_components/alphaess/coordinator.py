"""Coordinator for AlphaEss integration."""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Union

import aiohttp
from alphaess import alphaess

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_IP_ADDRESS,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    LOWER_INVERTER_API_CALL_LIST,
    SCAN_INTERVAL,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from .enums import AlphaESSNames

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
    async def calculate_time_window(time_period_minutes: int) -> tuple[str, str]:
        """Calculate start and end time for a given period."""
        now = datetime.now()
        start_time_str = await TimeHelper.get_rounded_time()
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
            AlphaESSNames.mbat: await self.dp.process_value(invertor.get("mbat")),
            AlphaESSNames.poinv: await self.dp.process_value(invertor.get("poinv")),
            AlphaESSNames.popv: await self.dp.process_value(invertor.get("popv")),
            AlphaESSNames.EmsStatus: await self.dp.process_value(invertor.get("emsStatus")),
            AlphaESSNames.usCapacity: await self.dp.process_value(invertor.get("usCapacity")),
            AlphaESSNames.surplusCobat: await self.dp.process_value(invertor.get("surplusCobat")),
            AlphaESSNames.cobat: await self.dp.process_value(invertor.get("cobat")),
        }

    async def parse_local_ip_data(self, local_ip_data: Dict) -> Dict[str, Any]:
        """Parse local IP system data."""
        if not local_ip_data:
            return {}

        status = local_ip_data.get("status", {})
        device_info = local_ip_data.get("device_info", {})

        return {
            AlphaESSNames.localIP: local_ip_data.get("ip"),
            AlphaESSNames.deviceStatus: await self.dp.safe_get(status, "devstatus"),
            AlphaESSNames.cloudConnectionStatus: await self.dp.safe_get(status, "serverstatus"),
            AlphaESSNames.wifiStatus: await self.dp.safe_get(status, "wifistatus"),
            AlphaESSNames.connectedSSID: await self.dp.safe_get(status, "connssid"),
            AlphaESSNames.wifiDHCP: await self.dp.safe_get(status, "wifidhcp"),
            AlphaESSNames.wifiIP: await self.dp.safe_get(status, "wifiip"),
            AlphaESSNames.wifiMask: await self.dp.safe_get(status, "wifimask"),
            AlphaESSNames.wifiGateway: await self.dp.safe_get(status, "wifigateway"),
            AlphaESSNames.deviceSerialNumber: await self.dp.safe_get(device_info, "sn"),
            AlphaESSNames.registerKey: await self.dp.safe_get(device_info, "key"),
            AlphaESSNames.hardwareVersion: await self.dp.safe_get(device_info, "hw"),
            AlphaESSNames.softwareVersion: await self.dp.safe_get(device_info, "sw"),
            AlphaESSNames.apn: await self.dp.safe_get(device_info, "apn"),
            AlphaESSNames.username: await self.dp.safe_get(device_info, "username"),
            AlphaESSNames.password: await self.dp.safe_get(device_info, "password"),
            AlphaESSNames.ethernetModule: await self.dp.safe_get(device_info, "ethmoudle"),
            AlphaESSNames.fourGModule: await self.dp.safe_get(device_info, "g4moudle"),
        }

    async def parse_ev_data(self, ev_data: Optional[Dict], invertor: Dict) -> Dict[str, Any]:
        """Parse EV charger data."""
        if not ev_data:
            return {}

        ev_data = ev_data[0] if isinstance(ev_data, list) else ev_data
        ev_status = invertor.get("EVStatus", {})
        ev_current = invertor.get("EVCurrent", {})

        return {
            AlphaESSNames.evchargersn: await self.dp.safe_get(ev_data, "evchargerSn"),
            AlphaESSNames.evchargermodel: await self.dp.safe_get(ev_data, "evchargerModel"),
            AlphaESSNames.evchargerstatus: await self.dp.safe_get(ev_status, "evchargerStatus"),
            AlphaESSNames.evchargerstatusraw: await self.dp.safe_get(ev_status, "evchargerStatus"),
            AlphaESSNames.evcurrentsetting: await self.dp.safe_get(ev_current, "currentsetting"),
        }

    async def parse_summary_data(self, sum_data: Dict) -> Dict[str, Any]:
        """Parse summary statistics."""
        data = {
            AlphaESSNames.TotalLoad: await self.dp.safe_get(sum_data, "eload"),
            AlphaESSNames.Income: await self.dp.safe_get(sum_data, "totalIncome"),
            AlphaESSNames.Total_Generation: await self.dp.safe_get(sum_data, "epvtotal"),
            AlphaESSNames.treePlanted: await self.dp.safe_get(sum_data, "treeNum"),
            AlphaESSNames.carbonReduction: await self.dp.safe_get(sum_data, "carbonNum"),
            "Currency": await self.dp.safe_get(sum_data, "moneyType"),
        }

        # Handle self consumption and sufficiency correctly
        self_consumption = await self.dp.safe_get(sum_data, "eselfConsumption")
        self_sufficiency = await self.dp.safe_get(sum_data, "eselfSufficiency")

        data[AlphaESSNames.SelfConsumption] = self_consumption * 100 if self_consumption is not None else None
        data[AlphaESSNames.SelfSufficiency] = self_sufficiency * 100 if self_sufficiency is not None else None

        return data

    async def parse_energy_data(self, energy_data: Dict) -> Dict[str, Any]:
        """Parse daily energy flow data."""
        pv = await self.dp.safe_get(energy_data, "epv")
        feedin = await self.dp.safe_get(energy_data, "eOutput")
        gridcharge = await self.dp.safe_get(energy_data, "eGridCharge")
        charge = await self.dp.safe_get(energy_data, "eCharge")

        return {
            AlphaESSNames.SolarProduction: pv,
            AlphaESSNames.SolarToLoad: await self.dp.safe_calculate(pv, feedin),
            AlphaESSNames.SolarToGrid: feedin,
            AlphaESSNames.SolarToBattery: await self.dp.safe_calculate(charge, gridcharge),
            AlphaESSNames.GridToLoad: await self.dp.safe_get(energy_data, "eInput"),
            AlphaESSNames.GridToBattery: gridcharge,
            AlphaESSNames.Charge: charge,
            AlphaESSNames.Discharge: await self.dp.safe_get(energy_data, "eDischarge"),
            AlphaESSNames.EVCharger: await self.dp.safe_get(energy_data, "eChargingPile"),
        }

    async def parse_power_data(self, power_data: Dict, one_day_power: Optional[list]) -> Dict[str, Any]:
        """Parse instantaneous power data."""
        soc = await self.dp.safe_get(power_data, "soc")
        grid_details = power_data.get("pgridDetail", {})
        pv_details = power_data.get("ppvDetail", {})
        ev_details = power_data.get("pevDetail", {})

        data = {
            AlphaESSNames.BatterySOC: soc,
            AlphaESSNames.BatteryIO: await self.dp.safe_get(power_data, "pbat"),
            AlphaESSNames.Load: await self.dp.safe_get(power_data, "pload"),
            AlphaESSNames.Generation: await self.dp.safe_get(power_data, "ppv"),
            AlphaESSNames.GridIOTotal: await self.dp.safe_get(power_data, "pgrid"),
            AlphaESSNames.pev: await self.dp.safe_get(power_data, "pev"),
            AlphaESSNames.PrealL1: await self.dp.safe_get(power_data, "prealL1"),
            AlphaESSNames.PrealL2: await self.dp.safe_get(power_data, "prealL2"),
            AlphaESSNames.PrealL3: await self.dp.safe_get(power_data, "prealL3"),
        }

        # PV string data
        for i in range(1, 5):
            data[getattr(AlphaESSNames, f"PPV{i}")] = await self.dp.safe_get(pv_details, f"ppv{i}")

        data[AlphaESSNames.pmeterDc] = await self.dp.safe_get(pv_details, "pmeterDc")

        # Grid phase data
        for i in range(1, 4):
            data[getattr(AlphaESSNames, f"GridIOL{i}")] = await self.dp.safe_get(grid_details, f"pmeterL{i}")

        # EV power data
        for i in range(1, 5):
            key_map = {1: "One", 2: "Two", 3: "Three", 4: "Four"}
            data[getattr(AlphaESSNames, f"ElectricVehiclePower{key_map[i]}")] = await self.dp.safe_get(ev_details, f"ev{i}Power")

        # Fallback SOC from daily data
        if one_day_power and soc == 0:
            first_entry = one_day_power[0]
            cbat = first_entry.get("cbat")
            if cbat is not None:
                data[AlphaESSNames.StateOfCharge] = cbat

        return data

    async def parse_charge_config(self, config: Dict) -> Dict[str, Any]:
        """Parse charge configuration."""
        data = {}
        for key in ["gridCharge", AlphaESSNames.batHighCap]:
            if key == AlphaESSNames.batHighCap:
                data[key] = await self.dp.safe_get(config, "batHighCap")
            else:
                data[key] = await self.dp.safe_get(config, key)

        # Parse time slots with the correct key names
        time_start_1 = await self.dp.safe_get(config, "timeChaf1")
        time_end_1 = await self.dp.safe_get(config, "timeChae1")
        time_start_2 = await self.dp.safe_get(config, "timeChaf2")
        time_end_2 = await self.dp.safe_get(config, "timeChae2")

        # Format as "HH:MM - HH:MM" to match expected format
        if time_start_1 and time_end_1:
            data[AlphaESSNames.ChargeTime1] = f"{time_start_1} - {time_end_1}"
        else:
            data[AlphaESSNames.ChargeTime1] = "00:00 - 00:00"

        if time_start_2 and time_end_2:
            data[AlphaESSNames.ChargeTime2] = f"{time_start_2} - {time_end_2}"
        else:
            data[AlphaESSNames.ChargeTime2] = "00:00 - 00:00"

        # Also keep the raw values for compatibility
        data["charge_timeChaf1"] = time_start_1
        data["charge_timeChae1"] = time_end_1
        data["charge_timeChaf2"] = time_start_2
        data["charge_timeChae2"] = time_end_2

        return data

    async def parse_discharge_config(self, config: Dict) -> Dict[str, Any]:
        """Parse discharge configuration."""
        data = {}
        for key in ["ctrDis", AlphaESSNames.batUseCap]:
            if key == AlphaESSNames.batUseCap:
                data[key] = await self.dp.safe_get(config, "batUseCap")
            else:
                data[key] = await self.dp.safe_get(config, key)

        # Parse time slots with the correct key names
        time_start_1 = await self.dp.safe_get(config, "timeDisf1")
        time_end_1 = await self.dp.safe_get(config, "timeDise1")
        time_start_2 = await self.dp.safe_get(config, "timeDisf2")
        time_end_2 = await self.dp.safe_get(config, "timeDise2")

        # Format as "HH:MM - HH:MM" to match expected format
        if time_start_1 and time_end_1:
            data[AlphaESSNames.DischargeTime1] = f"{time_start_1} - {time_end_1}"
        else:
            data[AlphaESSNames.DischargeTime1] = "00:00 - 00:00"

        if time_start_2 and time_end_2:
            data[AlphaESSNames.DischargeTime2] = f"{time_start_2} - {time_end_2}"
        else:
            data[AlphaESSNames.DischargeTime2] = "00:00 - 00:00"

        # Also keep the raw values for compatibility
        data["discharge_timeDisf1"] = time_start_1
        data["discharge_timeDise1"] = time_end_1
        data["discharge_timeDisf2"] = time_start_2
        data["discharge_timeDise2"] = time_end_2

        return data


class AlphaESSDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: alphaess.alphaess,
        ip_address_map: dict[str, str | None] | None = None,
        inverter_models: list[str] | None = None,
        entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = client
        self.hass = hass
        self.data: dict[str, dict[str, float]] = {}
        self.entry = entry

        # Per-inverter IP address mapping
        self.ip_address_map = ip_address_map or {}

        # Track whether cloud API is reachable
        self.cloud_available = True

        # Initialize helpers
        self.data_processor = DataProcessor()
        self.time_helper = TimeHelper()
        self.parser = InverterDataParser(self.data_processor)

        # Store inverter info as instance state (no more globals)
        self.model_list = inverter_models or []
        self.inverter_count = len(self.model_list)
        self.LOCAL_INVERTER_COUNT = 0 if self.inverter_count <= 1 else self.inverter_count

        # Configure throttling based on inverter types
        self.throttle_multiplier = 0.0
        self.has_throttle = True
        if (all(inverter not in self.model_list for inverter in LOWER_INVERTER_API_CALL_LIST)
                and len(self.model_list) > 0):
            self.has_throttle = False
            self.throttle_multiplier = 1.25

        # Build subentry lookup for device info
        self._inverter_subentry_map: dict[str, str] = {}
        self._ev_charger_subentry_map: dict[str, str] = {}
        if entry:
            for subentry_id, subentry in entry.subentries.items():
                serial = subentry.data.get(CONF_SERIAL_NUMBER, "")
                if subentry.subentry_type == SUBENTRY_TYPE_INVERTER:
                    self._inverter_subentry_map[serial] = subentry_id
                elif subentry.subentry_type == SUBENTRY_TYPE_EV_CHARGER:
                    self._ev_charger_subentry_map[serial] = subentry_id

    def get_inverter_subentry_id(self, serial: str) -> str | None:
        """Get the subentry ID for an inverter by its serial number."""
        return self._inverter_subentry_map.get(serial)

    def get_ev_charger_subentry_id(self, ev_serial: str) -> str | None:
        """Get the subentry ID for an EV charger by its serial number."""
        return self._ev_charger_subentry_map.get(ev_serial)

    async def control_ev(self, serial: str, ev_serial: str, direction: str) -> None:
        """Control EV charger."""
        result = await self.api.remoteControlEvCharger(serial, ev_serial, direction)
        _LOGGER.info(
            f"Control EV Charger: {ev_serial} for serial: {serial} "
            f"Direction: {direction} - Result: {result}"
        )

    async def reset_config(self, serial: str) -> None:
        """Reset charge and discharge configuration."""
        bat_use_cap = self.hass.data[DOMAIN][serial].get(AlphaESSNames.batUseCap, 10)
        bat_high_cap = self.hass.data[DOMAIN][serial].get(AlphaESSNames.batHighCap, 90)

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
        start_time, end_time = await self.time_helper.calculate_time_window(time_period)

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
        start_time, end_time = await self.time_helper.calculate_time_window(time_period)

        result = await self.api.updateChargeConfigInfo(
            serial, bat_high_cap, 1, end_time, "00:00", start_time, "00:00"
        )

        _LOGGER.info(
            f"Updated charge config - Capacity: {bat_high_cap}, "
            f"Period: {start_time} to {end_time}, Result: {result}"
        )

    async def _async_update_data(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """Update data via library."""
        if self.data is None:
            self.data = {}

        try:
            throttle_factor = self.throttle_multiplier * self.LOCAL_INVERTER_COUNT
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

            # Fetch local IP data per-inverter for those with configured IPs
            await self._fetch_per_inverter_local_data()

            self.cloud_available = True
            return self.data

        except (aiohttp.ClientConnectorError, aiohttp.ClientResponseError, TypeError) as error:
            _LOGGER.warning(f"Cloud API error: {error}")
            self.cloud_available = False
            return await self._fallback_to_local_data()
        except Exception as error:
            _LOGGER.error(f"Unexpected error fetching data: {error}")
            self.cloud_available = False
            return await self._fallback_to_local_data()

    async def _fetch_per_inverter_local_data(self) -> None:
        """Fetch local IP data for each inverter that has a configured IP.

        Temporarily sets the API client's ipaddress for each call,
        then resets it to None.
        """
        for serial, ip in self.ip_address_map.items():
            if not ip or serial not in self.data:
                continue

            # Skip if cloud API already provided LocalIPData for this inverter
            if self.data[serial].get("Local IP"):
                continue

            try:
                self.api.ipaddress = ip
                local_ip_raw = await self.api.getIPData()
                if local_ip_raw:
                    local_ip_data = {"ip": ip, **local_ip_raw}
                    parsed = await self.parser.parse_local_ip_data(local_ip_data)
                    self.data[serial].update(parsed)
                    _LOGGER.debug(f"Fetched local IP data for {serial} from {ip}")
            except Exception as error:
                _LOGGER.debug(f"Could not fetch local IP data for {serial} from {ip}: {error}")
            finally:
                self.api.ipaddress = None

    async def _fallback_to_local_data(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """Attempt to fetch local IP data when cloud API is unavailable.

        Uses per-inverter IP addresses from subentry configuration.
        Cloud sensor keys are removed so those entities become unavailable.
        Local IP sensor keys are kept with fresh data.
        """
        has_any_local_ip = any(ip for ip in self.ip_address_map.values() if ip)

        if not has_any_local_ip:
            _LOGGER.debug("No local IP configured for any inverter")
            return None

        any_success = False

        for serial, ip in self.ip_address_map.items():
            if not ip:
                # No IP for this inverter - clear cloud data but keep model
                if serial in self.data:
                    model = self.data[serial].get("Model")
                    self.data[serial] = {"Model": model}
                continue

            try:
                self.api.ipaddress = ip
                local_ip_raw = await self.api.getIPData()
                if local_ip_raw:
                    local_ip_data = {"ip": ip, **local_ip_raw}
                    parsed = await self.parser.parse_local_ip_data(local_ip_data)
                    model = self.data.get(serial, {}).get("Model")
                    self.data[serial] = {"Model": model, **parsed}
                    any_success = True
                    _LOGGER.info(f"Cloud unavailable - using local data for {serial} from {ip}")
                else:
                    model = self.data.get(serial, {}).get("Model")
                    self.data[serial] = {"Model": model}
            except Exception as error:
                _LOGGER.warning(f"Local IP fetch failed for {serial} ({ip}): {error}")
                model = self.data.get(serial, {}).get("Model")
                self.data[serial] = {"Model": model}
            finally:
                self.api.ipaddress = None

        if not any_success:
            _LOGGER.warning("Cloud API unavailable and all local IP fetches failed")
            return None

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
            data[AlphaESSNames.ChargeRange] = f"{bat_use_cap}% - {bat_high_cap}%"

        return data