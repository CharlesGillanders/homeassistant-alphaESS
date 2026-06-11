"""Tests for device info builders and diagnostics."""
from custom_components.alphaess.device import (
    build_ev_charger_device_info,
    build_inverter_device_info,
)
from custom_components.alphaess.diagnostics import async_get_config_entry_diagnostics
from custom_components.alphaess.enums import AlphaESSNames

from .conftest import FakeEntry

SERIAL = "al1000021000123"


class TestInverterDeviceInfo:
    def test_basic(self):
        info = build_inverter_device_info(SERIAL, {"Model": "SMILE5-INV"})
        assert info["identifiers"] == {("alphaess", SERIAL.upper())}
        assert info["model"] == "SMILE5-INV"
        assert "configuration_url" not in info

    def test_with_local_ip(self):
        data = {
            "Model": "SMILE5-INV",
            AlphaESSNames.localIP: "192.168.1.5",
            AlphaESSNames.deviceStatus: 1,
            AlphaESSNames.deviceSerialNumber: "AL1234",
            AlphaESSNames.softwareVersion: "1.2.3",
            AlphaESSNames.hardwareVersion: "A1",
        }
        info = build_inverter_device_info(SERIAL, data)
        assert info["configuration_url"] == "http://192.168.1.5"
        # DeviceInfo carries the system serial; the comms-dongle SN stays
        # available as its own diagnostic sensor.
        assert info["serial_number"] == SERIAL
        assert info["sw_version"] == "1.2.3"
        assert info["hw_version"] == "A1"

    def test_local_ip_zero_ignored(self):
        data = {
            "Model": "SMILE5-INV",
            AlphaESSNames.localIP: "0",
            AlphaESSNames.deviceStatus: 1,
        }
        info = build_inverter_device_info(SERIAL, data)
        assert "configuration_url" not in info

    def test_local_ip_without_status_ignored(self):
        data = {"Model": "SMILE5-INV", AlphaESSNames.localIP: "192.168.1.5"}
        info = build_inverter_device_info(SERIAL, data)
        assert "configuration_url" not in info


class TestEvChargerDeviceInfo:
    def test_basic(self):
        data = {
            AlphaESSNames.evchargersn: "EV123",
            AlphaESSNames.evchargermodel: "SMILE-EVCT11",
        }
        info = build_ev_charger_device_info(data)
        assert info["identifiers"] == {("alphaess", "EV123")}
        assert info["model"] == "SMILE-EVCT11"


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
