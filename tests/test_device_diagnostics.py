"""Tests for device info builders and diagnostics."""
import time as time_mod
from dataclasses import dataclass

import pytest
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import UNDEFINED, UndefinedType

from custom_components.alphaess.device import (
    build_ev_charger_device_info,
    build_inverter_device_info,
)
from custom_components.alphaess.diagnostics import async_get_config_entry_diagnostics
from custom_components.alphaess.enums import AlphaESSNames

from .conftest import FakeEntry

SERIAL = "al1000021000123"


@dataclass(kw_only=True, slots=True)
class DataclassDeviceInfo:
    """Exercise the Core 2026.11 shape without its mapping compatibility layer."""

    identifiers: set[tuple[str, str]] | UndefinedType = UNDEFINED
    manufacturer: str | None | UndefinedType = UNDEFINED
    model: str | None | UndefinedType = UNDEFINED
    serial_number: str | None | UndefinedType = UNDEFINED
    name: str | None | UndefinedType = UNDEFINED
    sw_version: str | None | UndefinedType = UNDEFINED
    hw_version: str | None | UndefinedType = UNDEFINED
    configuration_url: str | None | UndefinedType = UNDEFINED


@pytest.fixture(params=[DeviceInfo, DataclassDeviceInfo], ids=["native", "dataclass"])
def device_info_type(request, monkeypatch):
    """Run builders against native DeviceInfo and a strict dataclass."""
    monkeypatch.setattr("custom_components.alphaess.device.DeviceInfo", request.param)
    return request.param


class TestInverterDeviceInfo:
    @pytest.mark.parametrize("data", [{"Model": "SMILE5-INV"}, {}], ids=["model", "missing_model"])
    def test_basic(self, device_info_type, data):
        info = build_inverter_device_info(SERIAL, data)
        assert info == device_info_type(
            identifiers={("alphaess", SERIAL.upper())},
            manufacturer="AlphaESS",
            model=data.get("Model"),
            serial_number=SERIAL,
            name=f"Alpha ESS Energy Statistics : {SERIAL.upper()}",
        )

    @pytest.mark.parametrize("status", [0, 1])
    def test_with_local_ip(self, device_info_type, status):
        data = {
            "Model": "SMILE5-INV",
            AlphaESSNames.localIP: "192.168.1.5",
            AlphaESSNames.deviceStatus: status,
            AlphaESSNames.deviceSerialNumber: "AL1234",
            AlphaESSNames.softwareVersion: "1.2.3",
            AlphaESSNames.hardwareVersion: "A1",
        }
        info = build_inverter_device_info(SERIAL, data)
        # DeviceInfo carries the system serial; the comms-dongle SN stays
        # available as its own diagnostic sensor.
        assert info == device_info_type(
            identifiers={("alphaess", SERIAL.upper())},
            manufacturer="AlphaESS",
            model="SMILE5-INV",
            serial_number=SERIAL,
            name=f"Alpha ESS Energy Statistics : {SERIAL.upper()}",
            sw_version="1.2.3",
            hw_version="A1",
            configuration_url="http://192.168.1.5",
        )

    @pytest.mark.parametrize(
        ("local_ip", "status"),
        [(None, 1), ("", 1), ("0", 1), ("192.168.1.5", None)],
        ids=["missing_ip", "empty_ip", "zero_ip", "missing_status"],
    )
    def test_unavailable_local_metadata_omitted(self, device_info_type, local_ip, status):
        data = {
            "Model": "SMILE5-INV",
            AlphaESSNames.localIP: local_ip,
            AlphaESSNames.deviceStatus: status,
            AlphaESSNames.softwareVersion: "1.2.3",
            AlphaESSNames.hardwareVersion: "A1",
        }
        info = build_inverter_device_info(SERIAL, data)
        assert info == device_info_type(
            identifiers={("alphaess", SERIAL.upper())},
            manufacturer="AlphaESS",
            model="SMILE5-INV",
            serial_number=SERIAL,
            name=f"Alpha ESS Energy Statistics : {SERIAL.upper()}",
        )

    def test_local_ip_with_missing_versions(self, device_info_type):
        data = {
            AlphaESSNames.localIP: "192.168.1.5",
            AlphaESSNames.deviceStatus: 1,
        }
        info = build_inverter_device_info(SERIAL, data)
        assert info == device_info_type(
            identifiers={("alphaess", SERIAL.upper())},
            manufacturer="AlphaESS",
            model=None,
            serial_number=SERIAL,
            name=f"Alpha ESS Energy Statistics : {SERIAL.upper()}",
            sw_version=None,
            hw_version=None,
            configuration_url="http://192.168.1.5",
        )


class TestEvChargerDeviceInfo:
    @pytest.mark.parametrize("model", ["SMILE-EVCT11", None])
    def test_basic(self, device_info_type, model):
        data = {
            AlphaESSNames.evchargersn: "EV123",
            AlphaESSNames.evchargermodel: model,
        }
        info = build_ev_charger_device_info(data)
        assert info == device_info_type(
            identifiers={("alphaess", "EV123")},
            manufacturer="AlphaESS",
            model=model,
            serial_number="EV123",
            name="Alpha ESS Charger : EV123",
        )


class TestDiagnostics:
    async def test_diagnostics_redacts_sensitive_data(self, mock_hass, make_coordinator):
        coordinator = make_coordinator(models=["SMILE5-INV"])
        coordinator.data = {
            "AL_SERIAL": {
                "Model": "SMILE5-INV",
                AlphaESSNames.TotalLoad: 5.0,
                AlphaESSNames.registerKey: "secret-key",
                AlphaESSNames.password: "wifi-pass",
            }
        }
        entry = FakeEntry(
            data={"AppID": "app-id", "AppSecret": "app-secret"},
            options={"scan_interval_seconds": 60},
        )
        entry.runtime_data = coordinator

        result = await async_get_config_entry_diagnostics(mock_hass, entry)

        assert result["entry"]["data"]["AppID"] == "**REDACTED**"
        assert result["entry"]["data"]["AppSecret"] == "**REDACTED**"
        inverter = result["data"]["inverter_1"]
        assert inverter[AlphaESSNames.registerKey] == "**REDACTED**"
        assert inverter[AlphaESSNames.password] == "**REDACTED**"
        assert inverter[AlphaESSNames.TotalLoad] == 5.0
        assert "AL_SERIAL" not in result["data"]
        assert result["coordinator"]["inverter_count"] == 1

    async def test_diagnostics_with_empty_data(self, mock_hass, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {}
        entry = FakeEntry()
        entry.runtime_data = coordinator

        result = await async_get_config_entry_diagnostics(mock_hass, entry)
        assert result["data"] == {}

    async def test_multi_inverter_download_uses_one_completed_poll(
        self, mock_hass, make_coordinator
    ):
        """A download during the next sequential poll must not contain the
        freshly replaced first inverter and stale second inverter."""
        coordinator = make_coordinator(models=["A", "B"])
        coordinator.data = {
            "SERIAL_A": {"Model": "A", AlphaESSNames.TotalLoad: 1.0},
            "SERIAL_B": {"Model": "B", AlphaESSNames.TotalLoad: 2.0},
        }
        coordinator._periodic_readable.update({"SERIAL_A": False, "SERIAL_B": False})
        coordinator._legacy_schedules["SERIAL_A"] = {
            "charge": {"gridCharge": 1, "timeChaf1": "01:00"},
            "discharge": {"ctrDis": 1},
        }
        coordinator._legacy_schedules["SERIAL_B"] = {
            "charge": {"gridCharge": 1, "timeChaf1": "02:00"},
            "discharge": {"ctrDis": 1},
        }
        coordinator._poll_tick_count = 728
        coordinator._last_poll_type = "normal"
        coordinator._finalize_data()

        # Simulate the next poll after it has replaced only the first serial.
        coordinator._poll_tick_count = 729
        coordinator.data["SERIAL_A"] = {
            "Model": "A", AlphaESSNames.TotalLoad: 9.0,
        }
        coordinator._legacy_schedules["SERIAL_A"]["charge"]["timeChaf1"] = "09:00"
        entry = FakeEntry()
        entry.runtime_data = coordinator

        result = await async_get_config_entry_diagnostics(mock_hass, entry)

        first = result["data"]["inverter_1"]
        second = result["data"]["inverter_2"]
        assert first[AlphaESSNames.TotalLoad] == 1.0
        assert second[AlphaESSNames.TotalLoad] == 2.0
        assert first[AlphaESSNames.PollTickCount] == 728
        assert second[AlphaESSNames.PollTickCount] == 728
        assert result["coordinator"]["api"]["poll_tick_count"] == 728
        assert result["schedule"]["inverter_1"]["legacy_snapshot"]["charge"][
            "timeChaf1"
        ] == "01:00"
        assert result["schedule_live"]["inverter_1"]["legacy_snapshot"]["charge"][
            "timeChaf1"
        ] == "09:00"


class TestScheduleDiagnostics:
    """Every field here exists because answering a report needed it and the
    download did not have it."""

    def _periodic(self):
        return {
            "executeCycleType": 1,
            "chargeTimeList": [
                {
                    "beginTime": "05:00",
                    "endTime": "06:00",
                    "weeks": [1, 2, 3, 4, 5, 6, 7],
                    "chargePower": 5000,
                    "chargeLimit": 55,
                }
            ],
            "dischargeTimeList": [],
        }

    async def test_a_periodic_system_reports_why_it_is_in_that_state(
        self, mock_hass, make_coordinator
    ):
        coordinator = make_coordinator()
        coordinator.data = {"AL_SERIAL": {"Model": "SMILE-G3-S5-INV"}}
        coordinator._periodic_readable["AL_SERIAL"] = True
        coordinator._periodic_schedules["AL_SERIAL"] = self._periodic()
        coordinator.set_periodic_enable_intent(
            "AL_SERIAL", grid_charge=1, ctr_dis=0,
        )
        coordinator.last_charge_update["AL_SERIAL"] = time_mod.monotonic()
        entry = FakeEntry()
        entry.runtime_data = coordinator

        result = await async_get_config_entry_diagnostics(mock_hass, entry)
        schedule = result["schedule"]["inverter_1"]

        assert schedule["governing_store"] == "periodic"
        assert schedule["periodic_read"] == "readable"
        assert schedule["periodic_write_denied"] is False
        # The write-only pair: the only record there is.
        assert schedule["enable_intent"] == {
            "gridChargeCycle": 1, "ctrDisCycle": 0,
        }
        # The resource itself, so an empty list is visible without asking.
        assert schedule["periodic_snapshot"]["dischargeTimeList"] == []
        assert schedule["capabilities"]["can_modify_time_controls"] is True
        assert schedule["capabilities"]["assume_schedule_flags_enabled"] is False
        assert schedule["draft"] is None
        assert schedule["seconds_since_last_charge_write"] is not None
        assert schedule["seconds_since_last_discharge_write"] is None
        assert result["coordinator"]["api"]["fast_api_lane"] is True

    async def test_a_backup_system_reports_the_legacy_snapshot(
        self, mock_hass, make_coordinator
    ):
        """The store a migrated system no longer uses is exactly what needs to
        be visible when it is the one being read."""
        coordinator = make_coordinator()
        coordinator.data = {"AL_SERIAL": {"Model": "SMILE5-INV"}}
        coordinator._periodic_readable["AL_SERIAL"] = False
        coordinator._legacy_schedules["AL_SERIAL"] = {
            "charge": {"gridCharge": 0, "timeChaf1": "17:00", "timeChae1": "17:15"},
            "discharge": {"ctrDis": 0, "timeDisf1": "00:00", "timeDise1": "00:00"},
        }
        entry = FakeEntry()
        entry.runtime_data = coordinator

        result = await async_get_config_entry_diagnostics(mock_hass, entry)
        schedule = result["schedule"]["inverter_1"]

        assert schedule["governing_store"] == "legacy-backup"
        assert schedule["legacy_snapshot"]["charge"]["timeChaf1"] == "17:00"
        assert schedule["capabilities"]["can_reset_schedule"] is True

    async def test_an_open_draft_is_described(self, mock_hass, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {"AL_SERIAL": {"Model": "SMILE-G3-S5-INV"}}
        coordinator._periodic_readable["AL_SERIAL"] = True
        coordinator._periodic_schedules["AL_SERIAL"] = self._periodic()
        coordinator.set_periodic_enable_intent(
            "AL_SERIAL", grid_charge=1, ctr_dis=1,
        )
        coordinator.stage_schedule_change("AL_SERIAL", charge={"timeChaf1": "02:00"})
        entry = FakeEntry()
        entry.runtime_data = coordinator

        result = await async_get_config_entry_diagnostics(mock_hass, entry)
        draft = result["schedule_live"]["inverter_1"]["draft"]

        assert draft["dirty"]["charge"] == ["timeChaf1"]
        assert draft["apply_in_progress"] is False
        assert draft["staged"]["charge"]["timeChaf1"] == "02:00"

    async def test_a_system_with_no_usable_store_says_so(
        self, mock_hass, make_coordinator
    ):
        coordinator = make_coordinator()
        coordinator.data = {"AL_SERIAL": {"Model": "SMILE5-INV"}}
        entry = FakeEntry()
        entry.runtime_data = coordinator

        result = await async_get_config_entry_diagnostics(mock_hass, entry)
        schedule = result["schedule"]["inverter_1"]

        assert schedule["governing_store"] == "none"
        assert schedule["periodic_read"] == "unknown"
        assert schedule["enable_intent"] is None
        assert schedule["capabilities"]["can_stage_schedule"] is False
