"""Tests for binary_sensor, switch and time platforms."""
from datetime import time as dt_time
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigSubentry

from custom_components.alphaess.binary_sensor import (
    AlphaEVReadinessBinarySensor,
)
from custom_components.alphaess.binary_sensor import (
    async_setup_entry as binary_setup,
)
from custom_components.alphaess.const import (
    CONF_INVERTER_MODEL,
    CONF_IP_ADDRESS,
    CONF_PARENT_INVERTER,
    CONF_SERIAL_NUMBER,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from custom_components.alphaess.enums import AlphaESSNames
from custom_components.alphaess.sensorlist import (
    CHARGE_DISCHARGE_SWITCHES,
    CHARGE_DISCHARGE_TIMES,
    EV_CHARGER_BINARY_SENSORS,
)
from custom_components.alphaess.switch import AlphaSwitch
from custom_components.alphaess.switch import async_setup_entry as switch_setup
from custom_components.alphaess.time import AlphaTime
from custom_components.alphaess.time import async_setup_entry as time_setup

from .conftest import FakeEntry

SERIAL = "AL1000021000123"


def _inverter_subentry(serial=SERIAL, model="SMILE5-INV"):
    return ConfigSubentry(
        data={
            CONF_SERIAL_NUMBER: serial,
            CONF_INVERTER_MODEL: model,
            CONF_IP_ADDRESS: "",
        },
        subentry_type=SUBENTRY_TYPE_INVERTER,
        title=f"{model} ({serial})",
        unique_id=f"{SUBENTRY_TYPE_INVERTER}_{serial}",
    )


def _ev_subentry(ev_serial="EV123", parent=SERIAL):
    return ConfigSubentry(
        data={CONF_SERIAL_NUMBER: ev_serial, CONF_PARENT_INVERTER: parent},
        subentry_type=SUBENTRY_TYPE_EV_CHARGER,
        title=f"EV ({ev_serial})",
        unique_id=f"{SUBENTRY_TYPE_EV_CHARGER}_{ev_serial}",
    )


def _entry_for(subentries, coordinator):
    entry = FakeEntry(subentries={sub.subentry_id: sub for sub in subentries})
    entry.runtime_data = coordinator
    return entry


class TestBinarySensorSetup:
    async def test_auto_discovered_ev(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.evchargermodel: "SMILE-EVCT11",
            }
        }
        entry = _entry_for([_inverter_subentry()], coordinator)

        await binary_setup(mock_hass, entry, add_entities)
        assert len(add_entities.entities) == len(EV_CHARGER_BINARY_SENSORS)

    async def test_no_ev_charger_no_entities(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await binary_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_serial_missing_skipped(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await binary_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_ev_with_subentry_not_duplicated_on_inverter(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.evchargermodel: "SMILE-EVCT11",
            }
        }
        entry = _entry_for([_inverter_subentry(), _ev_subentry()], coordinator)

        await binary_setup(mock_hass, entry, add_entities)
        # only via the EV subentry path
        assert len(add_entities.calls) == 1

    async def test_ev_subentry_missing_parent(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {}
        entry = _entry_for([_ev_subentry(parent="GONE")], coordinator)

        await binary_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_ev_subentry_parent_without_charger(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        entry = _entry_for([_ev_subentry()], coordinator)

        await binary_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []


class TestBinarySensorEntity:
    def _make(self, coordinator, direction_index=0, device_info=None):
        return AlphaEVReadinessBinarySensor(
            coordinator,
            SERIAL,
            FakeEntry(),
            EV_CHARGER_BINARY_SENSORS[direction_index],
            ev_serial="EV123",
            device_info=device_info,
        )

    def test_is_on_states(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 2}}
        start = self._make(coordinator, 0)  # direction=1
        stop = self._make(coordinator, 1)  # direction=0
        assert start.is_on is True
        assert stop.is_on is False

    def test_is_on_none_when_no_status(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        assert self._make(coordinator).is_on is None

    def test_is_on_none_when_no_direction(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 2}}
        sensor = self._make(coordinator)
        sensor._direction = None
        assert sensor.is_on is None

    def test_available(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"EV Charger S/N": "EV123"}}
        sensor = self._make(coordinator, device_info={"identifiers": {("alphaess", "EV123")}})

        coordinator.last_update_success = False
        assert sensor.available is False

        coordinator.last_update_success = True
        coordinator.cloud_available = False
        assert sensor.available is False

        coordinator.cloud_available = True
        assert sensor.available is True

        coordinator.data = {SERIAL: {}}
        assert sensor.available is False

    def test_properties(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        sensor = self._make(coordinator)
        assert SERIAL in sensor.unique_id
        assert sensor.name == "Can Start Charging"
        assert sensor.suggested_object_id == f"{SERIAL} Can Start Charging"
        assert sensor.entity_category is not None
        assert sensor.icon


class TestSwitchSetup:
    async def test_entities_created(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await switch_setup(mock_hass, entry, add_entities)
        assert len(add_entities.entities) == len(CHARGE_DISCHARGE_SWITCHES)

    async def test_blacklisted_model_skipped(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "VT1000"}}
        entry = _entry_for([_inverter_subentry(model="VT1000")], coordinator)

        await switch_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_non_inverter_subentry_skipped(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        entry = _entry_for([_ev_subentry()], coordinator)

        await switch_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_serial_missing_skipped(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await switch_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []


class TestSwitchEntity:
    def _make(self, coordinator, index=0, device_info=None):
        return AlphaSwitch(
            coordinator, SERIAL, FakeEntry(),
            CHARGE_DISCHARGE_SWITCHES[index], device_info=device_info,
        )

    def test_is_on_variants(self, make_coordinator):
        coordinator = make_coordinator()
        switch = self._make(coordinator)  # gridCharge
        key = switch._coordinator_key

        coordinator.data = {SERIAL: {}}
        assert switch.is_on is None

        coordinator.data = {SERIAL: {key: 1}}
        assert switch.is_on is True

        coordinator.data = {SERIAL: {key: 0}}
        assert switch.is_on is False

        switch._optimistic_state = True
        assert switch.is_on is True

    def test_coordinator_update_clears_optimistic(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        switch = self._make(coordinator)
        switch._optimistic_state = True
        switch.async_write_ha_state = MagicMock()
        switch._handle_coordinator_update()
        assert switch._optimistic_state is None

    async def test_turn_on_grid_charge(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                AlphaESSNames.batHighCap: 95,
                "charge_timeChae1": "05:00",
            }
        }
        switch = self._make(coordinator, 0)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()
        assert switch._optimistic_state is True
        args = mock_api.updateChargeConfigInfo.await_args.args
        assert args == (SERIAL, 95, 1, "05:00", "00:00", "00:00", "00:00")

    async def test_turn_off_discharge(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.batUseCap: 15}}
        # find the ctrDis switch
        index = next(
            i for i, d in enumerate(CHARGE_DISCHARGE_SWITCHES)
            if d.coordinator_key == "ctrDis"
        )
        switch = self._make(coordinator, index, device_info={"identifiers": set()})
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()
        assert switch._optimistic_state is False
        args = mock_api.updateDisChargeConfigInfo.await_args.args
        assert args == (SERIAL, 15, 0, "00:00", "00:00", "00:00", "00:00")

    async def test_set_value_unknown_key_noop(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        switch = self._make(coordinator)
        switch.async_write_ha_state = MagicMock()
        switch._coordinator_key = "unknown"
        await switch._set_value(1)
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()

    def test_available_and_properties(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        switch = self._make(coordinator)

        coordinator.last_update_success = False
        assert switch.available is False
        coordinator.last_update_success = True
        coordinator.cloud_available = False
        assert switch.available is False
        coordinator.cloud_available = True
        assert switch.available is True

        assert SERIAL in switch.unique_id
        assert switch.name
        assert switch.entity_category is not None
        assert switch.icon


class TestTimeSetup:
    async def test_entities_created(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await time_setup(mock_hass, entry, add_entities)
        assert len(add_entities.entities) == len(CHARGE_DISCHARGE_TIMES)

    async def test_blacklisted_model_skipped(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "VT1000"}}
        entry = _entry_for([_inverter_subentry(model="VT1000")], coordinator)

        await time_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_non_inverter_skipped(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entry = _entry_for([_ev_subentry()], coordinator)

        await time_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_serial_missing_skipped(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await time_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []


def _time_description(coordinator_key):
    return next(d for d in CHARGE_DISCHARGE_TIMES if d.coordinator_key == coordinator_key)


class TestTimeEntity:
    def _make(self, coordinator, coordinator_key="charge_timeChaf1", device_info=None):
        return AlphaTime(
            coordinator, SERIAL, FakeEntry(),
            _time_description(coordinator_key), device_info=device_info,
        )

    def test_native_value_from_coordinator(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"charge_timeChaf1": "06:30"}}
        entity = self._make(coordinator, device_info={"identifiers": set()})
        assert entity.native_value == dt_time(6, 30)

    def test_native_value_invalid_string(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"charge_timeChaf1": "garbage"}}
        entity = self._make(coordinator)
        assert entity.native_value is None

    def test_native_value_missing(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entity = self._make(coordinator)
        assert entity.native_value is None

    def test_native_value_prefers_attr(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"charge_timeChaf1": "06:30"}}
        entity = self._make(coordinator)
        entity._attr_native_value = dt_time(9, 15)
        assert entity.native_value == dt_time(9, 15)

    def test_handle_coordinator_update(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"charge_timeChaf1": "07:45"}}
        entity = self._make(coordinator)
        entity.async_write_ha_state = MagicMock()
        entity._handle_coordinator_update()
        assert entity._attr_native_value == dt_time(7, 45)

    async def test_set_value_rounds_to_quarter_hour(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                AlphaESSNames.batHighCap: 90,
                "gridCharge": 1,
                "charge_timeChae1": "05:00",
            }
        }
        entity = self._make(coordinator, "charge_timeChaf1")
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_value(dt_time(6, 7))  # rounds to 06:00
        args = mock_api.updateChargeConfigInfo.await_args.args
        # updateChargeConfigInfo(serial, cap, enabled, chae1, chae2, chaf1, chaf2)
        assert args == (SERIAL, 90, 1, "05:00", "00:00", "06:00", "00:00")
        coordinator.async_request_refresh.assert_awaited_once()

    async def test_set_value_midnight_wrap(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entity = self._make(coordinator, "charge_timeChaf1")
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_value(dt_time(23, 55))  # rounds to 24:00 -> 00:00
        args = mock_api.updateChargeConfigInfo.await_args.args
        assert args[5] == "00:00"

    async def test_set_value_discharge(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                AlphaESSNames.batUseCap: 12,
                "ctrDis": 0,
                "discharge_timeDise2": "22:00",
            }
        }
        entity = self._make(coordinator, "discharge_timeDisf2")
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_value(dt_time(20, 0))
        args = mock_api.updateDisChargeConfigInfo.await_args.args
        # updateDisChargeConfigInfo(serial, cap, enabled, dise1, dise2, disf1, disf2)
        assert args == (SERIAL, 12, 0, "00:00", "22:00", "00:00", "20:00")

    async def test_set_value_failure_reverts(self, make_coordinator, mock_api):
        """Only revert when nothing was written — both APIs have to fail."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"charge_timeChaf1": "03:00"}}
        entity = self._make(coordinator, "charge_timeChaf1")
        entity.async_write_ha_state = MagicMock()
        entity._attr_native_value = dt_time(3, 0)
        mock_api.setTimeChargeBySn.side_effect = OSError("api down")
        mock_api.updateChargeConfigInfo.side_effect = OSError("api down")

        await entity.async_set_value(dt_time(8, 0))
        assert entity._attr_native_value == dt_time(3, 0)
        coordinator.async_request_refresh.assert_not_awaited()

    async def test_set_value_keeps_value_when_only_legacy_fails(
        self, make_coordinator, mock_api
    ):
        """The periodic request went through, so the change may have landed."""
        coordinator = make_coordinator()
        # Both sides need a period, otherwise the periodic write is skipped.
        coordinator.data = {SERIAL: {
            "charge_timeChaf1": "03:00", "charge_timeChae1": "05:00",
            "discharge_timeDisf1": "17:00", "discharge_timeDise1": "21:00",
        }}
        entity = self._make(coordinator, "charge_timeChaf1")
        entity.async_write_ha_state = MagicMock()
        entity._attr_native_value = dt_time(3, 0)
        mock_api.updateChargeConfigInfo.side_effect = OSError("api down")

        # 04:00 keeps the charge window inside 03:00-05:00, clear of the
        # discharge window, so the periodic write actually goes out.
        await entity.async_set_value(dt_time(4, 0))
        assert entity._attr_native_value == dt_time(4, 0)
        coordinator.async_request_refresh.assert_awaited_once()

    def test_available_and_properties(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entity = self._make(coordinator)

        coordinator.last_update_success = False
        assert entity.available is False
        coordinator.last_update_success = True
        coordinator.cloud_available = False
        assert entity.available is False
        coordinator.cloud_available = True
        assert entity.available is True

        assert SERIAL in entity.unique_id
        assert entity.name == "Charge Start Time 1"
        assert entity.entity_category is not None
        assert entity.icon
