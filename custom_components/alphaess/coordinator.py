"""Coordinator for AlphaEss integration."""
import asyncio
import logging
import math
import time as time_mod
from copy import deepcopy
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from alphaess import alphaess
from alphaess.alphaess import AlphaESSApiError

from .const import (
    AUTH_FAILURE_CODES,
    CONF_SERIAL_NUMBER,
    DEFAULT_FAST_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    FAST_API_CALL_INTERVAL_SECONDS,
    LOWER_INVERTER_API_CALL_LIST,
    MAX_LOGGED_RESPONSE_CHARS,
    MIN_API_CALL_INTERVAL_SECONDS,
    RATE_LIMIT_CODE,
    SCAN_INTERVAL,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from .currency import normalize_currency_unit
from .enums import AlphaESSNames

_LOGGER = logging.getLogger(__name__)

# --- Periodic (setTimeChargeBySn) schedule -----------------------------------
# executeCycleType 0 = daily, 1 = weekly. Existing values are preserved. The
# two-slot HA surface has no weekday selector. A new weekly period can therefore
# only inherit the explicit weekdays of an existing period on the same side.
PERIODIC_DAILY = 0

# chargeLimit is documented as [10, 100]; anything outside returns 6001.
PERIODIC_MIN_CHARGE_LIMIT = 10
PERIODIC_MAX_CHARGE_LIMIT = 100

# Values for the "Periodic Schedule Read" diagnostic sensor. This reports
# whether getTimeChargeBySn can be read on this account. A partial write cannot
# safely preserve this full-replacement resource unless the read succeeds.
PERIODIC_READ_OK = "readable"
PERIODIC_READ_UNAVAILABLE = "unreadable"
PERIODIC_READ_UNKNOWN = "unknown"

# "No operation permissions" — this system is not entitled to the periodic
# endpoints. Documented as permanent, so it is worth caching rather than
# retrying. See docs/RETURN_CODES.md in alphaess-openAPI.
PERIODIC_NOT_ENTITLED = 6017

# "Set failed". Overlap is one possible cause documented by the upstream
# project, but the return code itself is generic.
PERIODIC_OVERLAP = 6008


# Scope under which responses from calls that name no system (getESSList)
# are retained for the export_raw_snapshot service.
RAW_ACCOUNT_SCOPE = "account"
# Bind/unbind calls carry a check code or a verification code in their
# arguments; those arguments are never retained.
RAW_UNRECORDED_ARGS = frozenset({"getVerificationCode", "bindSn", "unBindSn"})


class ScheduleWriteError(HomeAssistantError):
    """Raised when a full-replacement schedule cannot be applied safely."""


class ScheduleConflictError(ScheduleWriteError):
    """Raised when a remote schedule changed while a local draft was open."""


class SchedulePartialWriteError(ScheduleWriteError):
    """Raised when one backup store accepted a change and another did not.

    Only possible in legacy backup mode, where charge and discharge are two
    separate stores. The periodic (primary) mode is a single store and cannot
    partially fail.
    """


class ScheduleWriteUnknownError(ScheduleWriteError):
    """Raised when a write response was lost and remote state is unknown."""


class SchedulePartialWriteUnknownError(
    SchedulePartialWriteError, ScheduleWriteUnknownError
):
    """Raised when one store is known-good and another write is indeterminate."""



def describe_api_error(err: AlphaESSApiError) -> str:
    """Render a return code with the meaning the library has on file.

    The English text comes from RETURN_CODES / UNDOCUMENTED_RETURN_CODES in
    alphaess-openAPI, via the exception, so the wording stays in one place. A
    code the library doesn't recognise still shows its number.
    """
    described = str(err.code)
    if err.description:
        described += f" ({err.description})"
    if err.expMsg:
        described += f" - {err.expMsg}"
    return described




def _format_periods(periods: list[dict[str, Any]]) -> str:
    """Render a period list for a log line."""
    if not periods:
        return "none"
    return ", ".join(f"{p['beginTime']}-{p['endTime']}" for p in periods)


def _format_window(begin: Any, end: Any) -> str:
    """Render one two-slot window for the period sensors.

    AlphaESS has no "empty" value: an unused slot is stored with start equal
    to end (usually 00:00-00:00), which reads better as "Not set" than as a
    zero-length midnight window.
    """
    if not begin or not end or begin == end:
        return "Not set"
    return f"{begin} - {end}"


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

    def parse_ev_data(
        self, ev_data: list[dict[str, Any]] | dict[str, Any] | None, invertor: dict
    ) -> dict[str, Any]:
        """Parse EV charger data."""
        if not ev_data:
            return {}

        ev_count = len(ev_data) if isinstance(ev_data, list) else 1
        ev_data = ev_data[0] if isinstance(ev_data, list) else ev_data
        ev_status = invertor.get("EVStatus", {})
        ev_current = invertor.get("EVCurrent", {})

        return {
            AlphaESSNames.evchargersn: self.dp.safe_get(ev_data, "evchargerSn"),
            AlphaESSNames.evchargermodel: self.dp.safe_get(ev_data, "evchargerModel"),
            AlphaESSNames.evchargercount: ev_count,
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

        # moneyType is sometimes an ISO code and sometimes the symbol; the
        # sensor named "Currency Code" should report a code either way, and so
        # should the diagnostics download.
        resolved = normalize_currency_unit(currency, fallback_currency) or "Unknown"
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
        """Parse the legacy charge configuration (backup mode only)."""
        data: dict[str, Any] = {
            "gridCharge": self.dp.safe_get(config, "gridCharge"),
            AlphaESSNames.batHighCap: self.dp.safe_get(config, "batHighCap"),
        }

        time_start_1 = self.dp.safe_get(config, "timeChaf1")
        time_end_1 = self.dp.safe_get(config, "timeChae1")
        time_start_2 = self.dp.safe_get(config, "timeChaf2")
        time_end_2 = self.dp.safe_get(config, "timeChae2")

        data[AlphaESSNames.ChargeTime1] = _format_window(time_start_1, time_end_1)
        data[AlphaESSNames.ChargeTime2] = _format_window(time_start_2, time_end_2)

        data["charge_timeChaf1"] = time_start_1
        data["charge_timeChae1"] = time_end_1
        data["charge_timeChaf2"] = time_start_2
        data["charge_timeChae2"] = time_end_2
        return data

    def parse_discharge_config(self, config: dict) -> dict[str, Any]:
        """Parse the legacy discharge configuration (backup mode only)."""
        data: dict[str, Any] = {
            "ctrDis": self.dp.safe_get(config, "ctrDis"),
            AlphaESSNames.batUseCap: self.dp.safe_get(config, "batUseCap"),
        }

        time_start_1 = self.dp.safe_get(config, "timeDisf1")
        time_end_1 = self.dp.safe_get(config, "timeDise1")
        time_start_2 = self.dp.safe_get(config, "timeDisf2")
        time_end_2 = self.dp.safe_get(config, "timeDise2")

        data[AlphaESSNames.DischargeTime1] = _format_window(time_start_1, time_end_1)
        data[AlphaESSNames.DischargeTime2] = _format_window(time_start_2, time_end_2)

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
        assume_schedule_flags_enabled: bool = False,
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
        # A completed coordinator cycle is published atomically, but normal
        # polling assembles the next cycle in ``self.data`` one inverter at a
        # time. Diagnostics must use the last complete cycle rather than
        # observing that in-progress mutation.
        self._diagnostics_data_snapshot: dict[str, dict[str, Any]] | None = None
        self._diagnostics_api_snapshot: dict[str, Any] | None = None
        self._diagnostics_schedule_snapshot: dict[str, dict[str, Any]] | None = None
        self._prefetched_ess_list: list[dict[str, Any]] | None = None
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

        # AlphaESS advises at least ten seconds between polling calls. Apply it
        # to every model; unsupported endpoints are skipped separately.
        self.throttle_multiplier = float(MIN_API_CALL_INTERVAL_SECONDS)
        self.has_throttle = True
        # All OpenAPI reads and writes share one gate. Poll sleeps alone cannot
        # protect a button/service call that arrives during a coordinator poll.
        self._api_request_lock = asyncio.Lock()
        self._last_api_request_completed: float | None = None
        # All calls run on the fast lane until the server ever answers 6053;
        # see _call_interval for the live-probe evidence behind it.
        self._fast_api_lane = True

        # Per-serial throttle tracking for charge/discharge buttons (monotonic timestamps)
        self.last_discharge_update: dict[str, float] = {}
        self.last_charge_update: dict[str, float] = {}

        # Per-serial user settings from number entities (batUseCap/batHighCap),
        # keyed by serial then setting key. Replaces the old hass.data[DOMAIN][serial] store.
        self.number_settings: dict[str, dict[str, float]] = {}

        # The periodic schedule API is the primary schedule store; keep its
        # last-known-good snapshot per serial. Systems that AlphaESS answers
        # 6017 for fall back to the legacy two-slot endpoints ("backup mode"),
        # whose snapshots live in _legacy_schedules. A system is in exactly
        # one mode; the stores are never mixed (issue #269).
        self._periodic_schedules: dict[str, dict[str, Any]] = {}
        self._legacy_schedules: dict[str, dict[str, dict[str, Any]]] = {}
        self._periodic_overlaid_serials: set[str] = set()

        # Periodic schedule (getTimeChargeBySn) readability per serial.
        # Missing = not yet determined, True/False = known answer.
        self._periodic_readable: dict[str, bool] = {}

        # gridChargeCycle/ctrDisCycle are write-only. getTimeChargeBySn answers
        # 0 for both whatever the inverter is doing, while setTimeChargeBySn
        # accepts the explicit pair. The read cannot be echoed back, and the
        # only record of what Home Assistant asked for is the one kept here,
        # fed by the two switches and confirmed by each successful write.
        self._periodic_enable_intent: dict[str, dict[str, int]] = {}
        self._periodic_enable_sent: dict[str, dict[str, int]] = {}
        # Every periodic write needs that pair. With this option on, a flag
        # Home Assistant has no record of is sent as 1 rather than refusing
        # the write: the escape hatch for systems that read the pair back as
        # 0/0 (#267) and have never had the switches set. It never overrides
        # a value the user gave, and the pair is recorded once a write lands.
        self.assume_schedule_flags_enabled = assume_schedule_flags_enabled
        self._assumed_flags_logged: set[str] = set()

        # The last answer from every endpoint, per system, for the
        # export_raw_snapshot service: what the API actually returned, as
        # opposed to the entity view parsed from it. Keyed by serial (or the
        # account scope for calls that name none) and then endpoint name.
        self._raw_responses: dict[str, dict[str, dict[str, Any]]] = {}

        # Last logged schedule capability state per serial, so changes are
        # reported once with their reason rather than on every poll.
        self._schedule_state_logged: dict[str, tuple] = {}

        # Entity changes are drafts. A user can edit start/end/limits/switches
        # without emitting a series of unsafe intermediate full replacements,
        # then commit once with the Apply Schedule button.
        self._schedule_drafts: dict[str, dict[str, dict[str, Any]]] = {}
        self._schedule_draft_dirty: dict[str, dict[str, set[str]]] = {}
        self._schedule_draft_base_periodic: dict[str, dict[str, Any]] = {}
        self._schedule_draft_base_legacy: dict[str, dict[str, dict[str, Any]]] = {}
        self._schedule_draft_revisions: dict[str, int] = {}
        self._schedule_write_revisions: dict[str, int] = {}
        self._schedule_apply_in_progress: set[str] = set()

        # Serialise every schedule read/modify/write transaction across time,
        # number, switch, button and service platforms.
        self._schedule_locks: dict[str, asyncio.Lock] = {}

        # Serials whose periodic *write* came back 6017. Unlike the read, this
        # is the endpoint we actually care about answering for itself, so once
        # it refuses there is no point asking again.
        self._periodic_write_denied: set[str] = set()

        # The current entity model exposes one EV charger per inverter. Do not
        # silently truncate the upstream list when an account has more.
        self._multi_ev_warned: set[str] = set()

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

    def _call_interval(self) -> float:
        """Return the current spacing between OpenAPI calls.

        At the documented 10-second spacing a two-inverter full poll takes
        minutes, which blocks HA startup and delays app-side schedule changes
        by up to five minutes. The new server was live-probed accepting reads at
        sub-second spacing without 6053, so everything runs at the fast pace
        and any 6053 drops the session straight back to the documented
        interval (with that one call retried once).
        """
        if self._fast_api_lane:
            return min(
                float(FAST_API_CALL_INTERVAL_SECONDS), self.throttle_multiplier
            )
        return self.throttle_multiplier

    @staticmethod
    def _describe_call(method, args, kwargs) -> str:
        """Render one call the way it would be written by hand."""
        shown = [repr(arg) for arg in args]
        shown += [f"{key}={value!r}" for key, value in kwargs.items()]
        return f"{getattr(method, '__name__', method)}({', '.join(shown)})"

    @staticmethod
    def _describe_response(value: Any) -> str:
        """Render a response, capping the ones that run to thousands of lines."""
        text = repr(value)
        if len(text) <= MAX_LOGGED_RESPONSE_CHARS:
            return text
        return f"{text[:MAX_LOGGED_RESPONSE_CHARS]}... [{len(text)} chars]"

    async def _call_cloud_api(self, method, *args, **kwargs) -> Any:
        """Call one OpenAPI endpoint after the currently required interval.

        With debug logging on, every request and every response is written out.
        Establishing what this API actually returns has needed hand-signed
        requests more than once (#267); it should come out of a log instead.
        The last answer from every endpoint is also retained per system, so
        the export_raw_snapshot service can hand over the same evidence
        without a log.
        """
        tracing = _LOGGER.isEnabledFor(logging.DEBUG)
        call = self._describe_call(method, args, kwargs) if tracing else ""
        waited = 0.0
        async with self._api_request_lock:
            if self._last_api_request_completed is not None:
                remaining = self._call_interval() - (
                    time_mod.monotonic() - self._last_api_request_completed
                )
                if remaining > 0:
                    waited = remaining
                    await asyncio.sleep(remaining)
            # Only read the clock when the answer will be used: tests and
            # production alike should not pay for tracing that is switched off.
            started = time_mod.monotonic() if tracing else 0.0
            try:
                result = await method(*args, **kwargs)
                if tracing:
                    _LOGGER.debug(
                        "API %s -> %s (%.2fs, waited %.2fs)",
                        call, self._describe_response(result),
                        time_mod.monotonic() - started, waited,
                    )
                self._record_raw(method, args, kwargs, response=result)
                return result
            except AlphaESSApiError as err:
                if err.code == RATE_LIMIT_CODE and self._fast_api_lane:
                    # The fast lane overshot this server's limits. Fall back
                    # to the documented spacing for the rest of the session
                    # and retry this one call once.
                    self._fast_api_lane = False
                    _LOGGER.info(
                        "AlphaESS rate-limited the fast lane (6053); "
                        "dropping to %ss spacing", self.throttle_multiplier,
                    )
                    await asyncio.sleep(self.throttle_multiplier)
                    try:
                        retried = await method(*args, **kwargs)
                    except AlphaESSApiError as retry_err:
                        self._record_raw(method, args, kwargs, error=retry_err)
                        raise
                    if tracing:
                        _LOGGER.debug(
                            "API %s retried after 6053 -> %s",
                            call, self._describe_response(retried),
                        )
                    self._record_raw(method, args, kwargs, response=retried)
                    return retried
                if tracing:
                    _LOGGER.debug(
                        "API %s -> %s (%.2fs, waited %.2fs)",
                        call, describe_api_error(err),
                        time_mod.monotonic() - started, waited,
                    )
                self._record_raw(method, args, kwargs, error=err)
                raise
            except Exception as err:
                if tracing:
                    _LOGGER.debug(
                        "API %s raised %s: %s (%.2fs, waited %.2fs)",
                        call, type(err).__name__, err,
                        time_mod.monotonic() - started, waited,
                    )
                self._record_raw(method, args, kwargs, exception=err)
                raise
            finally:
                # A rejected or indeterminate request still consumed an API
                # attempt and must participate in the next-call delay.
                self._last_api_request_completed = time_mod.monotonic()

    @staticmethod
    def _endpoint_name(method) -> str:
        """Return the OpenAPI method name a call went to."""
        return getattr(method, "__name__", None) or str(method)

    def _record_raw(
        self,
        method,
        args,
        kwargs,
        *,
        response: Any = None,
        error: AlphaESSApiError | None = None,
        exception: Exception | None = None,
    ) -> None:
        """Retain what an endpoint last answered, for export_raw_snapshot.

        One record per endpoint per system: the most recent call replaces the
        previous one, so memory stays bounded however long the entry runs.
        A rejection is kept as its return code, a transport failure as the
        exception, so a report can show which it was.
        """
        endpoint = self._endpoint_name(method)
        serial = kwargs.get("sysSn")
        if serial is None and args and isinstance(args[0], str):
            serial = args[0]
        scope = serial if isinstance(serial, str) and serial else RAW_ACCOUNT_SCOPE
        record: dict[str, Any] = {
            "captured_at": dt_util.utcnow().isoformat(timespec="seconds"),
        }
        if endpoint not in RAW_UNRECORDED_ARGS:
            record["request"] = {
                "args": [deepcopy(arg) for arg in args],
                "kwargs": deepcopy(kwargs),
            }
        if error is not None:
            record["error"] = {"code": error.code, "message": str(error)}
        elif exception is not None:
            record["exception"] = f"{type(exception).__name__}: {exception}"
        else:
            record["response"] = deepcopy(response)
        self._raw_responses.setdefault(scope, {})[endpoint] = record

    def _record_local_raw(self, serial: str, ip: str | None, response: Any) -> None:
        """Retain a local dongle answer; it bypasses the cloud gate."""
        self._raw_responses.setdefault(serial, {})["getIPData"] = {
            "captured_at": dt_util.utcnow().isoformat(timespec="seconds"),
            "request": {"ip": ip},
            "response": deepcopy(response),
        }

    def raw_responses(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Return a copy of the last response from every endpoint."""
        return deepcopy(self._raw_responses)

    async def _async_wait_for_api_interval(self) -> None:
        """Hold the API gate until a just-completed request is safe to follow."""
        async with self._api_request_lock:
            if self._last_api_request_completed is None:
                return
            remaining = self.throttle_multiplier - (
                time_mod.monotonic() - self._last_api_request_completed
            )
            if remaining > 0:
                await asyncio.sleep(remaining)

    async def async_request_verification_code(
        self, serial: str, check_code: str
    ) -> Any:
        """Request a bind code through the shared OpenAPI gate."""
        return await self._call_cloud_api(
            self.api.getVerificationCode, serial, check_code
        )

    async def async_bind_system(self, serial: str, code: str) -> Any:
        """Bind a system and leave enough space before setup reloads."""
        try:
            return await self._call_cloud_api(self.api.bindSn, serial, code)
        finally:
            await self._async_wait_for_api_interval()

    async def async_unbind_system(self, serial: str) -> Any:
        """Unbind a system and leave enough space before setup reloads."""
        try:
            return await self._call_cloud_api(self.api.unBindSn, serial)
        finally:
            await self._async_wait_for_api_interval()

    async def _read(self, method, *args) -> Any:
        """Run a polling read, tolerating an API-level rejection.

        The client runs with raise_on_error so writes can tell success from
        failure. Reads want the older, softer behaviour: an endpoint this
        account cannot use (6017) or a momentarily unhappy one should leave that
        one value missing, not abort the whole inverter fetch and trip the
        error backoff. Transport errors still propagate — those really do mean
        the inverter is unreachable.
        """
        try:
            return await self._call_cloud_api(method, *args)
        except AlphaESSApiError as err:
            if err.code in AUTH_FAILURE_CODES:
                raise ConfigEntryAuthFailed(
                    f"AlphaESS credentials rejected: {err}"
                ) from err
            _LOGGER.debug(
                "%s returned %s; continuing without it",
                getattr(method, "__name__", method), describe_api_error(err),
            )
            return None

    async def _async_get_ess_list(self) -> Any:
        """Consume setup's successful discovery result before polling again."""
        if self._prefetched_ess_list is not None:
            units = self._prefetched_ess_list
            self._prefetched_ess_list = None
            return units
        return await self._read(self.api.getESSList)

    def set_prefetched_ess_list(self, units: list[dict[str, Any]]) -> None:
        """Reuse setup discovery and account for the request in API spacing."""
        self._prefetched_ess_list = units
        self._last_api_request_completed = time_mod.monotonic()

    def _prune_unbound_systems(self, active_serials: set[str]) -> None:
        """Drop state for systems absent from a successful ESS-list response."""
        stale_serials = set(self.data) - active_serials
        for serial in stale_serials:
            _LOGGER.info("Removing unbound AlphaESS system %s from coordinator state", serial)
            self.data.pop(serial, None)
            for cache in (
                self._periodic_schedules,
                self._legacy_schedules,
                self._periodic_readable,
                self._schedule_drafts,
                self._schedule_draft_dirty,
                self._schedule_draft_base_periodic,
                self._schedule_draft_base_legacy,
                self._schedule_draft_revisions,
                self._schedule_write_revisions,
                self._periodic_enable_intent,
                self._periodic_enable_sent,
                self.number_settings,
                self._inverter_error_count,
                self.last_charge_update,
                self.last_discharge_update,
            ):
                cache.pop(serial, None)
            self._periodic_write_denied.discard(serial)
            self._periodic_overlaid_serials.discard(serial)
            self._multi_ev_warned.discard(serial)
            # Drop the write lock too, but never while a transaction holds it:
            # popping a held lock would let a new one be created alongside it.
            lock = self._schedule_locks.get(serial)
            if lock is not None and not lock.locked():
                self._schedule_locks.pop(serial, None)

    def get_inverter_subentry_id(self, serial: str) -> str | None:
        """Get the subentry ID for an inverter by its serial number."""
        return self._inverter_subentry_map.get(serial)

    def set_number_setting(self, serial: str, key: str, value: float) -> None:
        """Store a per-inverter number setting (e.g. batUseCap/batHighCap)."""
        self.number_settings.setdefault(serial, {})[key] = value

    def get_number_setting(self, serial: str, key: str, default: float | None = None) -> float | None:
        """Read a per-inverter number setting."""
        return self.number_settings.get(serial, {}).get(key, default)

    def clear_number_setting(self, serial: str, key: str) -> None:
        """Forget a stored setting so callers fall back to their default.

        Storing None instead would shadow that default, since get_number_setting
        only substitutes when the key is absent.
        """
        self.number_settings.get(serial, {}).pop(key, None)

    def get_ev_charger_subentry_id(self, ev_serial: str) -> str | None:
        """Get the subentry ID for an EV charger by its serial number."""
        return self._ev_charger_subentry_map.get(ev_serial)

    async def set_ev_charger_current(self, serial: str, value: int) -> None:
        """Set EV charger current setting."""
        result = await self._call_cloud_api(
            self.api.setEvChargerCurrentsBySn,
            sysSn=serial, currentsetting=value,
        )
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
        """Whether an EV command looks compatible with the last known state.

        Direction: 0 = stop, 1 = start.

        Advisory only, and used for the Can Start/Stop Charging binary sensors.
        It deliberately does not gate control_ev: the status behind it is as
        old as the last poll, and our mapping of states to allowed commands is
        our reading of the API rather than the API's own rule.
        """
        status = self.get_ev_charger_status_raw(serial)
        if status is None:
            return False

        if direction == 1:
            return status in (2, 4, 5, 6)
        if direction == 0:
            return status in (3, 4, 5)
        return False

    async def control_ev(self, serial: str, ev_serial: str, direction: int | str) -> None:
        """Control EV charger.

        Sent regardless of the last known charger state. Refusing locally meant
        a status that was merely stale, or missing because the status endpoint
        had failed, silently swallowed the command; and with no status at all it
        blocked every command indefinitely. The charger is the authority on what
        it will accept, so ask it and report what it says.
        """
        control_mode = int(direction)
        if not self.can_control_ev(serial, control_mode):
            _LOGGER.debug(
                "EV command for %s (%s) direction=%s does not match the last known "
                "state=%s; sending anyway",
                serial, ev_serial, direction, self.get_ev_charger_status_raw(serial),
            )

        result = await self._call_cloud_api(
            self.api.remoteControlEvCharger,
            sysSn=serial,
            evchargerSn=ev_serial,
            controlMode=control_mode,
        )
        _LOGGER.info(
            "Control EV Charger: %s for serial: %s Direction: %s - Result: %s",
            ev_serial, serial, direction, result,
        )

    async def reset_config(self, serial: str) -> None:
        """Reset both legacy backup stores (backup mode only).

        The periodic store cannot be cleared — AlphaESS rejects empty period
        lists — so on periodic-governed systems this action raises and the
        Reset button reports unavailable instead.
        """
        await self._safe_async_apply_schedule(
            serial,
            clear_all=True,
            charge={
                "gridCharge": 1,
                "timeChaf1": "00:00", "timeChae1": "00:00",
                "timeChaf2": "00:00", "timeChae2": "00:00",
            },
            discharge={
                "ctrDis": 1,
                "timeDisf1": "00:00", "timeDise1": "00:00",
                "timeDisf2": "00:00", "timeDise2": "00:00",
            },
        )
        _LOGGER.info("Reset charge and discharge configuration for %s", serial)
        # Optimistically update so switches reflect the change immediately
        if serial in self.data:
            self.data[serial]["gridCharge"] = 1
            self.data[serial]["ctrDis"] = 1
            self.async_set_updated_data(self.data)

    async def update_discharge(self, _name: str, serial: str, time_period: int) -> None:
        """Update discharge configuration for specified time period.

        Only slot 1 is written. The buttons used to zero slot 2 as well, which
        on the periodic store deleted an app-configured second period — the
        same class of app-state stomp as issue #269.
        """
        start_time, end_time = self.time_helper.calculate_time_window(time_period)

        await self._safe_async_apply_schedule(
            serial,
            discharge={
                "ctrDis": 1,
                "timeDisf1": start_time, "timeDise1": end_time,
            },
            now_window=True,
        )

        _LOGGER.info(
            "Updated discharge config - Period: %s to %s",
            start_time, end_time,
        )
        # Optimistically update so the discharge switch reflects enabled immediately
        if serial in self.data:
            self.data[serial]["ctrDis"] = 1
            self.async_set_updated_data(self.data)

    async def update_charge(self, _name: str, serial: str, time_period: int) -> None:
        """Update charge configuration for specified time period.

        Only slot 1 is written; see update_discharge.
        """
        start_time, end_time = self.time_helper.calculate_time_window(time_period)

        await self._safe_async_apply_schedule(
            serial,
            charge={
                "gridCharge": 1,
                "timeChaf1": start_time, "timeChae1": end_time,
            },
            now_window=True,
        )

        _LOGGER.info(
            "Updated charge config - Period: %s to %s",
            start_time, end_time,
        )
        # Optimistically update so the charge switch reflects enabled immediately
        if serial in self.data:
            self.data[serial]["gridCharge"] = 1
            self.async_set_updated_data(self.data)

    # ------------------------------------------------------------------
    # Charge / discharge schedule writes
    #
    # The periodic schedule API (getTimeChargeBySn / setTimeChargeBySn) is
    # the PRIMARY and preferred schedule store: it is what the AlphaESS app
    # manages on the new backend. The legacy two-slot endpoints remain only
    # as a BACKUP for systems whose developer account AlphaESS answers 6017
    # for on the periodic read — until the permission is granted, they are
    # the only control surface those systems have.
    #
    # A system is in exactly one mode. Periodic mode never reads or writes
    # the legacy stores; backup mode never builds state from the periodic
    # projection. Mixing the two caused issue #269 (hidden legacy periods,
    # phantom overlaps, accepted-but-inert writes). Note the backup's limits:
    # live probing (2026-08, SN AL7011023030623) showed the new backend
    # round-trips legacy writes but may not act on the discharge store.
    # ------------------------------------------------------------------

    def is_legacy_backup_active(self, serial: str) -> bool:
        """Return whether this system runs on the legacy backup endpoints.

        Only a definitive 6017 on the periodic READ selects backup mode. A
        write-denied system (read works, write 6017) stays fail-closed: the
        app-facing periodic store governs its inverter, so backup writes
        could only diverge from it.
        """
        return (
            self._periodic_readable.get(serial) is False
            and serial not in self._periodic_write_denied
        )

    @staticmethod
    def _legacy_state_from_data(
        data: dict[str, Any],
    ) -> dict[str, dict[str, Any]] | None:
        """Return a complete legacy snapshot, or None when a poll was incomplete."""
        state = {
            "charge": {
                "batHighCap": data.get(AlphaESSNames.batHighCap),
                "gridCharge": data.get("gridCharge"),
                "timeChaf1": data.get("charge_timeChaf1"),
                "timeChae1": data.get("charge_timeChae1"),
                "timeChaf2": data.get("charge_timeChaf2"),
                "timeChae2": data.get("charge_timeChae2"),
            },
            "discharge": {
                "batUseCap": data.get(AlphaESSNames.batUseCap),
                "ctrDis": data.get("ctrDis"),
                "timeDisf1": data.get("discharge_timeDisf1"),
                "timeDise1": data.get("discharge_timeDise1"),
                "timeDisf2": data.get("discharge_timeDisf2"),
                "timeDise2": data.get("discharge_timeDise2"),
            },
        }
        if any(value is None for side in state.values() for value in side.values()):
            return None
        return state

    @staticmethod
    def _normalise_legacy_side(side: str, payload: Any) -> dict[str, Any]:
        """Validate one full legacy GET response without inventing defaults."""
        fields = {
            "charge": (
                "batHighCap", "gridCharge", "timeChaf1", "timeChae1",
                "timeChaf2", "timeChae2",
            ),
            "discharge": (
                "batUseCap", "ctrDis", "timeDisf1", "timeDise1",
                "timeDisf2", "timeDise2",
            ),
        }[side]
        if not isinstance(payload, dict):
            raise ScheduleWriteError(
                f"Cannot update the {side} schedule: its full current configuration "
                "could not be read"
            )
        missing = [key for key in fields if payload.get(key) is None]
        if missing:
            raise ScheduleWriteError(
                f"Cannot update the {side} schedule without resetting unknown fields; "
                f"the API omitted {', '.join(missing)}"
            )
        return {key: payload[key] for key in fields}

    def _cache_legacy_state(
        self,
        serial: str,
        data: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> None:
        """Retain complete legacy snapshots from successful backup-mode polls."""
        if (
            expected_revision is not None
            and self._schedule_write_revisions.get(serial, 0) != expected_revision
        ):
            _LOGGER.debug(
                "Ignoring a legacy schedule poll for %s that started before a write",
                serial,
            )
            return
        state = self._legacy_state_from_data(data)
        if state is not None:
            self._legacy_schedules[serial] = deepcopy(state)
            # Fresh legacy data has replaced any lingering periodic
            # projection from before a 6017 transition.
            self._periodic_overlaid_serials.discard(serial)

    def _mark_schedule_write(self, serial: str) -> None:
        """Invalidate legacy snapshots older than a write attempt."""
        self._schedule_write_revisions[serial] = (
            self._schedule_write_revisions.get(serial, 0) + 1
        )

    async def _async_write_legacy_charge(self, serial: str, charge: dict[str, Any]) -> None:
        """Write the two-slot charge config via updateChargeConfigInfo."""
        result = await self._call_cloud_api(
            self.api.updateChargeConfigInfo,
            sysSn=serial,
            batHighCap=charge["batHighCap"],
            gridCharge=charge["gridCharge"],
            timeChae1=charge["timeChae1"],
            timeChae2=charge["timeChae2"],
            timeChaf1=charge["timeChaf1"],
            timeChaf2=charge["timeChaf2"],
        )
        _LOGGER.info("Updated charge config for %s: %s - Result: %s", serial, charge, result)

    async def _async_write_legacy_discharge(self, serial: str, discharge: dict[str, Any]) -> None:
        """Write the two-slot discharge config via updateDisChargeConfigInfo."""
        result = await self._call_cloud_api(
            self.api.updateDisChargeConfigInfo,
            sysSn=serial,
            batUseCap=discharge["batUseCap"],
            ctrDis=discharge["ctrDis"],
            timeDise1=discharge["timeDise1"],
            timeDise2=discharge["timeDise2"],
            timeDisf1=discharge["timeDisf1"],
            timeDisf2=discharge["timeDisf2"],
        )
        _LOGGER.info("Updated discharge config for %s: %s - Result: %s", serial, discharge, result)

    @staticmethod
    def _normalise_periodic_schedule(payload: Any) -> dict[str, Any]:
        """Keep the complete writable periodic payload and strip read-only fields."""
        if not isinstance(payload, dict):
            raise ScheduleWriteError("The periodic schedule response was not an object")
        if payload.get("executeCycleType") not in (0, 1):
            raise ScheduleWriteError("The periodic schedule omitted a valid executeCycleType")

        # gridChargeCycle/ctrDisCycle are deliberately dropped: the endpoint
        # can answer 0 for both regardless of the inverter's actual mode.
        # Echoing them would overwrite write-only intent with an untrustworthy
        # read. See _resolve_periodic_enable.
        result: dict[str, Any] = {
            "executeCycleType": int(payload["executeCycleType"]),
        }

        writable_fields = (
            "beginTime", "endTime", "weeks", "chargePower", "chargeLimit",
        )
        for list_key in ("chargeTimeList", "dischargeTimeList"):
            periods = payload.get(list_key)
            if not isinstance(periods, list):
                raise ScheduleWriteError(f"The periodic schedule omitted {list_key}")
            normalised: list[dict[str, Any]] = []
            for period in periods:
                if not isinstance(period, dict):
                    raise ScheduleWriteError(f"{list_key} contained a non-object period")
                cleaned = {
                    key: deepcopy(period[key])
                    for key in writable_fields
                    if period.get(key) is not None
                }
                if cleaned.get("beginTime") is None or cleaned.get("endTime") is None:
                    raise ScheduleWriteError(f"{list_key} contained a period without both times")
                normalised.append(cleaned)
            result[list_key] = normalised
        return result

    def _state_from_periodic(
        self, serial: str, schedule: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Map the first two periodic periods onto the existing entity surface."""
        charge_periods = schedule["chargeTimeList"]
        discharge_periods = schedule["dischargeTimeList"]

        state = {
            "charge": {
                # The only fallbacks for an empty period list are the user's
                # own number settings and the long-standing defaults.
                "batHighCap": (
                    charge_periods[0].get("chargeLimit") if charge_periods
                    else self.get_number_setting(serial, "batHighCap", 90)
                ),
                "gridCharge": (self._periodic_enable_intent.get(serial) or {}).get(
                    "gridChargeCycle"
                ),
                "timeChaf1": "00:00", "timeChae1": "00:00",
                "timeChaf2": "00:00", "timeChae2": "00:00",
                "chargePower1": None, "chargePower2": None,
            },
            "discharge": {
                # The legacy discharge store is retired; the only fallbacks
                # for an empty periodic list are the user's own number
                # settings and the long-standing defaults.
                "batUseCap": (
                    discharge_periods[0].get("chargeLimit") if discharge_periods
                    else self.get_number_setting(serial, "batUseCap", 10)
                ),
                "ctrDis": (self._periodic_enable_intent.get(serial) or {}).get(
                    "ctrDisCycle"
                ),
                "timeDisf1": "00:00", "timeDise1": "00:00",
                "timeDisf2": "00:00", "timeDise2": "00:00",
                "chargePower1": None, "chargePower2": None,
            },
        }
        for index, period in enumerate(charge_periods[:2], start=1):
            state["charge"][f"timeChaf{index}"] = period["beginTime"]
            state["charge"][f"timeChae{index}"] = period["endTime"]
            state["charge"][f"chargePower{index}"] = period.get("chargePower")
        for index, period in enumerate(discharge_periods[:2], start=1):
            state["discharge"][f"timeDisf{index}"] = period["beginTime"]
            state["discharge"][f"timeDise{index}"] = period["endTime"]
            state["discharge"][f"chargePower{index}"] = period.get("chargePower")
        return state

    def _committed_schedule_entity_state(
        self, serial: str
    ) -> dict[str, dict[str, Any]]:
        """Return the latest committed periodic or legacy schedule view."""
        if self._has_periodic_schedule_snapshot(serial):
            self._periodic_overlaid_serials.add(serial)
            return self._state_from_periodic(serial, self._periodic_schedules[serial])
        if self.is_legacy_backup_active(serial):
            cached = self._legacy_schedules.get(serial, {})
            if serial in self._periodic_overlaid_serials:
                # The entity data may still hold a periodic projection after
                # a 6017 transition; never reinterpret that as legacy values.
                state = None
            else:
                state = self._legacy_state_from_data(self.data.get(serial) or {})
            if state is not None:
                # A one-sided write can leave the cache newer than the polled
                # view for just that side; the cache wins where it exists.
                for side in ("charge", "discharge"):
                    if side in cached:
                        state[side] = deepcopy(cached[side])
            elif all(side in cached for side in ("charge", "discharge")):
                state = deepcopy(cached)
            if state is not None:
                self._legacy_schedules[serial] = deepcopy(state)
                self._periodic_overlaid_serials.discard(serial)
                return state
        raise ScheduleWriteError(
            f"Cannot edit the schedule for {serial}: no schedule store is "
            "usable yet for this system"
        )

    def _schedule_entity_state(self, serial: str) -> dict[str, dict[str, Any]]:
        """Return an open draft or the latest committed schedule view."""
        if serial in self._schedule_drafts:
            return deepcopy(self._schedule_drafts[serial])
        return self._committed_schedule_entity_state(serial)

    @staticmethod
    def _put_schedule_state(data: dict[str, Any], state: dict[str, dict[str, Any]]) -> None:
        """Overlay the authoritative/draft schedule onto coordinator entity data."""
        charge = state["charge"]
        data[AlphaESSNames.batHighCap] = charge["batHighCap"]
        data["gridCharge"] = charge["gridCharge"]
        data["charge_timeChaf1"] = charge["timeChaf1"]
        data["charge_timeChae1"] = charge["timeChae1"]
        data["charge_timeChaf2"] = charge["timeChaf2"]
        data["charge_timeChae2"] = charge["timeChae2"]
        data[AlphaESSNames.ChargeTime1] = _format_window(
            charge["timeChaf1"], charge["timeChae1"]
        )
        data[AlphaESSNames.ChargeTime2] = _format_window(
            charge["timeChaf2"], charge["timeChae2"]
        )
        data[AlphaESSNames.ChargePower1] = charge.get("chargePower1")
        data[AlphaESSNames.ChargePower2] = charge.get("chargePower2")
        discharge = state["discharge"]
        data[AlphaESSNames.batUseCap] = discharge["batUseCap"]
        data["ctrDis"] = discharge["ctrDis"]
        data["discharge_timeDisf1"] = discharge["timeDisf1"]
        data["discharge_timeDise1"] = discharge["timeDise1"]
        data["discharge_timeDisf2"] = discharge["timeDisf2"]
        data["discharge_timeDise2"] = discharge["timeDise2"]
        data[AlphaESSNames.DischargeTime1] = _format_window(
            discharge["timeDisf1"], discharge["timeDise1"]
        )
        data[AlphaESSNames.DischargeTime2] = _format_window(
            discharge["timeDisf2"], discharge["timeDise2"]
        )
        data[AlphaESSNames.DischargePower1] = discharge.get("chargePower1")
        data[AlphaESSNames.DischargePower2] = discharge.get("chargePower2")
        data[AlphaESSNames.ChargeRange] = (
            f"{discharge['batUseCap']}% - {charge['batHighCap']}%"
        )
        # Whether either recorded schedule flag is enabled. This deliberately
        # says nothing about the inverter's working mode: OpenAPI exposes no
        # authoritative working-mode endpoint.
        grid_flag, dis_flag = charge["gridCharge"], discharge["ctrDis"]
        data[AlphaESSNames.ScheduleFlagsEnabled] = (
            None
            if grid_flag is None and dis_flag is None
            else int(grid_flag or 0) == 1 or int(dis_flag or 0) == 1
        )

    def _overlay_schedule_view(self, serial: str, data: dict[str, Any]) -> None:
        """Keep drafts/periodic state from being reset by a stale legacy poll."""
        try:
            state = self._schedule_entity_state(serial)
        except ScheduleWriteError:
            return
        self._put_schedule_state(data, state)
        # This diagnostic reflects recorded remote intent, not an open draft
        # and not the inverter's working mode.
        committed = self._recorded_enable_flags(serial)
        data[AlphaESSNames.ScheduleFlagsEnabled] = (
            None
            if committed is None
            else committed[0] == 1 or committed[1] == 1
        )

    def _note_schedule_surface(self, serial: str) -> None:
        """Log schedule-surface capability changes once.

        A user reports "the controls went away"; this is the line that says
        when, and on the strength of what.
        """
        state = (
            self.get_periodic_read_state(serial),
            self.can_modify_time_controls(serial),
        )
        if self._schedule_state_logged.get(serial) == state:
            return
        first_time = serial not in self._schedule_state_logged
        self._schedule_state_logged[serial] = state
        read_state, modifiable = state
        message = (
            "Schedule surface for %s is %s: periodic read %s, recorded enable "
            "flags %s (working mode unavailable through OpenAPI)"
        )
        args = (
            serial,
            "editable" if modifiable else "locked",
            read_state,
            self._periodic_enable_intent.get(serial) or "none recorded",
        )
        if first_time:
            _LOGGER.debug(message, *args)
        else:
            _LOGGER.info(message, *args)

    def _publish_schedule_view(self, serial: str) -> None:
        """Publish a schedule view change to all coordinator entities."""
        if serial not in self.data:
            return
        self._overlay_schedule_view(serial, self.data[serial])
        self._note_schedule_surface(serial)
        self.async_set_updated_data({key: dict(value) for key, value in self.data.items()})

    def stage_schedule_change(
        self,
        serial: str,
        *,
        charge: dict[str, Any] | None = None,
        discharge: dict[str, Any] | None = None,
    ) -> None:
        """Stage entity edits; no remote replacement occurs until Apply."""
        if not charge and not discharge:
            return
        readable = self._periodic_readable.get(serial)
        if readable is None:
            raise ScheduleWriteError(
                "Cannot stage schedule changes until AlphaESS confirms whether the "
                "periodic schedule can be read"
            )
        if serial in self._periodic_write_denied:
            raise ScheduleWriteError(
                "Schedule control requires the AlphaESS periodic schedule API "
                "(setTimeChargeBySn), whose writes AlphaESS refuses for this "
                "system; ask AlphaESS to enable the timed charge/discharge "
                "permission for your account"
            )
        if readable is True and serial not in self._periodic_schedules:
            raise ScheduleWriteError(
                "Cannot stage schedule changes until the complete periodic schedule "
                "has been read"
            )
        power_fields = {"chargePower1", "chargePower2"}
        if readable is False and any(
            changes and power_fields.intersection(changes)
            for changes in (charge, discharge)
        ):
            raise ScheduleWriteError(
                "Per-period power exists only in the periodic schedule API, "
                "which AlphaESS has not enabled for this system; the legacy "
                "backup endpoints have no power field"
            )
        if serial not in self._schedule_drafts:
            self._schedule_drafts[serial] = self._schedule_entity_state(serial)
            self._schedule_draft_dirty[serial] = {"charge": set(), "discharge": set()}
            if readable is True:
                self._schedule_draft_base_periodic[serial] = deepcopy(
                    self._periodic_schedules[serial]
                )
            else:
                self._schedule_draft_base_legacy[serial] = deepcopy(
                    self._legacy_schedules[serial]
                )

        for side, changes in (("charge", charge), ("discharge", discharge)):
            if changes:
                _LOGGER.debug(
                    "Staged %s for %s: %s (draft now %s)",
                    side, serial, changes, self._schedule_drafts[serial][side],
                )
                self._schedule_drafts[serial][side].update(changes)
                self._schedule_draft_dirty[serial][side].update(changes)
        self._schedule_draft_revisions[serial] = (
            self._schedule_draft_revisions.get(serial, 0) + 1
        )
        self._publish_schedule_view(serial)

    def has_schedule_draft(self, serial: str) -> bool:
        """Return whether this inverter has unapplied schedule changes."""
        return serial in self._schedule_drafts

    def _rebase_open_schedule_draft(self, serial: str) -> None:
        """Rebase an open draft onto the latest committed schedule.

        Quick buttons and immediate services can write while entity edits are
        still staged. Preserve fields the user actually changed, refresh every
        other field from the transaction that just succeeded, and make the
        next Apply compare against that latest committed store.
        """
        draft = self._schedule_drafts.get(serial)
        if draft is None:
            return
        dirty = self._schedule_draft_dirty.get(serial) or {}
        committed = self._committed_schedule_entity_state(serial)
        for side in ("charge", "discharge"):
            dirty_fields = dirty.get(side, set())
            for field, value in committed[side].items():
                if field not in dirty_fields:
                    draft[side][field] = deepcopy(value)
        if serial in self._periodic_schedules:
            self._schedule_draft_base_periodic[serial] = deepcopy(
                self._periodic_schedules[serial]
            )
        else:
            self._schedule_draft_base_periodic.pop(serial, None)
        if serial in self._legacy_schedules:
            self._schedule_draft_base_legacy[serial] = deepcopy(
                self._legacy_schedules[serial]
            )
        else:
            self._schedule_draft_base_legacy.pop(serial, None)
        if serial in self.data:
            self._overlay_schedule_view(serial, self.data[serial])

    def is_schedule_field_staged(self, serial: str, coordinator_key: str) -> bool:
        """Return whether this schedule field was explicitly staged in the draft.

        The entity keys are "<side>_<api field>", e.g. charge_timeChaf1.
        """
        dirty = self._schedule_draft_dirty.get(serial)
        if not dirty:
            return False
        side, _, field = coordinator_key.partition("_")
        return field in dirty.get(side, ())

    def is_schedule_apply_in_progress(self, serial: str) -> bool:
        """Return whether this inverter has a remote Apply transaction in flight."""
        return serial in self._schedule_apply_in_progress

    def _has_periodic_schedule_snapshot(self, serial: str) -> bool:
        """Return whether a complete periodic schedule has been read."""
        return (
            self._periodic_readable.get(serial) is True
            and serial in self._periodic_schedules
        )

    def is_periodic_schedule_readable(self, serial: str) -> bool:
        """Return whether per-period fields can be read and written safely."""
        return (
            self._has_periodic_schedule_snapshot(serial)
            and serial not in self._periodic_write_denied
        )

    def can_stage_schedule(self, serial: str) -> bool:
        """Return whether schedule edits have a live, known base.

        Primary mode needs the periodic schedule readable, writable and
        cached. Backup mode (definitive 6017) needs a complete legacy
        snapshot instead.
        """
        if serial not in self.data:
            return False
        if self.is_periodic_schedule_readable(serial):
            return True
        if not self.is_legacy_backup_active(serial):
            return False
        cached = self._legacy_schedules.get(serial, {})
        if all(side in cached for side in ("charge", "discharge")):
            return True
        if serial in self._periodic_overlaid_serials:
            return False
        return self._legacy_state_from_data(self.data.get(serial) or {}) is not None

    def set_periodic_enable_intent(
        self, serial: str, *, grid_charge: int | None = None, ctr_dis: int | None = None
    ) -> None:
        """Record what the enable switches were last known to be set to.

        Restored by the switch entities at startup: the API cannot report these,
        so a value Home Assistant has never been told is a value it must not
        invent.
        """
        intent = dict(self._periodic_enable_intent.get(serial) or {})
        if grid_charge is not None:
            intent["gridChargeCycle"] = int(grid_charge)
        if ctr_dis is not None:
            intent["ctrDisCycle"] = int(ctr_dis)
        if intent:
            # One switch may restore before the other; a half-filled record is
            # still refused by _resolve_periodic_enable until it is complete.
            self._periodic_enable_intent[serial] = intent

    def _resolve_periodic_enable(
        self,
        serial: str,
        charge: dict[str, Any] | None,
        discharge: dict[str, Any] | None,
    ) -> dict[str, int] | None:
        """Return the enable pair to send, or None when it is not known.

        This write wins over the stored intent; with neither, there is nothing
        truthful to send and the caller must refuse rather than guess.
        """
        stored = self._periodic_enable_intent.get(serial) or {}
        # Only fields owned by this transaction may override the committed
        # pair. In particular, quick buttons and immediate services must not
        # consume unrelated values waiting in an open Apply/Discard draft.
        grid = (charge or {}).get("gridCharge", stored.get("gridChargeCycle"))
        dis = (discharge or {}).get("ctrDis", stored.get("ctrDisCycle"))
        if (grid is None or dis is None) and self.assume_schedule_flags_enabled:
            # The option stands in only for a flag nobody has answered for.
            # Once this write lands the pair is recorded, so the assumption
            # is used at most once per system per session.
            assumed = [
                name for name, value in (("gridChargeCycle", grid), ("ctrDisCycle", dis))
                if value is None
            ]
            if serial not in self._assumed_flags_logged:
                self._assumed_flags_logged.add(serial)
                _LOGGER.info(
                    "Assuming %s enabled for %s: Home Assistant has no record "
                    "of the pair and the assume-enabled option is on; the pair "
                    "is recorded once AlphaESS accepts the write",
                    " and ".join(assumed), serial,
                )
            grid = 1 if grid is None else grid
            dis = 1 if dis is None else dis
        if grid is None or dis is None:
            return None
        return {"gridChargeCycle": int(grid), "ctrDisCycle": int(dis)}

    def _recorded_enable_flags(self, serial: str) -> tuple[int, int] | None:
        """Return the recorded flags, without inferring inverter mode."""
        if self._has_periodic_schedule_snapshot(serial):
            intent = self._periodic_enable_intent.get(serial) or {}
            grid, dis = intent.get("gridChargeCycle"), intent.get("ctrDisCycle")
            if grid is None or dis is None:
                return None
            return (int(grid), int(dis))
        if self.is_legacy_backup_active(serial):
            cached = self._legacy_schedules.get(serial, {})
            if all(side in cached for side in ("charge", "discharge")):
                return (
                    int(cached["charge"]["gridCharge"]),
                    int(cached["discharge"]["ctrDis"]),
                )
        return None

    def recorded_schedule_flags_enabled(self, serial: str) -> bool | None:
        """Return whether either recorded flag is on, not working mode."""
        flags = self._recorded_enable_flags(serial)
        return None if flags is None else flags[0] == 1 or flags[1] == 1

    def recorded_schedule_flag(self, serial: str, key: str) -> int | None:
        """Return one recorded enable flag for persisted entity attributes."""
        if self._has_periodic_schedule_snapshot(serial):
            intent_key = {
                "gridCharge": "gridChargeCycle",
                "ctrDis": "ctrDisCycle",
            }.get(key)
            value = (self._periodic_enable_intent.get(serial) or {}).get(intent_key)
            return None if value is None else int(value)
        if self.is_legacy_backup_active(serial):
            side = {"gridCharge": "charge", "ctrDis": "discharge"}.get(key)
            value = (self._legacy_schedules.get(serial, {}).get(side or "") or {}).get(
                key
            )
            return None if value is None else int(value)
        return None

    def can_modify_time_controls(self, serial: str) -> bool:
        """Return whether the timed-schedule editing surfaces should be live."""
        return self.can_stage_schedule(serial)

    def can_reset_schedule(self, serial: str) -> bool:
        """Return whether Reset Charge/Discharge can possibly succeed.

        Only the legacy backup stores can be cleared; the periodic store
        rejects empty period lists. Reporting this lets the button become
        unavailable instead of failing on every press.
        """
        return self.is_legacy_backup_active(serial)

    def discard_schedule_draft(self, serial: str) -> None:
        """Discard local entity edits and restore the latest remote view."""
        if serial in self._schedule_apply_in_progress:
            raise ScheduleWriteError(
                "Cannot discard schedule changes while Apply is still in progress"
            )
        self._schedule_draft_revisions[serial] = (
            self._schedule_draft_revisions.get(serial, 0) + 1
        )
        self._schedule_drafts.pop(serial, None)
        self._schedule_draft_dirty.pop(serial, None)
        self._schedule_draft_base_periodic.pop(serial, None)
        self._schedule_draft_base_legacy.pop(serial, None)
        self._publish_schedule_view(serial)

    async def async_apply_schedule_draft(self, serial: str) -> None:
        """Apply all dirty entity fields as one read/modify/write transaction."""
        draft = self._schedule_drafts.get(serial)
        dirty = self._schedule_draft_dirty.get(serial)
        if draft is None or dirty is None:
            raise ScheduleWriteError(f"There are no pending schedule changes for {serial}")
        revision = self._schedule_draft_revisions.get(serial, 0)
        if serial in self._schedule_apply_in_progress:
            raise ScheduleWriteError(f"A schedule Apply is already in progress for {serial}")

        charge = {key: draft["charge"][key] for key in dirty["charge"]} or None
        discharge = {
            key: draft["discharge"][key] for key in dirty["discharge"]
        } or None
        self._schedule_apply_in_progress.add(serial)
        self._publish_schedule_view(serial)
        try:
            await self._safe_async_apply_schedule(
                serial,
                charge=charge,
                discharge=discharge,
                expected_periodic=self._schedule_draft_base_periodic.get(serial),
                expected_legacy=self._schedule_draft_base_legacy.get(serial),
            )
        except SchedulePartialWriteError:
            # Backup mode only: one legacy store accepted the change. Keep
            # the draft for retry, but advance its conflict base to what the
            # API already accepted.
            if serial in self._legacy_schedules:
                self._schedule_draft_base_legacy[serial] = deepcopy(
                    self._legacy_schedules[serial]
                )
            raise
        finally:
            self._schedule_apply_in_progress.discard(serial)
            self._publish_schedule_view(serial)
        if self._schedule_draft_revisions.get(serial, 0) != revision:
            # Another edit (or Discard followed by a new edit) happened while
            # the API calls were awaiting responses. Keep that newer intent;
            # the next Apply starts from the state this transaction committed.
            current_draft = self._schedule_drafts.get(serial)
            if current_draft is draft:
                # Remove fields this transaction committed unless their value
                # changed again while the request awaited AlphaESS.
                current_dirty = self._schedule_draft_dirty.get(serial) or {}
                for side, applied in (
                    ("charge", charge),
                    ("discharge", discharge),
                ):
                    for field, value in (applied or {}).items():
                        if current_draft[side].get(field) == value:
                            current_dirty.get(side, set()).discard(field)
                if not any(
                    current_dirty.get(side) for side in ("charge", "discharge")
                ):
                    self._schedule_drafts.pop(serial, None)
                    self._schedule_draft_dirty.pop(serial, None)
                    self._schedule_draft_base_periodic.pop(serial, None)
                    self._schedule_draft_base_legacy.pop(serial, None)
                    self._publish_schedule_view(serial)
                    return
            if serial in self._schedule_drafts:
                self._rebase_open_schedule_draft(serial)
            self._publish_schedule_view(serial)
            return
        self._schedule_drafts.pop(serial, None)
        self._schedule_draft_dirty.pop(serial, None)
        self._schedule_draft_base_periodic.pop(serial, None)
        self._schedule_draft_base_legacy.pop(serial, None)
        self._publish_schedule_view(serial)

    async def async_write_charge_config(
        self,
        serial: str,
        *,
        bat_high_cap: float | None = None,
        grid_charge: int | None = None,
        times: dict[str, str] | None = None,
        powers: dict[str, float] | None = None,
    ) -> None:
        """Apply a charge configuration change.

        `times` is keyed by the legacy API names (timeChaf1/timeChae1/
        timeChaf2/timeChae2); anything omitted keeps its current value.
        """
        overrides: dict[str, Any] = {**(times or {}), **(powers or {})}
        if bat_high_cap is not None:
            overrides["batHighCap"] = bat_high_cap
        if grid_charge is not None:
            overrides["gridCharge"] = grid_charge
        await self._safe_async_apply_schedule(serial, charge=overrides)

    async def async_write_discharge_config(
        self,
        serial: str,
        *,
        bat_use_cap: float | None = None,
        ctr_dis: int | None = None,
        times: dict[str, str] | None = None,
        powers: dict[str, float] | None = None,
    ) -> None:
        """Apply a discharge configuration change.

        `times` is keyed by the legacy API names (timeDisf1/timeDise1/
        timeDisf2/timeDise2); anything omitted keeps its current value.
        """
        overrides: dict[str, Any] = {**(times or {}), **(powers or {})}
        if bat_use_cap is not None:
            overrides["batUseCap"] = bat_use_cap
        if ctr_dis is not None:
            overrides["ctrDis"] = ctr_dis
        await self._safe_async_apply_schedule(serial, discharge=overrides)

    async def _async_resolve_unknown_periodic_read(self) -> None:
        """Retry the periodic read for any inverter still undecided.

        The setup read can come back inconclusive if the cloud hiccups, which
        would otherwise leave the diagnostic sensor reading "unknown" forever.
        Readable schedules are refreshed on each full poll so changes made in
        the AlphaESS app are not overwritten from a setup-time cache. Permanent
        6017 rejections are the only results not retried.
        """
        for serial in list(self.data):
            if self._periodic_readable.get(serial) is not False:
                await self.async_probe_periodic_readable(serial)

    async def async_probe_periodic_readable(self, serial: str) -> bool | None:
        """Serialise a periodic refresh with schedule write transactions."""
        lock = self._schedule_locks.setdefault(serial, asyncio.Lock())
        async with lock:
            return await self._async_probe_periodic_readable_locked(serial)

    async def _async_probe_periodic_readable_locked(
        self, serial: str
    ) -> bool | None:
        """Read and retain the complete periodic schedule for diagnostics/writes.

        A partial update is allowed only when this full-replacement resource
        can be read first. A definitive 6017 means this system has no schedule
        surface at all (the legacy endpoints are retired); transient, empty or
        malformed responses remain unknown and fail closed on writes.

        Returns True/False once known and caches it. Returns None when the
        answer could not be established (transport error), leaving the cache
        untouched so it is retried later.
        """
        try:
            payload = await self._call_cloud_api(self.api.getTimeChargeBySn, serial)
        except asyncio.CancelledError:
            raise
        except AlphaESSApiError as err:
            if err.code in AUTH_FAILURE_CODES:
                raise ConfigEntryAuthFailed(
                    f"AlphaESS credentials rejected: {err}"
                ) from err
            # The API answered and refused. 6017 means this system is not
            # entitled to the feature, which will not change on a retry, so
            # cache it. Anything else might, so leave the answer open.
            if err.code == PERIODIC_NOT_ENTITLED:
                _LOGGER.debug("Periodic schedule not readable for %s: %s",
                              serial, describe_api_error(err))
                self._periodic_readable[serial] = False
                self._periodic_schedules.pop(serial, None)
                return False
            _LOGGER.debug("Periodic schedule read for %s failed with %s",
                          serial, describe_api_error(err))
            return None
        except Exception as err:
            _LOGGER.debug("Periodic schedule probe failed for %s: %s", serial, err)
            return None

        if payload is None:
            _LOGGER.debug("Periodic schedule for %s returned no data", serial)
            return None

        try:
            schedule = self._normalise_periodic_schedule(payload)
        except ScheduleWriteError as err:
            _LOGGER.warning("Periodic schedule for %s is not safely writable: %s", serial, err)
            return None

        self._periodic_readable[serial] = True
        self._periodic_schedules[serial] = schedule
        if serial in self.data:
            self._overlay_schedule_view(serial, self.data[serial])
        _LOGGER.debug("Periodic schedule API available for %s", serial)
        return True

    def _patch_periodic_schedule(
        self,
        serial: str,
        schedule: dict[str, Any],
        *,
        charge: dict[str, Any] | None,
        discharge: dict[str, Any] | None,
        now_window: bool = False,
    ) -> dict[str, Any]:
        """Patch only dirty fields while preserving the full periodic resource.

        now_window marks a quick-action write ("charge/discharge for the next N
        minutes"). It tightens the weekly check below and swaps draft-flavoured
        error messages for ones that name a remedy that works from a button.
        """
        proposed = deepcopy(schedule)
        cycle_type = proposed["executeCycleType"]

        side_specs = {
            "charge": {
                "changes": charge,
                "list": "chargeTimeList",
                "limit": "batHighCap",
                "start": "timeChaf",
                "end": "timeChae",
            },
            "discharge": {
                "changes": discharge,
                "list": "dischargeTimeList",
                "limit": "batUseCap",
                "start": "timeDisf",
                "end": "timeDise",
            },
        }

        for side, spec in side_specs.items():
            changes = spec["changes"]
            if not changes:
                continue

            # gridCharge/ctrDis are not part of the periodic resource; they
            # are resolved separately and sent as their own parameters.

            list_key = spec["list"]
            original = proposed[list_key]
            if spec["limit"] in changes:
                # Apply the HA cutoff only to the two slots it represented in
                # the original resource. If slot 1 is removed below, an unseen
                # third app period must not shift into index 2 and be changed.
                for period in original[:2]:
                    period["chargeLimit"] = changes[spec["limit"]]
            start_prefix = spec["start"]
            end_prefix = spec["end"]
            slot_keys = {
                f"{prefix}{index}"
                for prefix in (start_prefix, end_prefix, "chargePower")
                for index in (1, 2)
            }
            if slot_keys.intersection(changes):
                managed: list[dict[str, Any]] = []
                for index in (1, 2):
                    existing = original[index - 1] if len(original) >= index else None
                    start_key = f"{start_prefix}{index}"
                    end_key = f"{end_prefix}{index}"
                    power_key = f"chargePower{index}"
                    begin = changes.get(
                        start_key,
                        existing.get("beginTime") if existing else "00:00",
                    )
                    end = changes.get(
                        end_key,
                        existing.get("endTime") if existing else "00:00",
                    )
                    changed_slot_keys = {start_key, end_key, power_key}.intersection(changes)
                    if existing is None and changed_slot_keys:
                        if start_key not in changes or end_key not in changes:
                            raise ScheduleWriteError(
                                f"Set both the start and end for new {side} period {index} "
                                "before applying the schedule"
                            )
                    elif existing is None:
                        continue
                    if begin == end:
                        continue

                    if existing is not None:
                        if (
                            now_window
                            and changed_slot_keys
                            and cycle_type != PERIODIC_DAILY
                            and dt_util.now().isoweekday()
                            not in (existing.get("weeks") or ())
                        ):
                            # Retargeting would be accepted, run on other
                            # weekdays, and do nothing now — an inert write of
                            # exactly the issue #269 kind.
                            raise ScheduleWriteError(
                                f"The periodic schedule is weekly and {side} period "
                                f"{index} ({existing['beginTime']}-"
                                f"{existing['endTime']}) does not run today; a "
                                "quick button cannot retarget it without silently "
                                "rescheduling its other weekdays. Adjust the "
                                "schedule in the AlphaESS app instead"
                            )
                        period = deepcopy(existing)
                        period["beginTime"] = begin
                        period["endTime"] = end
                        if power_key in changes:
                            period["chargePower"] = changes[power_key]
                    else:
                        weeks = None
                        if cycle_type != PERIODIC_DAILY:
                            # The two-slot entities cannot choose weekdays, but
                            # the new app writes "every day" as a weekly schedule
                            # covering all seven, so refusing outright blocks most
                            # systems on the new backend (#267). Inherit the days
                            # a sibling period already runs on instead: visible in
                            # the app, and reversible there.
                            sibling = original[0] if original else None
                            weeks = (sibling or {}).get("weeks")
                            if not weeks:
                                raise ScheduleWriteError(
                                    f"Cannot add {side} period {index} to a weekly "
                                    "schedule with no existing period to take its "
                                    "weekdays from; create it in the AlphaESS app"
                                )
                            _LOGGER.info(
                                "New %s period %s on %s inherits weekdays %s from the "
                                "existing period; change them in the AlphaESS app",
                                side, index, serial, weeks,
                            )
                        power = changes.get(power_key)
                        if power is None:
                            if now_window:
                                raise ScheduleWriteError(
                                    f"The quick {side} button cannot create {side} "
                                    f"period {index}: AlphaESS needs an explicit "
                                    "positive power (chargePower) for a new period. "
                                    "Create the period in the AlphaESS app, or stage "
                                    "its times and power with the schedule entities "
                                    "and select Apply Charge/Discharge Schedule"
                                )
                            raise ScheduleWriteError(
                                f"Set an explicit positive power for new {side} period "
                                f"{index} before applying the schedule"
                            )
                        limit = changes.get(spec["limit"])
                        if limit is None and original:
                            limit = original[0].get("chargeLimit")
                        if limit is None:
                            if now_window:
                                raise ScheduleWriteError(
                                    f"The quick {side} button cannot create {side} "
                                    f"period {index} without a cutoff SOC. Create the "
                                    "period in the AlphaESS app, or stage it with the "
                                    "schedule entities and select Apply "
                                    "Charge/Discharge Schedule"
                                )
                            raise ScheduleWriteError(
                                f"Set an explicit cutoff SOC for new {side} period {index} "
                                "before applying the schedule"
                            )
                        period = {
                            "beginTime": begin,
                            "endTime": end,
                            "chargeLimit": limit,
                            "chargePower": power,
                        }
                        if weeks is not None:
                            period["weeks"] = deepcopy(weeks)
                    managed.append(period)
                proposed[list_key] = managed + deepcopy(original[2:])

        for list_key in ("chargeTimeList", "dischargeTimeList"):
            periods = proposed[list_key]
            if not periods:
                raise ScheduleWriteError(
                    "setTimeChargeBySn requires at least one charge period and one "
                    f"discharge period; {list_key} would be empty (it answers 6001 "
                    "for an empty list and 10001 for a missing one). To run one side "
                    "only, give the other side a period and turn its switch off. Add "
                    "a period to each side in the AlphaESS app, or stage both sides "
                    "with the schedule entities and apply them together"
                )
            for position, period in enumerate(periods, start=1):
                # Name the offending period: it may be one the app created and
                # this write merely round-trips, so "a period" alone reads as
                # the integration blaming the change the user just made.
                where = (
                    f"{list_key} period {position} "
                    f"({period.get('beginTime')}-{period.get('endTime')})"
                )
                try:
                    limit = float(period["chargeLimit"])
                except (KeyError, TypeError, ValueError) as err:
                    raise ScheduleWriteError(
                        f"{where} has no valid chargeLimit; repair it in the "
                        "AlphaESS app or via the schedule entities"
                    ) from err
                if not PERIODIC_MIN_CHARGE_LIMIT <= limit <= PERIODIC_MAX_CHARGE_LIMIT:
                    raise ScheduleWriteError(
                        f"{where} has chargeLimit {limit}; the periodic "
                        "API accepts 10-100 and the value was not changed silently"
                    )
                try:
                    raw_power = period["chargePower"]
                    if isinstance(raw_power, bool):
                        raise TypeError
                    power = float(raw_power)
                except (KeyError, TypeError, ValueError) as err:
                    raise ScheduleWriteError(
                        f"{where} has no valid chargePower; sending it "
                        "would create an accepted but inert schedule. Set its "
                        "power in the AlphaESS app (or the Power entities for "
                        "periods 1-2) and retry"
                    ) from err
                if not math.isfinite(power) or power <= 0:
                    raise ScheduleWriteError(
                        f"{where} has non-positive chargePower {power}; sending it "
                        "would create an accepted but inert schedule. Set its "
                        "power in the AlphaESS app (or the Power entities for "
                        "periods 1-2) and retry"
                    )
                if not power.is_integer():
                    raise ScheduleWriteError(
                        f"{where} has non-integral chargePower {power}; "
                        "the periodic API accepts whole watts"
                    )
                # Home Assistant NumberEntity values arrive as floats. The
                # upstream contract requires a JSON integer for chargePower.
                period["chargePower"] = int(power)
                if cycle_type == 1 and not period.get("weeks"):
                    raise ScheduleWriteError(
                        f"Weekly {where} has no weekdays"
                    )
        return proposed

    async def _safe_async_apply_schedule(
        self,
        serial: str,
        *,
        charge: dict[str, Any] | None = None,
        discharge: dict[str, Any] | None = None,
        expected_periodic: dict[str, Any] | None = None,
        expected_legacy: dict[str, dict[str, Any]] | None = None,
        clear_all: bool = False,
        now_window: bool = False,
    ) -> None:
        """Safely patch the schedule store this system runs on."""
        if charge is None and discharge is None:
            return
        lock = self._schedule_locks.setdefault(serial, asyncio.Lock())
        async with lock:
            await self._safe_async_apply_schedule_locked(
                serial,
                charge=charge,
                discharge=discharge,
                expected_periodic=expected_periodic,
                expected_legacy=expected_legacy,
                clear_all=clear_all,
                now_window=now_window,
            )
            # Draft Apply owns its own revision-aware cleanup. Any other
            # successful write is an out-of-band commit relative to an open
            # draft, so advance that draft's conflict base now.
            if serial not in self._schedule_apply_in_progress:
                self._rebase_open_schedule_draft(serial)

    async def _safe_async_apply_schedule_locked(
        self,
        serial: str,
        *,
        charge: dict[str, Any] | None,
        discharge: dict[str, Any] | None,
        expected_periodic: dict[str, Any] | None,
        expected_legacy: dict[str, dict[str, Any]] | None = None,
        clear_all: bool = False,
        now_window: bool = False,
    ) -> None:
        """Read, conflict-check and write the schedule while holding the lock."""
        periodic_current: dict[str, Any] | None = None
        periodic_error: Exception | None = None

        if (
            serial not in self._periodic_write_denied
            and self._periodic_readable.get(serial) is not False
        ):
            try:
                payload = await self._call_cloud_api(
                    self.api.getTimeChargeBySn, serial
                )
                if payload is None:
                    raise ScheduleWriteError(
                        "The periodic schedule returned no data, so nothing was changed"
                    )
                periodic_current = self._normalise_periodic_schedule(payload)
                self._periodic_readable[serial] = True
                self._periodic_schedules[serial] = deepcopy(periodic_current)
            except asyncio.CancelledError:
                raise
            except AlphaESSApiError as err:
                if err.code == PERIODIC_NOT_ENTITLED:
                    self._periodic_readable[serial] = False
                    self._periodic_schedules.pop(serial, None)
                else:
                    periodic_error = err
            except Exception as err:
                periodic_error = err

        if periodic_current is None:
            if self.is_legacy_backup_active(serial):
                if expected_periodic is not None:
                    raise ScheduleWriteError(
                        "The periodic schedule used to open this draft is no "
                        "longer available for this system; discard the draft "
                        "and try again. Nothing was changed"
                    )
                await self._apply_legacy_backup_locked(
                    serial,
                    charge=charge,
                    discharge=discharge,
                    expected_legacy=expected_legacy,
                    clear_all=clear_all,
                )
                return
            if serial in self._periodic_write_denied:
                raise ScheduleWriteError(
                    "Schedule control requires the AlphaESS periodic schedule "
                    "API (setTimeChargeBySn), whose writes AlphaESS refuses "
                    "for this system (6017 No operation permissions); ask "
                    "AlphaESS to enable the timed charge/discharge "
                    "permission. Nothing was changed"
                )
            if expected_periodic is not None:
                raise ScheduleWriteError(
                    "The periodic schedule used to open this draft can no longer "
                    "be read; nothing was changed"
                ) from periodic_error
            detail = f": {periodic_error}" if periodic_error else ""
            raise ScheduleWriteError(
                f"The current periodic schedule could not be read safely{detail}; "
                "nothing was changed"
            ) from periodic_error

        if expected_legacy is not None:
            # The draft was opened against the backup stores, but the system
            # now answers on the periodic API. The base it was edited from
            # describes a different resource.
            raise ScheduleConflictError(
                "The periodic schedule API became available after this draft "
                "was opened on the legacy backup; discard the draft, review "
                "the fresh values, and try again"
            )
        if clear_all:
            raise ScheduleWriteError(
                "AlphaESS does not provide a verified way to clear the "
                "periodic schedule because both period lists must stay "
                "non-empty; nothing was changed"
            )

        if expected_periodic is not None and periodic_current != expected_periodic:
            raise ScheduleConflictError(
                "The AlphaESS periodic schedule changed after this draft was opened. "
                "Discard the draft, review the fresh values, and try again"
            )

        periodic_proposed = self._patch_periodic_schedule(
            serial, periodic_current, charge=charge, discharge=discharge,
            now_window=now_window,
        )

        enable = self._resolve_periodic_enable(serial, charge, discharge)
        if enable is None:
            raise ScheduleWriteError(
                "Home Assistant does not know whether scheduled charging and "
                "discharging should be enabled: getTimeChargeBySn reports 0 for "
                "both whatever the inverter is doing. Set the Scheduled Charging and "
                "Scheduled Discharging switches once so there is something "
                "truthful to send, or turn on 'Assume scheduled charging and "
                "discharging are enabled when unknown' in the integration "
                "options; nothing was written"
            )

        if (
            periodic_proposed == periodic_current
            and enable == self._periodic_enable_sent.get(serial)
        ):
            _LOGGER.debug(
                "Periodic schedule for %s already matches the requested values",
                serial,
            )
            self._publish_schedule_view(serial)
            return

        try:
            await self._call_cloud_api(
                self.api.setTimeChargeBySn,
                sysSn=serial,
                executeCycleType=periodic_proposed["executeCycleType"],
                chargeTimeList=periodic_proposed["chargeTimeList"],
                dischargeTimeList=periodic_proposed["dischargeTimeList"],
                **enable,
            )
        except asyncio.CancelledError:
            raise
        except AlphaESSApiError as err:
            if err.code == PERIODIC_NOT_ENTITLED:
                self._periodic_write_denied.add(serial)
                _LOGGER.info(
                    "%s is not entitled to periodic schedule writes: %s",
                    serial, describe_api_error(err),
                )
            elif err.code == PERIODIC_OVERLAP:
                _LOGGER.warning(
                    "AlphaESS returned generic set-failed for periodic schedule %s "
                    "(%s). Overlap is one possible cause. Charge %s, discharge %s",
                    serial, describe_api_error(err),
                    _format_periods(periodic_proposed["chargeTimeList"]),
                    _format_periods(periodic_proposed["dischargeTimeList"]),
                )
            else:
                _LOGGER.warning(
                    "Periodic schedule write for %s rejected with %s",
                    serial, describe_api_error(err),
                )
            self._publish_schedule_view(serial)
            raise ScheduleWriteError(
                f"The periodic schedule for {serial} was not updated: "
                f"{describe_api_error(err)}"
            ) from err
        except Exception as err:
            # The POST may have been stored before the response was lost.
            _LOGGER.warning("Periodic schedule write failed for %s: %s", serial, err)
            self._publish_schedule_view(serial)
            raise ScheduleWriteUnknownError(
                f"The periodic schedule write for {serial} has an unknown "
                f"outcome: {err}. Check the AlphaESS app before retrying"
            ) from err

        self._periodic_schedules[serial] = deepcopy(periodic_proposed)
        self._periodic_enable_intent[serial] = dict(enable)
        self._periodic_enable_sent[serial] = dict(enable)
        _LOGGER.info(
            "Wrote periodic schedule for %s - enable %s, charge: %s, discharge: %s",
            serial,
            enable,
            periodic_proposed["chargeTimeList"],
            periodic_proposed["dischargeTimeList"],
        )
        self._publish_schedule_view(serial)

    async def _apply_legacy_backup_locked(
        self,
        serial: str,
        *,
        charge: dict[str, Any] | None,
        discharge: dict[str, Any] | None,
        expected_legacy: dict[str, dict[str, Any]] | None,
        clear_all: bool,
    ) -> None:
        """Read-modify-write the legacy backup stores.

        Reached only for systems whose periodic READ is definitively 6017.
        Charge and discharge are two separate full-replacement stores here,
        so a two-sided change can partially fail; that surfaces as a
        SchedulePartialWriteError naming what already landed.
        """
        power_fields = {"chargePower1", "chargePower2"}
        if any(
            changes and power_fields.intersection(changes)
            for changes in (charge, discharge)
        ):
            raise ScheduleWriteError(
                "Per-period power exists only in the periodic schedule API, "
                "which AlphaESS has not enabled for this system; the legacy "
                "backup endpoints have no power field. Nothing was changed"
            )

        legacy_changes = {
            side: (
                {key: value for key, value in changes.items()}
                if changes
                else None
            )
            for side, changes in (("charge", charge), ("discharge", discharge))
        }

        # Invalidate any poll snapshot that started before this transaction.
        self._mark_schedule_write(serial)
        legacy_current: dict[str, dict[str, Any]] = {}
        for side, method in (
            ("charge", self.api.getChargeConfigInfo),
            ("discharge", self.api.getDisChargeConfigInfo),
        ):
            if not legacy_changes[side]:
                continue
            try:
                legacy_current[side] = self._normalise_legacy_side(
                    side, await self._call_cloud_api(method, serial)
                )
            except asyncio.CancelledError:
                raise
            except ScheduleWriteError:
                raise
            except Exception as err:
                raise ScheduleWriteError(
                    f"The full current legacy {side} configuration could not be "
                    f"read: {err}; nothing was changed"
                ) from err

        if expected_legacy is not None:
            for side, current in legacy_current.items():
                if current != expected_legacy.get(side):
                    raise ScheduleConflictError(
                        f"The AlphaESS legacy {side} schedule changed after this "
                        "draft was opened. Discard the draft, review the fresh "
                        "values, and try again"
                    )

        legacy_proposed = {
            side: self._normalise_legacy_side(
                side, {**legacy_current[side], **legacy_changes[side]}
            )
            for side in legacy_current
        }

        satisfied: set[str] = set()
        outcome_unknown: set[str] = set()
        errors: dict[str, Exception] = {}
        for side in ("charge", "discharge"):
            if side not in legacy_proposed:
                continue
            if legacy_proposed[side] == legacy_current[side]:
                satisfied.add(side)
                self._legacy_schedules.setdefault(serial, {})[side] = deepcopy(
                    legacy_current[side]
                )
                _LOGGER.debug(
                    "Legacy %s schedule for %s already matches the requested values",
                    side, serial,
                )
                continue
            try:
                self._mark_schedule_write(serial)
                if side == "charge":
                    await self._async_write_legacy_charge(serial, legacy_proposed[side])
                else:
                    await self._async_write_legacy_discharge(serial, legacy_proposed[side])
                satisfied.add(side)
                self._legacy_schedules.setdefault(serial, {})[side] = deepcopy(
                    legacy_proposed[side]
                )
            except asyncio.CancelledError:
                raise
            except Exception as err:
                errors[side] = err
                if not isinstance(err, AlphaESSApiError):
                    outcome_unknown.add(side)

        self._publish_schedule_view(serial)

        if errors and satisfied:
            landed = ", ".join(sorted(satisfied))
            failed = "; ".join(f"{side}: {err}" for side, err in errors.items())
            error_type = (
                SchedulePartialWriteUnknownError
                if outcome_unknown
                else SchedulePartialWriteError
            )
            failure_state = (
                "had an unknown outcome" if outcome_unknown else "failed"
            )
            raise error_type(
                f"The legacy {landed} schedule for {serial} matches the "
                f"requested values, but the other write {failure_state} "
                f"({failed}). Check the AlphaESS app before retrying"
            ) from next(iter(errors.values()))
        if errors:
            error = next(iter(errors.values()))
            error_type = (
                ScheduleWriteUnknownError if outcome_unknown else ScheduleWriteError
            )
            outcome = (
                "No schedule store is known to have accepted the change"
                if outcome_unknown
                else "No AlphaESS schedule store accepted the change"
            )
            raise error_type(f"{outcome} for {serial}: {error}") from error

    async def _async_update_data(self) -> dict[str, dict[str, Any]] | None:
        """Update data via library."""
        if self.data is None:
            self.data = {}

        if self.alt_polling_mode:
            return await self._async_update_data_alt()

        self._poll_tick_count += 1
        self._last_poll_type = "normal"

        try:
            throttle_delay = self._call_interval()

            # Get list of registered inverters
            units = await self._async_get_ess_list()
            if units is None:
                return self._finalize_data()
            if not isinstance(units, list) or any(
                not isinstance(unit, dict) for unit in units
            ):
                raise TypeError("getESSList returned an invalid response shape")
            if any(
                not isinstance(unit.get("sysSn"), str)
                or not unit["sysSn"].strip()
                for unit in units
            ):
                _LOGGER.warning("getESSList returned an item without a valid system serial")
                self.cloud_available = False
                self._update_diagnostics()
                return self._finalize_data()
            active_serials = {unit["sysSn"] for unit in units}
            if len(active_serials) != len(units):
                _LOGGER.warning("getESSList returned duplicate system serials")
                units = list({unit["sysSn"]: unit for unit in units}.values())
            self._prune_unbound_systems(active_serials)
            if not units:
                self.cloud_available = True
                self._last_full_poll_utc = dt_util.utcnow().isoformat(timespec="seconds")
                return self._finalize_data()
            await asyncio.sleep(throttle_delay)

            # Resolve the schedule mode BEFORE fetching, so the first cycle
            # after startup already polls the right store: a 6017 answer here
            # selects the legacy backup and its config reads happen in this
            # same cycle, instead of the schedule entities sitting
            # unavailable until the next full poll.
            for unit in units:
                if self._periodic_readable.get(unit["sysSn"]) is None:
                    await self.async_probe_periodic_readable(unit["sysSn"])

            # Fetch data per-inverter separately
            any_success = False
            for idx, unit in enumerate(units):
                serial = unit["sysSn"]

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
                    schedule_revision = self._schedule_write_revisions.get(serial, 0)
                    invertor = await self._fetch_inverter_data(
                        serial, unit, throttle_delay, get_power=True, get_ev=True,
                        include_local_ip=(idx == 0),
                    )
                    inverter_data = self._parse_inverter_data(invertor)
                    if self.is_legacy_backup_active(serial):
                        self._cache_legacy_state(
                            serial,
                            inverter_data,
                            expected_revision=schedule_revision,
                        )
                    self._overlay_schedule_view(serial, inverter_data)
                    self.data[serial] = inverter_data
                    self._inverter_error_count[serial] = 0
                    any_success = True
                except asyncio.CancelledError:
                    raise
                except ConfigEntryAuthFailed:
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
                await self._async_resolve_unknown_periodic_read()
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
            throttle_delay = self._call_interval()

            if need_full:
                # Full poll — per-inverter API calls
                self._last_poll_type = "full"
                _LOGGER.debug("Alt mode: performing full poll")
                units = await self._async_get_ess_list()
                if units is None:
                    return self._finalize_data()
                if not isinstance(units, list) or any(
                    not isinstance(unit, dict) for unit in units
                ):
                    raise TypeError("getESSList returned an invalid response shape")
                if any(
                    not isinstance(unit.get("sysSn"), str)
                    or not unit["sysSn"].strip()
                    for unit in units
                ):
                    _LOGGER.warning(
                        "getESSList returned an item without a valid system serial"
                    )
                    self.cloud_available = False
                    self._update_diagnostics()
                    return self._finalize_data()
                active_serials = {unit["sysSn"] for unit in units}
                if len(active_serials) != len(units):
                    _LOGGER.warning("getESSList returned duplicate system serials")
                    units = list({unit["sysSn"]: unit for unit in units}.values())
                self._prune_unbound_systems(active_serials)
                if not units:
                    self.cloud_available = True
                    self._last_full_poll = now
                    self._last_full_poll_utc = dt_util.utcnow().isoformat(timespec="seconds")
                    return self._finalize_data()
                await asyncio.sleep(throttle_delay)

                # Resolve the schedule mode before fetching; see the normal
                # poll for why this must happen in the same cycle.
                for unit in units:
                    if self._periodic_readable.get(unit["sysSn"]) is None:
                        await self.async_probe_periodic_readable(unit["sysSn"])

                any_success = False
                for idx, unit in enumerate(units):
                    serial = unit["sysSn"]
                    try:
                        schedule_revision = self._schedule_write_revisions.get(serial, 0)
                        invertor = await self._fetch_inverter_data(
                            serial, unit, throttle_delay, get_power=True, get_ev=True,
                            include_local_ip=(idx == 0),
                        )
                        inverter_data = self._parse_inverter_data(invertor)
                        if self.is_legacy_backup_active(serial):
                            self._cache_legacy_state(
                                serial,
                                inverter_data,
                                expected_revision=schedule_revision,
                            )
                        self._overlay_schedule_view(serial, inverter_data)
                        self.data[serial] = inverter_data
                        # Clear error count on success
                        self._inverter_error_count[serial] = 0
                        any_success = True
                    except asyncio.CancelledError:
                        raise
                    except ConfigEntryAuthFailed:
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
                    await self._async_resolve_unknown_periodic_read()
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
                            power_data = await self._read(self.api.getLastPowerData, serial)
                            if power_data:
                                parsed = self.parser.parse_power_data(power_data, None)
                                self.data[serial].update(parsed)
                            await asyncio.sleep(throttle_delay)

                        # getOneDateEnergyBySn — daily energy counters
                        energy_data = await self._read(
                            self.api.getOneDateEnergyBySn,
                            serial, dt_util.now().strftime("%Y-%m-%d")
                        )
                        if energy_data:
                            parsed = self.parser.parse_energy_data(energy_data)
                            self.data[serial].update(parsed)
                        await asyncio.sleep(throttle_delay)

                        # EV charger status if one is known
                        ev_sn = self.data[serial].get(AlphaESSNames.evchargersn)
                        if ev_sn:
                            ev_status = await self._read(
                                self.api.getEvChargerStatusBySn, serial, ev_sn)
                            if ev_status:
                                self.data[serial][AlphaESSNames.evchargerstatus] = ev_status.get("evchargerStatus")
                                self.data[serial][AlphaESSNames.evchargerstatusraw] = ev_status.get("evchargerStatus")
                            await asyncio.sleep(throttle_delay)

                        # Clear error count on success
                        self._inverter_error_count[serial] = 0
                    except asyncio.CancelledError:
                        raise
                    except ConfigEntryAuthFailed:
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
            self.data[serial][AlphaESSNames.PeriodicScheduleRead] = (
                self.get_periodic_read_state(serial)
            )

    def schedule_diagnostics(self, serial: str) -> dict[str, Any]:
        """Return everything needed to explain a schedule state from a report.

        Every field here answers a question that has cost a round trip with a
        tester: which store governs, why controls are unavailable, what the two
        write-only enable flags were last set to, and what the stores actually
        held at the time. It must never raise - a diagnostics download is often
        the only thing a user can still produce.
        """
        draft = self._schedule_drafts.get(serial)
        dirty = self._schedule_draft_dirty.get(serial) or {}
        now = time_mod.monotonic()

        def _cooldown(store: dict[str, float]) -> float | None:
            started = store.get(serial)
            return None if started is None else round(now - started, 1)

        if self._has_periodic_schedule_snapshot(serial):
            governing_store = "periodic"
        elif self.is_legacy_backup_active(serial):
            governing_store = "legacy-backup"
        else:
            governing_store = "none"

        return {
            "governing_store": governing_store,
            "periodic_read": self.get_periodic_read_state(serial),
            "periodic_write_denied": serial in self._periodic_write_denied,
            "capabilities": {
                "working_mode": "unavailable_through_openapi",
                "recorded_schedule_flags_enabled": (
                    self.recorded_schedule_flags_enabled(serial)
                ),
                "can_stage_schedule": self.can_stage_schedule(serial),
                "can_modify_time_controls": self.can_modify_time_controls(serial),
                "can_reset_schedule": self.can_reset_schedule(serial),
                "assume_schedule_flags_enabled": self.assume_schedule_flags_enabled,
            },
            # Write-only on the periodic API: the read reports 0 for both
            # whatever the inverter is set to, so this record is the only
            # answer there is, and an absent one blocks every write.
            "enable_intent": self._periodic_enable_intent.get(serial),
            "enable_last_sent": self._periodic_enable_sent.get(serial),
            "periodic_snapshot": self._periodic_schedules.get(serial),
            "legacy_snapshot": self._legacy_schedules.get(serial),
            "draft": None if draft is None else {
                "dirty": {side: sorted(fields) for side, fields in dirty.items()},
                "revision": self._schedule_draft_revisions.get(serial, 0),
                "apply_in_progress": serial in self._schedule_apply_in_progress,
                "staged": draft,
            },
            "number_settings": self.number_settings.get(serial),
            "seconds_since_last_charge_write": _cooldown(self.last_charge_update),
            "seconds_since_last_discharge_write": _cooldown(self.last_discharge_update),
            "consecutive_poll_errors": self._inverter_error_count.get(serial, 0),
        }

    def api_diagnostics(self) -> dict[str, Any]:
        """Return how the client is currently pacing and answering."""
        return {
            "fast_api_lane": self._fast_api_lane,
            "call_interval_seconds": self._call_interval(),
            "throttle_multiplier": self.throttle_multiplier,
            "last_poll_type": self._last_poll_type,
            "last_full_poll": self._last_full_poll_utc or "never",
            "poll_tick_count": self._poll_tick_count,
        }

    def get_periodic_read_state(self, serial: str) -> str:
        """Return whether the periodic schedule can be read on this account.

        "readable" - getTimeChargeBySn returns a schedule. "unreadable" - it is
        rejected, usually 6017. "unknown" - no answer yet.

        Only an explicit 6017 result is classified as unreadable and permits a
        legacy-only update. Unknown, empty and malformed reads fail closed.
        """
        readable = self._periodic_readable.get(serial)
        if readable is None:
            return PERIODIC_READ_UNKNOWN
        return PERIODIC_READ_OK if readable else PERIODIC_READ_UNAVAILABLE

    def _finalize_data(self) -> dict[str, dict[str, Any]]:
        """Write diagnostics and return a shallow per-serial copy of the data.

        Returning fresh dict objects each cycle ensures listeners comparing
        old/new data never see the same mutated reference.
        """
        self._update_diagnostics()
        published = {serial: dict(values) for serial, values in self.data.items()}
        self._diagnostics_data_snapshot = deepcopy(published)
        self._diagnostics_api_snapshot = deepcopy(self.api_diagnostics())
        self._diagnostics_schedule_snapshot = {
            serial: deepcopy(self.schedule_diagnostics(serial))
            for serial in published
        }
        return published

    def diagnostics_data_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return one complete published cycle for a diagnostics download."""
        source = (
            self.data
            if self._diagnostics_data_snapshot is None
            else self._diagnostics_data_snapshot
        )
        return deepcopy(source or {})

    def diagnostics_api_snapshot(self) -> dict[str, Any]:
        """Return API pacing metadata from the same completed poll cycle."""
        if self._diagnostics_api_snapshot is None:
            return self.api_diagnostics()
        return deepcopy(self._diagnostics_api_snapshot)

    def diagnostics_schedule_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return schedule diagnostics from the same completed poll cycle."""
        if self._diagnostics_schedule_snapshot is None:
            return {
                serial: deepcopy(self.schedule_diagnostics(serial))
                for serial in self.data
            }
        return deepcopy(self._diagnostics_schedule_snapshot)

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

        unit["SumData"] = await self._read(self.api.getSumDataForCustomer, serial)
        await asyncio.sleep(throttle_delay)

        unit["OneDateEnergy"] = await self._read(self.api.getOneDateEnergyBySn, serial, today)
        await asyncio.sleep(throttle_delay)

        # Skip getLastPowerData for inverters that don't support it
        if unit.get("minv") not in LOWER_INVERTER_API_CALL_LIST:
            unit["LastPower"] = await self._read(self.api.getLastPowerData, serial)
            await asyncio.sleep(throttle_delay)

        # Schedule state comes from the periodic API (primary). Only systems
        # in legacy backup mode (definitive 6017) poll the legacy two-slot
        # stores — for them it is the only schedule surface there is.
        if self.is_legacy_backup_active(serial):
            unit["ChargeConfig"] = await self._read(
                self.api.getChargeConfigInfo, serial
            )
            await asyncio.sleep(throttle_delay)
            unit["DisChargeConfig"] = await self._read(
                self.api.getDisChargeConfigInfo, serial
            )
            await asyncio.sleep(throttle_delay)

        if get_power:
            unit["OneDayPower"] = await self._read(self.api.getOneDayPowerBySn, serial, today)
            await asyncio.sleep(throttle_delay)

        if get_ev:
            try:
                unit["EVData"] = await self._read(self.api.getEvChargerConfigList, serial)
                await asyncio.sleep(throttle_delay)
                if unit["EVData"]:
                    ev_list = unit["EVData"]
                    if (
                        isinstance(ev_list, list)
                        and len(ev_list) > 1
                        and serial not in self._multi_ev_warned
                    ):
                        self._multi_ev_warned.add(serial)
                        _LOGGER.warning(
                            "AlphaESS returned %s EV chargers for %s; this integration "
                            "currently exposes only the first charger",
                            len(ev_list), serial,
                        )
                    ev_item = ev_list[0] if isinstance(ev_list, list) else ev_list
                    ev_serial = ev_item.get("evchargerSn")
                    if ev_serial:
                        unit["EVStatus"] = await self._read(
                            self.api.getEvChargerStatusBySn, serial, ev_serial)
                        await asyncio.sleep(throttle_delay)
                        unit["EVCurrent"] = await self._read(
                            self.api.getEvChargerCurrentsBySn, serial)
                        await asyncio.sleep(throttle_delay)
            except asyncio.CancelledError:
                raise
            except ConfigEntryAuthFailed:
                raise
            except Exception:
                _LOGGER.debug("Failed to fetch EV data for %s", serial, exc_info=True)

        # Include local IP data if available and this is the first inverter
        if include_local_ip and self.api.ipaddress:
            try:
                ip_data = await self.api.getIPData()
                self._record_local_raw(serial, self.api.ipaddress, ip_data)
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
                    self._record_local_raw(serial, ip, local_ip_raw)
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
                    self._record_local_raw(serial, ip, local_ip_raw)
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

        # Schedule configuration (times, cutoffs, enable flags, Charging
        # Range) is owned by _put_schedule_state via the periodic overlay.
        # In legacy backup mode the polled two-slot stores feed the same keys.
        charge_config = invertor.get("ChargeConfig", {})
        if charge_config:
            data.update(self.parser.parse_charge_config(charge_config))
        discharge_config = invertor.get("DisChargeConfig", {})
        if discharge_config:
            data.update(self.parser.parse_discharge_config(discharge_config))
        if charge_config and discharge_config:
            bat_high_cap = charge_config.get("batHighCap")
            bat_use_cap = discharge_config.get("batUseCap")
            if bat_high_cap is not None and bat_use_cap is not None:
                data[AlphaESSNames.ChargeRange] = (
                    f"{bat_use_cap}% - {bat_high_cap}%"
                )
        return data
