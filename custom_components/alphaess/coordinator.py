"""Coordinator for AlphaEss integration."""
import asyncio
import logging
import time as time_mod
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from alphaess import alphaess

from .const import (
    CONF_SERIAL_NUMBER,
    DEFAULT_FAST_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    LOWER_INVERTER_API_CALL_LIST,
    SCAN_INTERVAL,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from .enums import AlphaESSNames

_LOGGER = logging.getLogger(__name__)


class DataProcessor:
    """Helper class for data processing utilities."""

    @staticmethod
    def process_value(value: Any, default: Any = None) -> Any:
        """Process and validate a value, returning default if empty."""
        if value is None or (isinstance(value, str) and value.strip() == ''):
            return default
        return value

    @staticmethod
    def safe_get(dictionary: dict | None, key: str, default: Any = None) -> Any:
        """Safely get a value from a dictionary."""
        if dictionary is None:
            return default
        return DataProcessor.process_value(dictionary.get(key), default)

    @staticmethod
    def safe_calculate(val1: float | None, val2: float | None) -> float | None:
        """Safely calculate difference between two values."""
        if val1 is None or val2 is None:
            return None
        return val1 - val2


class TimeHelper:
    """Helper class for time-related operations."""

    @staticmethod
    def get_rounded_time() -> str:
        """Get time rounded to next 15-minute interval (HA local time)."""
        now = dt_util.now()

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
        now = dt_util.now()
        start_time_str = TimeHelper.get_rounded_time()
        hour, minute = (int(part) for part in start_time_str.split(":"))
        start_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        end_time = start_time + timedelta(minutes=time_period_minutes)
        return start_time.strftime("%H:%M"), end_time.strftime("%H:%M")


class InverterDataParser:
    """Parse inverter data into structured format."""

    def __init__(self, data_processor: DataProcessor):
        self.dp = data_processor

    def parse_basic_info(self, invertor: dict) -> dict[str, Any]:
        """Parse basic inverter information."""
        return {
            "Model": self.dp.process_value(invertor.get("minv")),
            AlphaESSNames.mbat: self.dp.process_value(invertor.get("mbat")),
            AlphaESSNames.poinv: self.dp.process_value(invertor.get("poinv")),
            AlphaESSNames.popv: self.dp.process_value(invertor.get("popv")),
            AlphaESSNames.EmsStatus: self.dp.process_value(invertor.get("emsStatus")),
            AlphaESSNames.usCapacity: self.dp.process_value(invertor.get("usCapacity")),
            AlphaESSNames.surplusCobat: self.dp.process_value(invertor.get("surplusCobat")),
            AlphaESSNames.cobat: self.dp.process_value(invertor.get("cobat")),
        }

    def parse_local_ip_data(self, local_ip_data: dict) -> dict[str, Any]:
        """Parse local IP system data."""
        if not local_ip_data:
            return {}

        status = local_ip_data.get("status", {})
        device_info = local_ip_data.get("device_info", {})

        return {
            AlphaESSNames.localIP: local_ip_data.get("ip"),
            AlphaESSNames.deviceStatus: self.dp.safe_get(status, "devstatus"),
            AlphaESSNames.cloudConnectionStatus: self.dp.safe_get(status, "serverstatus"),
            AlphaESSNames.wifiStatus: self.dp.safe_get(status, "wifistatus"),
            AlphaESSNames.connectedSSID: self.dp.safe_get(status, "connssid"),
            AlphaESSNames.wifiDHCP: self.dp.safe_get(status, "wifidhcp"),
            AlphaESSNames.wifiIP: self.dp.safe_get(status, "wifiip"),
            AlphaESSNames.wifiMask: self.dp.safe_get(status, "wifimask"),
            AlphaESSNames.wifiGateway: self.dp.safe_get(status, "wifigateway"),
            AlphaESSNames.deviceSerialNumber: self.dp.safe_get(device_info, "sn"),
            AlphaESSNames.registerKey: self.dp.safe_get(device_info, "key"),
            AlphaESSNames.hardwareVersion: self.dp.safe_get(device_info, "hw"),
            AlphaESSNames.softwareVersion: self.dp.safe_get(device_info, "sw"),
            AlphaESSNames.apn: self.dp.safe_get(device_info, "apn"),
            AlphaESSNames.username: self.dp.safe_get(device_info, "username"),
            AlphaESSNames.password: self.dp.safe_get(device_info, "password"),
            AlphaESSNames.ethernetModule: self.dp.safe_get(device_info, "ethmoudle"),
            AlphaESSNames.fourGModule: self.dp.safe_get(device_info, "g4moudle"),
        }

    def parse_ev_data(self, ev_data: dict | None, invertor: dict) -> dict[str, Any]:
        """Parse EV charger data."""
        if not ev_data:
            return {}

        ev_data = ev_data[0] if isinstance(ev_data, list) else ev_data
        ev_status = invertor.get("EVStatus", {})
        ev_current = invertor.get("EVCurrent", {})

        return {
            AlphaESSNames.evchargersn: self.dp.safe_get(ev_data, "evchargerSn"),
            AlphaESSNames.evchargermodel: self.dp.safe_get(ev_data, "evchargerModel"),
            AlphaESSNames.evchargerstatus: self.dp.safe_get(ev_status, "evchargerStatus"),
            AlphaESSNames.evchargerstatusraw: self.dp.safe_get(ev_status, "evchargerStatus"),
            AlphaESSNames.evcurrentsetting: self.dp.safe_get(ev_current, "currentsetting"),
        }

    def parse_summary_data(self, sum_data: dict, fallback_currency: str | None = None) -> dict[str, Any]:
        """Parse summary statistics."""
        currency = self.dp.safe_get(sum_data, "moneyType")

        data = {
            AlphaESSNames.TotalLoad: self.dp.safe_get(sum_data, "eload"),
            AlphaESSNames.Income: self.dp.safe_get(sum_data, "totalIncome"),
            AlphaESSNames.Total_Generation: self.dp.safe_get(sum_data, "epvtotal"),
            AlphaESSNames.treePlanted: self.dp.safe_get(sum_data, "treeNum"),
            AlphaESSNames.carbonReduction: self.dp.safe_get(sum_data, "carbonNum"),
            AlphaESSNames.TodayGeneration: self.dp.safe_get(sum_data, "epvtoday"),
            AlphaESSNames.TodayIncome: self.dp.safe_get(sum_data, "todayIncome"),
        }

        resolved = currency or fallback_currency or "Unknown"
        data[AlphaESSNames.CurrencyCode] = resolved
        data["Currency"] = resolved

        # Handle self consumption and sufficiency correctly
        self_consumption = self.dp.safe_get(sum_data, "eselfConsumption")
        self_sufficiency = self.dp.safe_get(sum_data, "eselfSufficiency")

        data[AlphaESSNames.SelfConsumption] = self_consumption * 100 if self_consumption is not None else None
        data[AlphaESSNames.SelfSufficiency] = self_sufficiency * 100 if self_sufficiency is not None else None

        return data

    def parse_energy_data(self, energy_data: dict) -> dict[str, Any]:
        """Parse daily energy flow data."""
        pv = self.dp.safe_get(energy_data, "epv")
        feedin = self.dp.safe_get(energy_data, "eOutput")
        gridcharge = self.dp.safe_get(energy_data, "eGridCharge")
        charge = self.dp.safe_get(energy_data, "eCharge")
        grid_consumption = self.dp.safe_get(energy_data, "eInput")
        discharge = self.dp.safe_get(energy_data, "eDischarge")
        ev_energy = self.dp.safe_get(energy_data, "eChargingPile")
        energy_date = self.dp.safe_get(energy_data, "theDate")

        return {
            AlphaESSNames.SolarProduction: pv,
            AlphaESSNames.SolarToLoad: self.dp.safe_calculate(pv, feedin),
            AlphaESSNames.SolarToGrid: feedin,
            AlphaESSNames.SolarToBattery: self.dp.safe_calculate(charge, gridcharge),
            AlphaESSNames.GridToLoad: grid_consumption,
            AlphaESSNames.GridToBattery: gridcharge,
            AlphaESSNames.Charge: charge,
            AlphaESSNames.Discharge: discharge,
            AlphaESSNames.EVCharger: ev_energy,
            AlphaESSNames.DailyPvGeneration: pv,
            AlphaESSNames.DailyGridConsumption: grid_consumption,
            AlphaESSNames.DailyFeedIn: feedin,
            AlphaESSNames.DailyGridCharge: gridcharge,
            AlphaESSNames.DailyBatteryCharge: charge,
            AlphaESSNames.DailyBatteryDischarge: discharge,
            AlphaESSNames.DailyEvChargingEnergy: ev_energy,
            AlphaESSNames.DailyEnergyDate: energy_date,
        }

    def parse_power_data(self, power_data: dict, one_day_power: list | None) -> dict[str, Any]:
        """Parse instantaneous power data."""
        soc = self.dp.safe_get(power_data, "soc")
        grid_details = power_data.get("pgridDetail", {})
        pv_details = power_data.get("ppvDetail", {})
        ev_details = power_data.get("pevDetail", {})

        data = {
            AlphaESSNames.BatterySOC: soc,
            AlphaESSNames.BatteryIO: self.dp.safe_get(power_data, "pbat"),
            AlphaESSNames.Load: self.dp.safe_get(power_data, "pload"),
            AlphaESSNames.Generation: self.dp.safe_get(power_data, "ppv"),
            AlphaESSNames.GridIOTotal: self.dp.safe_get(power_data, "pgrid"),
            AlphaESSNames.pev: self.dp.safe_get(power_data, "pev"),
            AlphaESSNames.PrealL1: self.dp.safe_get(power_data, "prealL1"),
            AlphaESSNames.PrealL2: self.dp.safe_get(power_data, "prealL2"),
            AlphaESSNames.PrealL3: self.dp.safe_get(power_data, "prealL3"),
        }

        # PV string data
        for i in range(1, 5):
            data[getattr(AlphaESSNames, f"PPV{i}")] = self.dp.safe_get(pv_details, f"ppv{i}")

        data[AlphaESSNames.pmeterDc] = self.dp.safe_get(pv_details, "pmeterDc")

        # Grid phase data
        for i in range(1, 4):
            data[getattr(AlphaESSNames, f"GridIOL{i}")] = self.dp.safe_get(grid_details, f"pmeterL{i}")

        # EV power data
        for i in range(1, 5):
            key_map = {1: "One", 2: "Two", 3: "Three", 4: "Four"}
            ev_power = self.dp.safe_get(ev_details, f"ev{i}Power")
            if ev_power is not None:
                data[getattr(AlphaESSNames, f"ElectricVehiclePower{key_map[i]}")] = ev_power

        # Fallback SOC from daily data
        if one_day_power and soc == 0:
            first_entry = one_day_power[0]
            cbat = first_entry.get("cbat")
            if cbat is not None:
                data[AlphaESSNames.StateOfCharge] = cbat

        return data

    def parse_charge_config(self, config: dict) -> dict[str, Any]:
        """Parse charge configuration."""
        data = {}
        for key in ["gridCharge", AlphaESSNames.batHighCap]:
            if key == AlphaESSNames.batHighCap:
                data[key] = self.dp.safe_get(config, "batHighCap")
            else:
                data[key] = self.dp.safe_get(config, key)

        # Parse time slots with the correct key names
        time_start_1 = self.dp.safe_get(config, "timeChaf1")
        time_end_1 = self.dp.safe_get(config, "timeChae1")
        time_start_2 = self.dp.safe_get(config, "timeChaf2")
        time_end_2 = self.dp.safe_get(config, "timeChae2")

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

    def parse_discharge_config(self, config: dict) -> dict[str, Any]:
        """Parse discharge configuration."""
        data = {}
        for key in ["ctrDis", AlphaESSNames.batUseCap]:
            if key == AlphaESSNames.batUseCap:
                data[key] = self.dp.safe_get(config, "batUseCap")
            else:
                data[key] = self.dp.safe_get(config, key)

        # Parse time slots with the correct key names
        time_start_1 = self.dp.safe_get(config, "timeDisf1")
        time_end_1 = self.dp.safe_get(config, "timeDise1")
        time_start_2 = self.dp.safe_get(config, "timeDisf2")
        time_end_2 = self.dp.safe_get(config, "timeDise2")

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


class AlphaESSDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Class to manage fetching data from the API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: alphaess.alphaess,
        ip_address_map: dict[str, str | None] | None = None,
        inverter_models: list[str] | None = None,
        entry: ConfigEntry | None = None,
        scan_interval: timedelta | None = None,
        alt_polling_mode: bool = False,
        fast_scan_interval: timedelta | None = None,
    ) -> None:
        """Initialize coordinator."""
        self.alt_polling_mode = alt_polling_mode
        self._full_poll_interval = scan_interval or SCAN_INTERVAL
        self._fast_scan_interval = fast_scan_interval or timedelta(seconds=DEFAULT_FAST_SCAN_INTERVAL_SECONDS)

        # In alt mode the coordinator ticks at the fast interval;
        # full polls happen when _full_poll_interval has elapsed.
        effective_interval = self._fast_scan_interval if alt_polling_mode else self._full_poll_interval

        super().__init__(
            hass,
            _LOGGER,
            # Pass the entry explicitly: relying on the ContextVar is
            # deprecated and breaks in HA 2026.8.
            config_entry=entry,
            name=DOMAIN,
            update_interval=effective_interval,
        )
        self.api = client
        self.hass = hass
        self.data: dict[str, dict[str, Any]] = {}
        self.entry = entry

        self._last_full_poll: float | None = None  # monotonic timestamp of last full poll

        # Stagger fast polls: round-robin index across inverters
        self._fast_poll_index: int = 0

        # Per-inverter consecutive error count for backoff
        self._inverter_error_count: dict[str, int] = {}
        self._ERROR_BACKOFF_THRESHOLD = 3  # skip inverter after this many consecutive failures
        self._ERROR_BACKOFF_CYCLES = 5     # retry every N cycles when backed off

        # Poll diagnostics (exposed as sensor data per-serial)
        self._last_poll_type: str = "none"
        self._last_full_poll_utc: str | None = None
        self._poll_tick_count: int = 0

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

        # Per-serial throttle tracking for charge/discharge buttons (monotonic timestamps)
        self.last_discharge_update: dict[str, float] = {}
        self.last_charge_update: dict[str, float] = {}

        # Per-serial user settings from number entities (batUseCap/batHighCap),
        # keyed by serial then setting key. Replaces the old hass.data[DOMAIN][serial] store.
        self.number_settings: dict[str, dict[str, float]] = {}

        # Guards temporary mutation of the shared API client's ipaddress
        self._local_ip_lock = asyncio.Lock()

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

    def set_number_setting(self, serial: str, key: str, value: float) -> None:
        """Store a per-inverter number setting (e.g. batUseCap/batHighCap)."""
        self.number_settings.setdefault(serial, {})[key] = value

    def get_number_setting(self, serial: str, key: str, default: float | None = None) -> float | None:
        """Read a per-inverter number setting."""
        return self.number_settings.get(serial, {}).get(key, default)

    def get_ev_charger_subentry_id(self, ev_serial: str) -> str | None:
        """Get the subentry ID for an EV charger by its serial number."""
        return self._ev_charger_subentry_map.get(ev_serial)

    async def set_ev_charger_current(self, serial: str, value: int) -> None:
        """Set EV charger current setting."""
        result = await self.api.setEvChargerCurrentsBySn(serial, value)
        _LOGGER.info(
            "Set EV charger current for %s to %sA - Result: %s",
            serial, value, result,
        )
        await self.async_request_refresh()

    def get_ev_charger_status_raw(self, serial: str) -> int | None:
        """Return EV charger raw status if available."""
        serial_data = self.data.get(serial, {})
        status = serial_data.get(AlphaESSNames.evchargerstatusraw)
        if status is None:
            status = serial_data.get(AlphaESSNames.evchargerstatus)

        try:
            return int(status) if status is not None else None
        except (TypeError, ValueError):
            return None

    def can_control_ev(self, serial: str, direction: int) -> bool:
        """Validate if EV remote command is compatible with current charger state.

        Direction: 0 = stop, 1 = start.
        """
        status = self.get_ev_charger_status_raw(serial)
        if status is None:
            return False

        if direction == 1:
            return status in (2, 4, 5, 6)
        if direction == 0:
            return status in (3, 4, 5)
        return False

    async def control_ev(self, serial: str, ev_serial: str, direction: str) -> None:
        """Control EV charger."""
        parsed_direction = int(direction)
        if not self.can_control_ev(serial, parsed_direction):
            _LOGGER.warning(
                "Skipping EV control command for %s (%s), direction=%s due to incompatible state=%s",
                serial,
                ev_serial,
                direction,
                self.get_ev_charger_status_raw(serial),
            )
            return

        result = await self.api.remoteControlEvCharger(serial, ev_serial, direction)
        _LOGGER.info(
            "Control EV Charger: %s for serial: %s Direction: %s - Result: %s",
            ev_serial, serial, direction, result,
        )

    async def reset_config(self, serial: str) -> None:
        """Reset charge and discharge configuration."""
        bat_use_cap = self.get_number_setting(serial, "batUseCap", 10)
        bat_high_cap = self.get_number_setting(serial, "batHighCap", 90)

        results = await self._reset_charge_discharge_config(serial, bat_high_cap, bat_use_cap)
        _LOGGER.info(
            "Reset Charge and Discharge configuration - Charge: %s, Discharge: %s",
            results["charge"], results["discharge"],
        )
        # Optimistically update so switches reflect the change immediately
        if serial in self.data:
            self.data[serial]["gridCharge"] = 1
            self.data[serial]["ctrDis"] = 1
            self.async_set_updated_data(self.data)

    async def _reset_charge_discharge_config(
            self, serial: str, bat_high_cap: int, bat_use_cap: int
    ) -> dict[str, Any]:
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
        bat_use_cap = self.get_number_setting(serial, name, 10)
        start_time, end_time = self.time_helper.calculate_time_window(time_period)

        result = await self.api.updateDisChargeConfigInfo(
            serial, bat_use_cap, 1, end_time, "00:00", start_time, "00:00"
        )

        _LOGGER.info(
            "Updated discharge config - Capacity: %s, Period: %s to %s, Result: %s",
            bat_use_cap, start_time, end_time, result,
        )
        # Optimistically update so the discharge switch reflects enabled immediately
        if serial in self.data:
            self.data[serial]["ctrDis"] = 1
            self.async_set_updated_data(self.data)

    async def update_charge(self, name: str, serial: str, time_period: int) -> None:
        """Update charge configuration for specified time period."""
        bat_high_cap = self.get_number_setting(serial, name, 90)
        start_time, end_time = self.time_helper.calculate_time_window(time_period)

        result = await self.api.updateChargeConfigInfo(
            serial, bat_high_cap, 1, end_time, "00:00", start_time, "00:00"
        )

        _LOGGER.info(
            "Updated charge config - Capacity: %s, Period: %s to %s, Result: %s",
            bat_high_cap, start_time, end_time, result,
        )
        # Optimistically update so the charge switch reflects enabled immediately
        if serial in self.data:
            self.data[serial]["gridCharge"] = 1
            self.async_set_updated_data(self.data)

    async def _async_update_data(self) -> dict[str, dict[str, Any]] | None:
        """Update data via library."""
        if self.data is None:
            self.data = {}

        if self.alt_polling_mode:
            return await self._async_update_data_alt()

        self._poll_tick_count += 1
        self._last_poll_type = "normal"

        try:
            throttle_delay = self.throttle_multiplier

            # Get list of registered inverters
            units = await self.api.getESSList()
            if not units:
                return self.data

            # Fetch data per-inverter separately
            any_success = False
            for idx, unit in enumerate(units):
                serial = unit.get("sysSn")
                if not serial:
                    continue

                # Per-inverter error backoff
                err_count = self._inverter_error_count.get(serial, 0)
                if err_count >= self._ERROR_BACKOFF_THRESHOLD:
                    if self._poll_tick_count % self._ERROR_BACKOFF_CYCLES != 0:
                        _LOGGER.debug(
                            "Skipping %s (backed off, %s consecutive errors)",
                            serial, err_count,
                        )
                        continue

                try:
                    invertor = await self._fetch_inverter_data(
                        serial, unit, throttle_delay, get_power=True, get_ev=True,
                        include_local_ip=(idx == 0),
                    )
                    inverter_data = self._parse_inverter_data(invertor)
                    self.data[serial] = inverter_data
                    self._inverter_error_count[serial] = 0
                    any_success = True
                except asyncio.CancelledError:
                    raise
                except aiohttp.ClientResponseError as err:
                    if err.status == 401:
                        raise ConfigEntryAuthFailed("AlphaESS credentials rejected") from err
                    _LOGGER.warning("Error fetching data for %s: %s", serial, err)
                    self._inverter_error_count[serial] = self._inverter_error_count.get(serial, 0) + 1
                except Exception as err:
                    _LOGGER.warning("Error fetching data for %s: %s", serial, err)
                    self._inverter_error_count[serial] = self._inverter_error_count.get(serial, 0) + 1

            # Fetch local IP data per-inverter for those with configured IPs
            await self._fetch_per_inverter_local_data()

            self.cloud_available = any_success
            if any_success:
                self._last_full_poll_utc = dt_util.utcnow().isoformat(timespec="seconds")
            else:
                _LOGGER.warning("All per-inverter fetches failed")
            return self._finalize_data()

        except ConfigEntryAuthFailed:
            raise
        except (aiohttp.ClientConnectorError, aiohttp.ClientResponseError, TypeError) as error:
            _LOGGER.warning("Cloud API error: %s", error)
            self.cloud_available = False
            self._update_diagnostics()
            return await self._fallback_to_local_data(error)
        except Exception as error:
            _LOGGER.error("Unexpected error fetching data: %s", error)
            self.cloud_available = False
            self._update_diagnostics()
            return await self._fallback_to_local_data(error)

    async def _async_update_data_alt(self) -> dict[str, dict[str, Any]] | None:
        """Alt polling mode: fast poll for live power data, full poll at scan_interval cadence."""
        now = time_mod.monotonic()
        self._poll_tick_count += 1
        need_full = (
            self._last_full_poll is None
            or (now - self._last_full_poll) >= self._full_poll_interval.total_seconds()
        )

        try:
            throttle_delay = self.throttle_multiplier

            if need_full:
                # Full poll — per-inverter API calls
                self._last_poll_type = "full"
                _LOGGER.debug("Alt mode: performing full poll")
                units = await self.api.getESSList()
                if not units:
                    return self.data

                any_success = False
                for idx, unit in enumerate(units):
                    serial = unit.get("sysSn")
                    if not serial:
                        continue
                    try:
                        invertor = await self._fetch_inverter_data(
                            serial, unit, throttle_delay, get_power=True, get_ev=True,
                            include_local_ip=(idx == 0),
                        )
                        inverter_data = self._parse_inverter_data(invertor)
                        self.data[serial] = inverter_data
                        # Clear error count on success
                        self._inverter_error_count[serial] = 0
                        any_success = True
                    except asyncio.CancelledError:
                        raise
                    except aiohttp.ClientResponseError as err:
                        if err.status == 401:
                            raise ConfigEntryAuthFailed("AlphaESS credentials rejected") from err
                        _LOGGER.debug("Alt mode full poll failed for %s: %s", serial, err)
                        self._inverter_error_count[serial] = self._inverter_error_count.get(serial, 0) + 1
                    except Exception as err:
                        _LOGGER.debug("Alt mode full poll failed for %s: %s", serial, err)
                        self._inverter_error_count[serial] = self._inverter_error_count.get(serial, 0) + 1

                self.cloud_available = any_success
                if any_success:
                    self._last_full_poll = now
                    self._last_full_poll_utc = dt_util.utcnow().isoformat(timespec="seconds")
                else:
                    _LOGGER.warning("Alt mode: all per-inverter fetches failed during full poll")
            else:
                # Fast poll — stagger: pick one inverter per tick (round-robin)
                self._last_poll_type = "fast"
                serials = list(self.data.keys())
                if serials:
                    serial = serials[self._fast_poll_index % len(serials)]
                    self._fast_poll_index += 1

                    # Per-inverter error backoff
                    err_count = self._inverter_error_count.get(serial, 0)
                    if err_count >= self._ERROR_BACKOFF_THRESHOLD:
                        # Retry every N cycles to see if it recovers
                        if self._poll_tick_count % self._ERROR_BACKOFF_CYCLES != 0:
                            _LOGGER.debug(
                                "Alt mode: skipping %s (backed off, %s consecutive errors)",
                                serial, err_count,
                            )
                            return self._finalize_data()

                    _LOGGER.debug("Alt mode: fast poll for %s", serial)
                    try:
                        # getLastPowerData — real-time watts/SOC (skip for unsupported models)
                        model = self.data[serial].get("Model")
                        if model not in LOWER_INVERTER_API_CALL_LIST:
                            power_data = await self.api.getLastPowerData(serial)
                            if power_data:
                                parsed = self.parser.parse_power_data(power_data, None)
                                self.data[serial].update(parsed)
                            await asyncio.sleep(throttle_delay)

                        # getOneDateEnergyBySn — daily energy counters
                        energy_data = await self.api.getOneDateEnergyBySn(
                            serial, dt_util.now().strftime("%Y-%m-%d")
                        )
                        if energy_data:
                            parsed = self.parser.parse_energy_data(energy_data)
                            self.data[serial].update(parsed)
                        await asyncio.sleep(throttle_delay)

                        # EV charger status if one is known
                        ev_sn = self.data[serial].get(AlphaESSNames.evchargersn)
                        if ev_sn:
                            ev_status = await self.api.getEvChargerStatusBySn(serial, ev_sn)
                            if ev_status:
                                self.data[serial][AlphaESSNames.evchargerstatus] = ev_status.get("evchargerStatus")
                                self.data[serial][AlphaESSNames.evchargerstatusraw] = ev_status.get("evchargerStatus")
                            await asyncio.sleep(throttle_delay)

                        # Clear error count on success
                        self._inverter_error_count[serial] = 0
                    except asyncio.CancelledError:
                        raise
                    except aiohttp.ClientResponseError as err:
                        if err.status == 401:
                            raise ConfigEntryAuthFailed("AlphaESS credentials rejected") from err
                        _LOGGER.debug("Alt mode fast poll failed for %s: %s", serial, err)
                        self._inverter_error_count[serial] = self._inverter_error_count.get(serial, 0) + 1
                    except Exception as err:
                        _LOGGER.debug("Alt mode fast poll failed for %s: %s", serial, err)
                        self._inverter_error_count[serial] = self._inverter_error_count.get(serial, 0) + 1

            # Fetch local IP data per-inverter for those with configured IPs
            await self._fetch_per_inverter_local_data()

            return self._finalize_data()

        except ConfigEntryAuthFailed:
            raise
        except (aiohttp.ClientConnectorError, aiohttp.ClientResponseError, TypeError) as error:
            _LOGGER.warning("Cloud API error (alt mode): %s", error)
            self.cloud_available = False
            self._update_diagnostics()
            return await self._fallback_to_local_data(error)
        except Exception as error:
            _LOGGER.error("Unexpected error fetching data (alt mode): %s", error)
            self.cloud_available = False
            self._update_diagnostics()
            return await self._fallback_to_local_data(error)

    def _update_diagnostics(self) -> None:
        """Write poll diagnostic data into each inverter's data dict."""
        for serial in self.data:
            self.data[serial][AlphaESSNames.PollMode] = "alt" if self.alt_polling_mode else "normal"
            self.data[serial][AlphaESSNames.LastPollType] = self._last_poll_type
            self.data[serial][AlphaESSNames.LastFullPoll] = self._last_full_poll_utc or "never"
            self.data[serial][AlphaESSNames.PollTickCount] = self._poll_tick_count

    def _finalize_data(self) -> dict[str, dict[str, Any]]:
        """Write diagnostics and return a shallow per-serial copy of the data.

        Returning fresh dict objects each cycle ensures listeners comparing
        old/new data never see the same mutated reference.
        """
        self._update_diagnostics()
        return {serial: dict(values) for serial, values in self.data.items()}

    async def _fetch_inverter_data(
        self,
        serial: str,
        unit: dict,
        throttle_delay: float,
        get_power: bool = False,
        get_ev: bool = False,
        include_local_ip: bool = False,
    ) -> dict[str, Any]:
        """Fetch all API data for a single inverter by its serial number."""
        today = dt_util.now().strftime("%Y-%m-%d")

        unit["SumData"] = await self.api.getSumDataForCustomer(serial)
        await asyncio.sleep(throttle_delay)

        unit["OneDateEnergy"] = await self.api.getOneDateEnergyBySn(serial, today)
        await asyncio.sleep(throttle_delay)

        # Skip getLastPowerData for inverters that don't support it
        if unit.get("minv") not in LOWER_INVERTER_API_CALL_LIST:
            unit["LastPower"] = await self.api.getLastPowerData(serial)
            await asyncio.sleep(throttle_delay)

        unit["ChargeConfig"] = await self.api.getChargeConfigInfo(serial)
        await asyncio.sleep(throttle_delay)

        unit["DisChargeConfig"] = await self.api.getDisChargeConfigInfo(serial)
        await asyncio.sleep(throttle_delay)

        if get_power:
            unit["OneDayPower"] = await self.api.getOneDayPowerBySn(serial, today)
            await asyncio.sleep(throttle_delay)

        if get_ev:
            try:
                unit["EVData"] = await self.api.getEvChargerConfigList(serial)
                await asyncio.sleep(throttle_delay)
                if unit["EVData"]:
                    ev_list = unit["EVData"]
                    ev_item = ev_list[0] if isinstance(ev_list, list) else ev_list
                    ev_serial = ev_item.get("evchargerSn")
                    if ev_serial:
                        unit["EVStatus"] = await self.api.getEvChargerStatusBySn(serial, ev_serial)
                        await asyncio.sleep(throttle_delay)
                        unit["EVCurrent"] = await self.api.getEvChargerCurrentsBySn(serial)
                        await asyncio.sleep(throttle_delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.debug("Failed to fetch EV data for %s", serial, exc_info=True)

        # Include local IP data if available and this is the first inverter
        if include_local_ip and self.api.ipaddress:
            try:
                ip_data = await self.api.getIPData()
                if ip_data:
                    unit["LocalIPData"] = {
                        "type": "local_ip_data",
                        "ip": self.api.ipaddress,
                        **ip_data,
                    }
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.debug("Failed to fetch local IP data", exc_info=True)

        return unit

    async def _fetch_per_inverter_local_data(self) -> None:
        """Fetch local IP data for each inverter that has a configured IP.

        Temporarily sets the API client's ipaddress for each call under a lock
        (the client instance is shared), then resets it to None.
        """
        for serial, ip in self.ip_address_map.items():
            if not ip or serial not in self.data:
                continue

            # Skip if cloud API already provided LocalIPData for this inverter
            if self.data[serial].get(AlphaESSNames.localIP):
                continue

            async with self._local_ip_lock:
                try:
                    self.api.ipaddress = ip
                    local_ip_raw = await self.api.getIPData()
                    if local_ip_raw:
                        local_ip_data = {"ip": ip, **local_ip_raw}
                        parsed = self.parser.parse_local_ip_data(local_ip_data)
                        self.data[serial].update(parsed)
                        _LOGGER.debug("Fetched local IP data for %s from %s", serial, ip)
                except Exception as error:
                    _LOGGER.debug("Could not fetch local IP data for %s from %s: %s", serial, ip, error)
                finally:
                    self.api.ipaddress = None

    async def _fallback_to_local_data(
        self, original_error: Exception | None = None
    ) -> dict[str, dict[str, Any]] | None:
        """Attempt to fetch local IP data when cloud API is unavailable.

        Uses per-inverter IP addresses from subentry configuration.
        Cloud sensor keys are removed so those entities become unavailable.
        Local IP sensor keys are kept with fresh data.

        Raises UpdateFailed when no data source is available at all, so
        HA marks the update as failed instead of silently keeping stale data.
        """
        has_any_local_ip = any(ip for ip in self.ip_address_map.values() if ip)

        if not has_any_local_ip:
            _LOGGER.debug("No local IP configured for any inverter")
            raise UpdateFailed(
                f"Cloud API unavailable and no local IP configured: {original_error}"
            ) from original_error

        any_success = False

        for serial, ip in self.ip_address_map.items():
            if not ip:
                # No IP for this inverter - clear cloud data but keep model
                if serial in self.data:
                    model = self.data[serial].get("Model")
                    self.data[serial] = {"Model": model}
                continue

            async with self._local_ip_lock:
                try:
                    self.api.ipaddress = ip
                    local_ip_raw = await self.api.getIPData()
                    if local_ip_raw:
                        local_ip_data = {"ip": ip, **local_ip_raw}
                        parsed = self.parser.parse_local_ip_data(local_ip_data)
                        model = self.data.get(serial, {}).get("Model")
                        self.data[serial] = {"Model": model, **parsed}
                        any_success = True
                        _LOGGER.info("Cloud unavailable - using local data for %s from %s", serial, ip)
                    else:
                        model = self.data.get(serial, {}).get("Model")
                        self.data[serial] = {"Model": model}
                except Exception as error:
                    _LOGGER.warning("Local IP fetch failed for %s (%s): %s", serial, ip, error)
                    model = self.data.get(serial, {}).get("Model")
                    self.data[serial] = {"Model": model}
                finally:
                    self.api.ipaddress = None

        if not any_success:
            _LOGGER.warning("Cloud API unavailable and all local IP fetches failed")
            raise UpdateFailed(
                f"Cloud API unavailable and all local IP fetches failed: {original_error}"
            ) from original_error

        return self._finalize_data()

    def _parse_inverter_data(self, invertor: dict) -> dict[str, Any]:
        """Parse all data for a single inverter."""
        # Start with basic info
        data = self.parser.parse_basic_info(invertor)

        # Add LocalIPData if available
        local_ip_data = invertor.get("LocalIPData", {})
        if local_ip_data:
            data.update(self.parser.parse_local_ip_data(local_ip_data))

        # Add EV data if available
        ev_data = invertor.get("EVData", {})
        if ev_data:
            data.update(self.parser.parse_ev_data(ev_data, invertor))

        # Add summary data
        sum_data = invertor.get("SumData", {})
        if sum_data:
            data.update(self.parser.parse_summary_data(sum_data, fallback_currency=self.hass.config.currency))

        # Add energy data
        energy_data = invertor.get("OneDateEnergy", {})
        if energy_data:
            data.update(self.parser.parse_energy_data(energy_data))

        # Add power data
        power_data = invertor.get("LastPower", {})
        if power_data:
            one_day_power = invertor.get("OneDayPower", {})
            data.update(self.parser.parse_power_data(power_data, one_day_power))

        # Add configuration data
        charge_config = invertor.get("ChargeConfig", {})
        if charge_config:
            data.update(self.parser.parse_charge_config(charge_config))

        discharge_config = invertor.get("DisChargeConfig", {})
        if discharge_config:
            data.update(self.parser.parse_discharge_config(discharge_config))

        # Add Charging Range (combining charge and discharge data)
        if charge_config or discharge_config:
            bat_high_cap = charge_config.get("batHighCap", 90) if charge_config else 90
            bat_use_cap = discharge_config.get("batUseCap", 10) if discharge_config else 10
            data[AlphaESSNames.ChargeRange] = f"{bat_use_cap}% - {bat_high_cap}%"

        return data
