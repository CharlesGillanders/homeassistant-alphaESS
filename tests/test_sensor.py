"""Tests for the sensor platform."""

from dataclasses import replace
from enum import StrEnum

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CURRENCY_DOLLAR

from custom_components.alphaess.const import (
    CONF_INVERTER_MODEL,
    CONF_IP_ADDRESS,
    CONF_SERIAL_NUMBER,
    SUBENTRY_TYPE_INVERTER,
)
from custom_components.alphaess.enums import AlphaESSNames
from custom_components.alphaess.sensor import AlphaESSSensor, async_setup_entry
from custom_components.alphaess.sensorlist import (
    EV_CHARGING_DETAILS,
    FULL_SENSOR_DESCRIPTIONS,
    LIMITED_SENSOR_DESCRIPTIONS,
    LOCAL_IP_SYSTEM_SENSORS,
)

from .conftest import CHARGER_INFO, FakeEntry

SERIAL = "AL1000021000123"

ALL_DESCRIPTIONS = (
    FULL_SENSOR_DESCRIPTIONS
    + LIMITED_SENSOR_DESCRIPTIONS
    + EV_CHARGING_DETAILS
    + LOCAL_IP_SYSTEM_SENSORS
)


def _inverter_subentry(serial=SERIAL, model="SMILE5-INV", ip=""):
    return ConfigSubentry(
        data={
            CONF_SERIAL_NUMBER: serial,
            CONF_INVERTER_MODEL: model,
            CONF_IP_ADDRESS: ip,
        },
        subentry_type=SUBENTRY_TYPE_INVERTER,
        title=f"{model} ({serial})",
        unique_id=f"{SUBENTRY_TYPE_INVERTER}_{serial}",
    )



def _entry_for(subentries, coordinator=None):
    entry = FakeEntry(subentries={sub.subentry_id: sub for sub in subentries})
    entry.runtime_data = coordinator
    return entry


def _description(key):
    return next(d for d in ALL_DESCRIPTIONS if d.key == key)


class TestSensorSetup:
    async def test_full_model_creates_entities(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV", "Currency": "USD"}}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await async_setup_entry(mock_hass, entry, add_entities)
        assert len(add_entities.entities) > 30

    async def test_limited_model_creates_fewer_entities(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "Storion-S5"}}
        entry = _entry_for([_inverter_subentry(model="Storion-S5")], coordinator)

        await async_setup_entry(mock_hass, entry, add_entities)
        limited_count = len(add_entities.entities)
        assert 0 < limited_count < len(FULL_SENSOR_DESCRIPTIONS)

    async def test_serial_missing_from_data_skipped(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await async_setup_entry(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_local_ip_sensors_created(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                "Model": "SMILE5-INV",
                AlphaESSNames.localIP: "192.168.1.5",
                AlphaESSNames.deviceStatus: 1,
            }
        }
        entry = _entry_for([_inverter_subentry()], coordinator)

        await async_setup_entry(mock_hass, entry, add_entities)
        names = {e._name for e in add_entities.entities}
        assert AlphaESSNames.localIP.value in names

    async def test_ev_connector_sensors_skipped_when_absent(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                "Model": "SMILE5-INV",
                AlphaESSNames.ElectricVehiclePowerOne: None,
            }
        }
        entry = _entry_for([_inverter_subentry()], coordinator)

        await async_setup_entry(mock_hass, entry, add_entities)
        keys = {e._key for e in add_entities.entities}
        assert AlphaESSNames.pev not in keys
        assert AlphaESSNames.ElectricVehiclePowerOne not in keys

    async def test_ev_connector_sensors_included_when_present(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                "Model": "SMILE5-INV",
                AlphaESSNames.ElectricVehiclePowerOne: 7000,
            }
        }
        entry = _entry_for([_inverter_subentry()], coordinator)

        await async_setup_entry(mock_hass, entry, add_entities)
        keys = {e._key for e in add_entities.entities}
        assert AlphaESSNames.pev in keys
        assert AlphaESSNames.ElectricVehiclePowerOne in keys

    async def test_auto_discovered_ev_without_subentry(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                "Model": "SMILE5-INV",
                AlphaESSNames.evchargersn: "EV999",
                AlphaESSNames.evchargermodel: "SMILE-EVCS7",
            }
        }
        coordinator.charger_device_info = {SERIAL: CHARGER_INFO}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await async_setup_entry(mock_hass, entry, add_entities)
        # The charger's sensors join the inverter's batch on its subentry.
        assert len(add_entities.calls) == 1
        charger_sensors = [e for e in add_entities.entities if e.device_info is CHARGER_INFO]
        assert len(charger_sensors) == len(EV_CHARGING_DETAILS)

    async def test_inverter_subentry_without_serial(
        self, mock_hass, make_coordinator, add_entities
    ):
        sub = ConfigSubentry(
            data={CONF_SERIAL_NUMBER: ""},
            subentry_type=SUBENTRY_TYPE_INVERTER,
            title="broken",
            unique_id="inverter_broken",
        )
        coordinator = make_coordinator()
        coordinator.data = {}
        entry = _entry_for([sub], coordinator)

        await async_setup_entry(mock_hass, entry, add_entities)
        assert add_entities.entities == []


def _make_sensor(coordinator, key, currency="USD", serial=SERIAL):
    return AlphaESSSensor(
        coordinator, FakeEntry(), serial, _description(key), currency
    )


class TestSensorEntity:
    def test_basic_properties(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.SolarProduction: 5.0}}
        sensor = _make_sensor(coordinator, AlphaESSNames.SolarProduction)

        assert sensor.unique_id == f"test_entry_{SERIAL} - Solar Production"
        assert sensor.name == "Solar Production"
        assert sensor.suggested_object_id == f"{SERIAL} Solar Production"
        assert sensor.native_value == 5.0
        assert sensor.device_class is not None
        assert sensor.state_class is not None
        assert sensor.icon is None or isinstance(sensor.icon, str)
        assert sensor.entity_category is None or sensor.entity_category

    @pytest.mark.parametrize("unit", [CURRENCY_DOLLAR, StrEnum("CurrencyMarker", {"DOLLAR": "$"}).DOLLAR])
    def test_currency_unit_substitution(self, make_coordinator, unit):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        description = replace(_description(AlphaESSNames.Income), native_unit_of_measurement=unit)
        income = AlphaESSSensor(coordinator, FakeEntry(), SERIAL, description, "EUR")
        assert income.native_unit_of_measurement == "EUR"

        solar = _make_sensor(coordinator, AlphaESSNames.SolarProduction)
        assert solar.native_unit_of_measurement == "kWh"

    def test_device_info_attached(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        device_info = {"identifiers": {("alphaess", SERIAL)}}
        sensor = AlphaESSSensor(
            coordinator, FakeEntry(), SERIAL,
            _description(AlphaESSNames.SolarProduction), "USD",
            device_info=device_info,
        )
        assert sensor._attr_device_info == device_info

    def test_available_requires_update_success(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.SolarProduction: 1.0}}
        sensor = _make_sensor(coordinator, AlphaESSNames.SolarProduction)

        coordinator.last_update_success = False
        assert sensor.available is False

        coordinator.last_update_success = True
        assert sensor.available is True

    def test_available_data_none(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.last_update_success = True
        coordinator.data = None
        sensor = _make_sensor(coordinator, AlphaESSNames.SolarProduction)
        assert sensor.available is False

    def test_available_serial_missing(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.last_update_success = True
        coordinator.data = {}
        sensor = _make_sensor(coordinator, AlphaESSNames.SolarProduction)
        assert sensor.available is False

    def test_available_ev_keys_require_charger(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.last_update_success = True
        coordinator.data = {SERIAL: {AlphaESSNames.pev: 5}}
        sensor = _make_sensor(coordinator, AlphaESSNames.pev)
        assert sensor.available is False

        coordinator.data = {
            SERIAL: {
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.pev: 5,
                AlphaESSNames.ElectricVehiclePowerOne: 5,
            }
        }
        assert sensor.available is True

    def test_available_pev_requires_connector_one(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.last_update_success = True
        coordinator.data = {
            SERIAL: {
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.pev: 5,
                AlphaESSNames.ElectricVehiclePowerOne: None,
            }
        }
        sensor = _make_sensor(coordinator, AlphaESSNames.pev)
        assert sensor.available is False

    def test_available_connector_power(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.last_update_success = True
        coordinator.data = {
            SERIAL: {
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.ElectricVehiclePowerTwo: None,
            }
        }
        sensor = _make_sensor(coordinator, AlphaESSNames.ElectricVehiclePowerTwo)
        assert sensor.available is False

        coordinator.data[SERIAL][AlphaESSNames.ElectricVehiclePowerTwo] = 11
        assert sensor.available is True

    def test_available_au_nullable_keys(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.last_update_success = True
        coordinator.data = {SERIAL: {AlphaESSNames.TotalLoad: None}}
        sensor = _make_sensor(coordinator, AlphaESSNames.TotalLoad)
        assert sensor.available is False

        coordinator.data[SERIAL][AlphaESSNames.TotalLoad] = 8.5
        assert sensor.available is True

    def test_available_key_presence(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.last_update_success = True
        coordinator.data = {SERIAL: {}}
        sensor = _make_sensor(coordinator, AlphaESSNames.SolarProduction)
        assert sensor.available is False

    def test_native_value_data_none(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = None
        sensor = _make_sensor(coordinator, AlphaESSNames.SolarProduction)
        assert sensor.native_value is None

    def test_native_value_ev_status_enum(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatus: 3}}
        sensor = _make_sensor(coordinator, AlphaESSNames.evchargerstatus)
        assert sensor.native_value == "charging"

        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatus: 77}}
        assert sensor.native_value == "unknown"

        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatus: "junk"}}
        assert sensor.native_value == "unknown"

        coordinator.data = {SERIAL: {}}
        assert sensor.native_value is None

    def test_native_value_status_lookups(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.cloudConnectionStatus: 0}}
        sensor = _make_sensor(coordinator, AlphaESSNames.cloudConnectionStatus)
        assert sensor.native_value == "connected_ok"

        coordinator.data = {SERIAL: {AlphaESSNames.cloudConnectionStatus: "x"}}
        assert sensor.native_value == "connect_fail"

        coordinator.data = {SERIAL: {}}
        assert sensor.native_value is None

        coordinator.data = {SERIAL: {AlphaESSNames.wifiStatus: 5}}
        wifi = _make_sensor(coordinator, AlphaESSNames.wifiStatus)
        assert wifi.native_value == "connected_ok"

        coordinator.data = {SERIAL: {AlphaESSNames.ethernetModule: 0}}
        eth = _make_sensor(coordinator, AlphaESSNames.ethernetModule)
        assert eth.native_value == "link_up"

        coordinator.data = {SERIAL: {AlphaESSNames.fourGModule: -1}}
        fourg = _make_sensor(coordinator, AlphaESSNames.fourGModule)
        assert fourg.native_value == "initialization"

    def test_native_value_charge_time(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.ChargeTime1: "01:00 - 05:00"}}
        sensor = _make_sensor(coordinator, AlphaESSNames.ChargeTime1)
        assert sensor.native_value == "01:00 - 05:00"

    def test_options(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}

        assert "charging" in _make_sensor(coordinator, AlphaESSNames.evchargerstatus).options
        assert "connected_ok" in _make_sensor(coordinator, AlphaESSNames.cloudConnectionStatus).options
        assert "link_up" in _make_sensor(coordinator, AlphaESSNames.ethernetModule).options
        assert "ok" in _make_sensor(coordinator, AlphaESSNames.fourGModule).options
        assert "connecting" in _make_sensor(coordinator, AlphaESSNames.wifiStatus).options
        assert _make_sensor(coordinator, AlphaESSNames.SolarProduction).options is None

    def test_translation_keys(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}

        cases = {
            AlphaESSNames.evchargerstatus: "ev_charger_status",
            AlphaESSNames.cloudConnectionStatus: "tcp_status",
            AlphaESSNames.ethernetModule: "ethernet_status",
            AlphaESSNames.fourGModule: "four_g_status",
            AlphaESSNames.wifiStatus: "wifi_status",
        }
        for key, expected in cases.items():
            sensor = _make_sensor(coordinator, key)
            if sensor._device_class == SensorDeviceClass.ENUM:
                assert sensor.translation_key == expected

        assert _make_sensor(coordinator, AlphaESSNames.SolarProduction).translation_key is None
