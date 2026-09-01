"""Tests for binary_sensor, switch and time platforms."""
from copy import deepcopy
from datetime import time as dt_time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigSubentry

from custom_components.alphaess.binary_sensor import (
    AlphaEVReadinessBinarySensor,
    AlphaScheduleControlBinarySensor,
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
    INVERTER_BINARY_SENSORS,
)
from custom_components.alphaess.switch import ATTR_LAST_CONFIRMED_STATE, AlphaSwitch
from custom_components.alphaess.switch import async_setup_entry as switch_setup
from custom_components.alphaess.time import AlphaTime
from custom_components.alphaess.time import async_setup_entry as time_setup

from .conftest import FakeEntry

SERIAL = "AL1000021000123"


def _complete_schedule_data() -> dict:
    """Return the complete entity schedule view required before staging edits."""
    return {
        "Model": "SMILE5-INV",
        AlphaESSNames.batHighCap: 90,
        "gridCharge": 1,
        "charge_timeChaf1": "01:00",
        "charge_timeChae1": "05:00",
        "charge_timeChaf2": "00:00",
        "charge_timeChae2": "00:00",
        AlphaESSNames.batUseCap: 20,
        "ctrDis": 1,
        "discharge_timeDisf1": "17:00",
        "discharge_timeDise1": "21:00",
        "discharge_timeDisf2": "00:00",
        "discharge_timeDise2": "00:00",
    }


def _seed_periodic(coordinator) -> None:
    """Give schedule controls their only live store: the periodic API."""
    coordinator._periodic_readable[SERIAL] = True
    coordinator.set_periodic_enable_intent(SERIAL, grid_charge=1, ctr_dis=1)
    coordinator._periodic_schedules[SERIAL] = coordinator._normalise_periodic_schedule({
        "executeCycleType": 0,
        "gridChargeCycle": 1,
        "ctrDisCycle": 1,
        "chargeTimeList": [
            {
                "beginTime": "01:00",
                "endTime": "05:00",
                "chargeLimit": 90,
                "chargePower": 3000,
            }
        ],
        "dischargeTimeList": [
            {
                "beginTime": "17:00",
                "endTime": "21:00",
                "chargeLimit": 15,
                "chargePower": 2500,
            }
        ],
    })


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
                "Model": "SMILE5-INV",
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.evchargermodel: "SMILE-EVCT11",
            }
        }
        entry = _entry_for([_inverter_subentry()], coordinator)

        await binary_setup(mock_hass, entry, add_entities)
        assert len(add_entities.entities) == (
            len(EV_CHARGER_BINARY_SENSORS) + len(INVERTER_BINARY_SENSORS)
        )

    async def test_no_ev_charger_creates_only_inverter_sensors(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await binary_setup(mock_hass, entry, add_entities)
        assert len(add_entities.entities) == len(INVERTER_BINARY_SENSORS)
        assert all(
            isinstance(entity, AlphaScheduleControlBinarySensor)
            for entity in add_entities.entities
        )

    async def test_blacklisted_model_gets_no_schedule_sensor(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "VT1000"}}
        entry = _entry_for([_inverter_subentry(model="VT1000")], coordinator)

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
                "Model": "SMILE5-INV",
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.evchargermodel: "SMILE-EVCT11",
            }
        }
        entry = _entry_for([_inverter_subentry(), _ev_subentry()], coordinator)

        await binary_setup(mock_hass, entry, add_entities)
        # inverter path adds only the schedule sensor; EV sensors come via
        # the EV subentry path without duplication
        assert len(add_entities.calls) == 2
        ev_entities = [
            entity
            for call_entities, _kwargs in add_entities.calls
            for entity in call_entities
            if isinstance(entity, AlphaEVReadinessBinarySensor)
        ]
        assert len(ev_entities) == len(EV_CHARGER_BINARY_SENSORS)

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
        data = _complete_schedule_data()
        data[AlphaESSNames.batHighCap] = 95
        data["gridCharge"] = 0
        coordinator.data = {SERIAL: data}
        # Staging requires the periodic API — the only schedule store.
        _seed_periodic(coordinator)
        switch = self._make(coordinator, 0)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        assert switch._optimistic_state is True
        assert coordinator.has_schedule_draft(SERIAL)
        assert coordinator.data[SERIAL]["gridCharge"] == 1
        assert coordinator._schedule_drafts[SERIAL]["charge"]["gridCharge"] == 1
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        coordinator.async_request_refresh.assert_not_awaited()

    async def test_turn_off_discharge(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        data = _complete_schedule_data()
        data[AlphaESSNames.batUseCap] = 15
        coordinator.data = {SERIAL: data}
        # Schedule control exists only through the periodic API.
        _seed_periodic(coordinator)
        # find the ctrDis switch
        index = next(
            i for i, d in enumerate(CHARGE_DISCHARGE_SWITCHES)
            if d.coordinator_key == "ctrDis"
        )
        switch = self._make(coordinator, index, device_info={"identifiers": set()})
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()

        assert switch._optimistic_state is False
        assert coordinator.has_schedule_draft(SERIAL)
        assert coordinator.data[SERIAL]["ctrDis"] == 0
        assert coordinator._schedule_drafts[SERIAL]["discharge"]["ctrDis"] == 0
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()
        coordinator.async_request_refresh.assert_not_awaited()

    async def test_staging_failure_reverts_and_propagates(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        coordinator.stage_schedule_change = MagicMock(
            side_effect=RuntimeError("schedule seed unavailable")
        )
        switch = self._make(coordinator, 0)
        switch.async_write_ha_state = MagicMock()

        with pytest.raises(RuntimeError, match="schedule seed unavailable"):
            await switch.async_turn_off()

        assert switch._optimistic_state is None
        assert switch.is_on is True
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

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
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        switch = self._make(coordinator)

        coordinator.last_update_success = False
        assert switch.available is False
        coordinator.last_update_success = True
        coordinator.cloud_available = False
        assert switch.available is False
        coordinator.cloud_available = True
        assert switch.available is True

        # A definitive 6017 read switches to the legacy backup view, which
        # the complete polled data supports; write-denied stays fail-closed.
        coordinator._periodic_readable[SERIAL] = False
        assert switch.available is True
        coordinator._periodic_readable[SERIAL] = True
        coordinator._periodic_write_denied.add(SERIAL)
        assert switch.available is False
        coordinator._periodic_write_denied.discard(SERIAL)
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
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        entity = self._make(coordinator, "charge_timeChaf1")
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_value(dt_time(6, 7))  # rounds to 06:00

        assert entity.native_value == dt_time(6, 0)
        assert coordinator.data[SERIAL]["charge_timeChaf1"] == "06:00"
        assert coordinator._schedule_drafts[SERIAL]["charge"]["timeChaf1"] == "06:00"
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        coordinator.async_request_refresh.assert_not_awaited()

    async def test_set_value_midnight_wrap(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        entity = self._make(coordinator, "charge_timeChaf1")
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_value(dt_time(23, 55))  # rounds to 24:00 -> 00:00

        assert entity.native_value == dt_time(0, 0)
        assert coordinator.data[SERIAL]["charge_timeChaf1"] == "00:00"
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_set_value_discharge(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        data = _complete_schedule_data()
        data[AlphaESSNames.batUseCap] = 12
        data["ctrDis"] = 0
        data["discharge_timeDise2"] = "22:00"
        coordinator.data = {SERIAL: data}
        # Schedule control exists only through the periodic API.
        _seed_periodic(coordinator)
        entity = self._make(coordinator, "discharge_timeDisf2")
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_value(dt_time(20, 0))

        assert entity.native_value == dt_time(20, 0)
        assert coordinator.data[SERIAL]["discharge_timeDisf2"] == "20:00"
        assert (
            coordinator._schedule_drafts[SERIAL]["discharge"]["timeDisf2"]
            == "20:00"
        )
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()

    async def test_staging_failure_reverts_and_propagates(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        data = _complete_schedule_data()
        data["charge_timeChaf1"] = "03:00"
        coordinator.data = {SERIAL: data}
        entity = self._make(coordinator, "charge_timeChaf1")
        entity.async_write_ha_state = MagicMock()
        entity._attr_native_value = dt_time(3, 0)
        coordinator.stage_schedule_change = MagicMock(
            side_effect=RuntimeError("schedule seed unavailable")
        )

        with pytest.raises(RuntimeError, match="schedule seed unavailable"):
            await entity.async_set_value(dt_time(8, 0))

        assert entity._attr_native_value == dt_time(3, 0)
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        coordinator.async_request_refresh.assert_not_awaited()

    async def test_sequential_time_edits_share_one_draft_without_writes(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        start = self._make(coordinator, "charge_timeChaf1")
        end = self._make(coordinator, "charge_timeChae1")
        start.async_write_ha_state = MagicMock()
        end.async_write_ha_state = MagicMock()

        await start.async_set_value(dt_time(3, 0))
        await end.async_set_value(dt_time(6, 0))

        assert coordinator._schedule_drafts[SERIAL]["charge"]["timeChaf1"] == "03:00"
        assert coordinator._schedule_drafts[SERIAL]["charge"]["timeChae1"] == "06:00"
        assert coordinator.data[SERIAL]["charge_timeChaf1"] == "03:00"
        assert coordinator.data[SERIAL]["charge_timeChae1"] == "06:00"
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        coordinator.async_request_refresh.assert_not_awaited()

    def test_available_and_properties(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        entity = self._make(coordinator)

        coordinator.last_update_success = False
        assert entity.available is False
        coordinator.last_update_success = True
        coordinator.cloud_available = False
        assert entity.available is False
        coordinator.cloud_available = True
        assert entity.available is True

        # A definitive 6017 read switches to the legacy backup view, which
        # the complete polled data supports; write-denied stays fail-closed.
        coordinator._periodic_readable[SERIAL] = False
        assert entity.available is True
        coordinator._periodic_readable[SERIAL] = True
        coordinator._periodic_write_denied.add(SERIAL)
        assert entity.available is False
        coordinator._periodic_write_denied.discard(SERIAL)
        assert entity.available is True

        assert SERIAL in entity.unique_id
        assert entity.name == "Charge Start Time 1"
        assert entity.entity_category is not None
        assert entity.icon


class TestRecordedScheduleFlagsBinarySensor:
    """The diagnostic reports recorded flags, never inverter working mode."""

    def _make(self, coordinator):
        return AlphaScheduleControlBinarySensor(
            coordinator, SERIAL, FakeEntry(), INVERTER_BINARY_SENSORS[0]
        )

    def test_legacy_zero_pair_reports_recorded_flags_disabled(
        self, make_coordinator
    ):
        coordinator = make_coordinator()
        data = _complete_schedule_data()
        data["gridCharge"] = 0
        data["ctrDis"] = 0
        coordinator.data = {SERIAL: data}
        coordinator._periodic_readable[SERIAL] = False
        coordinator._overlay_schedule_view(SERIAL, coordinator.data[SERIAL])

        entity = self._make(coordinator)
        assert entity.available is True
        assert entity.is_on is False

    def test_legacy_pair_is_on_when_either_recorded_flag_is_enabled(
        self, make_coordinator
    ):
        coordinator = make_coordinator()
        data = _complete_schedule_data()
        data["ctrDis"] = 0
        coordinator.data = {SERIAL: data}
        coordinator._periodic_readable[SERIAL] = False
        coordinator._overlay_schedule_view(SERIAL, coordinator.data[SERIAL])

        assert self._make(coordinator).is_on is True

    def test_periodic_value_follows_only_committed_write_intent(
        self, make_coordinator
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        _seed_periodic(coordinator)
        coordinator.set_periodic_enable_intent(SERIAL, grid_charge=0, ctr_dis=0)
        coordinator._overlay_schedule_view(SERIAL, coordinator.data[SERIAL])

        entity = self._make(coordinator)
        assert entity.is_on is False

        # A displayed draft is not yet a recorded remote write.
        coordinator.stage_schedule_change(SERIAL, charge={"gridCharge": 1})
        assert coordinator.data[SERIAL]["gridCharge"] == 1
        assert entity.is_on is False

        coordinator.set_periodic_enable_intent(SERIAL, ctr_dis=1)
        coordinator._overlay_schedule_view(SERIAL, coordinator.data[SERIAL])
        assert entity.is_on is True

    def test_unknown_until_home_assistant_has_a_complete_recorded_pair(
        self, make_coordinator
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        _seed_periodic(coordinator)
        coordinator._periodic_enable_intent.pop(SERIAL, None)
        coordinator._overlay_schedule_view(SERIAL, coordinator.data[SERIAL])

        assert self._make(coordinator).is_on is None

    def test_unavailable_without_a_schedule_store(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}

        entity = self._make(coordinator)
        assert entity.available is False
        assert entity.is_on is None

    def test_unavailable_when_the_inverter_is_missing_from_the_poll(
        self, make_coordinator
    ):
        coordinator = make_coordinator()
        coordinator.data = {}

        assert self._make(coordinator).available is False

    def test_renamed_entity_keeps_legacy_registry_identity(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        description = INVERTER_BINARY_SENSORS[0]

        entity = self._make(coordinator)

        assert description.name == "Recorded Schedule Flags Enabled"
        assert entity.unique_id == (
            f"test_entry_{SERIAL} - Time Based Control Active"
        )
        assert entity.name == description.name
        assert entity.suggested_object_id == f"{SERIAL} {description.name}"
        assert entity.entity_category == description.entity_category
        assert entity.icon == description.icon

        coordinator.last_update_success = False
        assert entity.available is False


class TestScheduleFlagsDoNotGateEditing:
    """A 0/0 pair is not a working-mode reading and must not lock controls."""

    @staticmethod
    def _seed_legacy_zero(coordinator):
        data = _complete_schedule_data()
        data["gridCharge"] = 0
        data["ctrDis"] = 0
        coordinator.data = {SERIAL: data}
        coordinator._periodic_readable[SERIAL] = False
        coordinator._overlay_schedule_view(SERIAL, coordinator.data[SERIAL])
        return coordinator

    @staticmethod
    def _seed_periodic_zero(coordinator):
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        coordinator.set_periodic_enable_intent(SERIAL, grid_charge=0, ctr_dis=0)
        coordinator._overlay_schedule_view(SERIAL, coordinator.data[SERIAL])
        return coordinator

    def test_legacy_zero_pair_remains_stageable_and_editable(self, make_coordinator):
        coordinator = self._seed_legacy_zero(make_coordinator())

        assert coordinator.can_stage_schedule(SERIAL) is True
        assert coordinator.can_modify_time_controls(SERIAL) is True
        coordinator.stage_schedule_change(
            SERIAL,
            charge={"gridCharge": 1, "timeChaf1": "02:00"},
            discharge={"batUseCap": 15},
        )
        assert coordinator.has_schedule_draft(SERIAL)
        assert coordinator.data[SERIAL]["gridCharge"] == 1
        switch_description = next(
            item
            for item in CHARGE_DISCHARGE_SWITCHES
            if item.coordinator_key == "gridCharge"
        )
        assert AlphaSwitch(
            coordinator, SERIAL, FakeEntry(), switch_description
        ).extra_state_attributes == {ATTR_LAST_CONFIRMED_STATE: "off"}

    def test_periodic_zero_pair_remains_stageable_and_editable(
        self, make_coordinator
    ):
        coordinator = self._seed_periodic_zero(make_coordinator())

        assert coordinator.can_stage_schedule(SERIAL) is True
        assert coordinator.can_modify_time_controls(SERIAL) is True
        coordinator.stage_schedule_change(
            SERIAL,
            charge={"timeChaf1": "02:00"},
            discharge={"batUseCap": 15},
        )
        assert coordinator.has_schedule_draft(SERIAL)

    @pytest.mark.parametrize(
        ("coordinator_key", "side"),
        (("gridCharge", "charge"), ("ctrDis", "discharge")),
    )
    async def test_switches_stage_without_posting_until_apply(
        self, make_coordinator, mock_api, coordinator_key, side
    ):
        coordinator = self._seed_periodic_zero(make_coordinator())
        description = next(
            item
            for item in CHARGE_DISCHARGE_SWITCHES
            if item.coordinator_key == coordinator_key
        )
        switch = AlphaSwitch(coordinator, SERIAL, FakeEntry(), description)
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        assert switch.is_on is True
        assert coordinator.has_schedule_draft(SERIAL)
        assert coordinator._schedule_drafts[SERIAL][side][coordinator_key] == 1
        assert coordinator._periodic_enable_intent[SERIAL] == {
            "gridChargeCycle": 0,
            "ctrDisCycle": 0,
        }
        mock_api.setTimeChargeBySn.assert_not_awaited()

    async def test_apply_allows_an_explicit_zero_zero_pair(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        coordinator._overlay_schedule_view(SERIAL, coordinator.data[SERIAL])
        mock_api.getTimeChargeBySn.return_value = deepcopy(
            coordinator._periodic_schedules[SERIAL]
        )

        for key in ("gridCharge", "ctrDis"):
            description = next(
                item
                for item in CHARGE_DISCHARGE_SWITCHES
                if item.coordinator_key == key
            )
            switch = AlphaSwitch(coordinator, SERIAL, FakeEntry(), description)
            switch.async_write_ha_state = MagicMock()
            await switch.async_turn_off()

        mock_api.setTimeChargeBySn.assert_not_awaited()
        await coordinator.async_apply_schedule_draft(SERIAL)

        payload = mock_api.setTimeChargeBySn.await_args.kwargs
        assert payload["gridChargeCycle"] == 0
        assert payload["ctrDisCycle"] == 0
        assert coordinator._periodic_enable_intent[SERIAL] == {
            "gridChargeCycle": 0,
            "ctrDisCycle": 0,
        }
        assert not coordinator.has_schedule_draft(SERIAL)

    def test_unreadable_schedule_leaves_controls_unavailable(
        self, make_coordinator
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        coordinator.last_update_success = True
        coordinator.cloud_available = True
        switch_description = next(
            item
            for item in CHARGE_DISCHARGE_SWITCHES
            if item.coordinator_key == "gridCharge"
        )

        assert coordinator.can_modify_time_controls(SERIAL) is False
        switch = AlphaSwitch(
            coordinator, SERIAL, FakeEntry(), switch_description
        )
        assert switch.available is False
        assert switch.extra_state_attributes == {
            ATTR_LAST_CONFIRMED_STATE: "unknown"
        }
        assert AlphaTime(
            coordinator, SERIAL, FakeEntry(), CHARGE_DISCHARGE_TIMES[0]
        ).available is False


class TestSwitchStateSurvivesRestart:
    """Persist committed flags separately from the potentially staged state."""

    def _switch(self, coordinator, coordinator_key="gridCharge"):
        description = next(
            item
            for item in CHARGE_DISCHARGE_SWITCHES
            if item.coordinator_key == coordinator_key
        )
        switch = AlphaSwitch(coordinator, SERIAL, FakeEntry(), description)
        switch.async_write_ha_state = MagicMock()
        return switch

    async def test_staged_off_display_restores_the_confirmed_on_value(
        self, make_coordinator
    ):
        before_restart = make_coordinator()
        before_restart.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(before_restart)
        before_restart._overlay_schedule_view(
            SERIAL, before_restart.data[SERIAL]
        )
        staged_switch = self._switch(before_restart)

        await staged_switch.async_turn_off()

        assert staged_switch.is_on is False
        assert staged_switch.extra_state_attributes == {
            ATTR_LAST_CONFIRMED_STATE: "on"
        }

        after_restart = make_coordinator()
        after_restart.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(after_restart)
        after_restart._periodic_enable_intent.pop(SERIAL, None)
        restored_switch = self._switch(after_restart)
        restored_switch.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(
                state="off",
                attributes=staged_switch.extra_state_attributes,
            )
        )

        await restored_switch.async_added_to_hass()

        assert after_restart._periodic_enable_intent[SERIAL] == {
            "gridChargeCycle": 1
        }

    async def test_confirmed_unknown_does_not_fall_back_to_main_state(
        self, make_coordinator
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        coordinator._periodic_enable_intent.pop(SERIAL, None)
        switch = self._switch(coordinator)
        switch.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(
                state="on",
                attributes={ATTR_LAST_CONFIRMED_STATE: "unknown"},
            )
        )

        await switch.async_added_to_hass()

        assert SERIAL not in coordinator._periodic_enable_intent

    async def test_missing_restore_state_leaves_the_question_open(
        self, make_coordinator
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        coordinator._periodic_enable_intent.pop(SERIAL, None)
        switch = self._switch(coordinator)
        switch.async_get_last_state = AsyncMock(return_value=None)

        await switch.async_added_to_hass()

        assert SERIAL not in coordinator._periodic_enable_intent

    @pytest.mark.parametrize(
        ("coordinator_key", "state", "intent_key", "expected"),
        (
            ("gridCharge", "on", "gridChargeCycle", 1),
            ("gridCharge", "off", "gridChargeCycle", 0),
            ("ctrDis", "on", "ctrDisCycle", 1),
        ),
    )
    async def test_legacy_state_without_attribute_is_a_one_time_fallback(
        self, make_coordinator, coordinator_key, state, intent_key, expected
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        coordinator._periodic_enable_intent.pop(SERIAL, None)
        switch = self._switch(coordinator, coordinator_key)
        switch.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(state=state)
        )

        await switch.async_added_to_hass()

        assert coordinator._periodic_enable_intent[SERIAL] == {
            intent_key: expected
        }

    async def test_successful_pair_is_exposed_as_confirmed_attributes(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        coordinator._overlay_schedule_view(SERIAL, coordinator.data[SERIAL])
        mock_api.getTimeChargeBySn.return_value = deepcopy(
            coordinator._periodic_schedules[SERIAL]
        )
        discharge = self._switch(coordinator, "ctrDis")

        await discharge.async_turn_off()
        await coordinator.async_apply_schedule_draft(SERIAL)

        grid = self._switch(coordinator, "gridCharge")
        assert grid.extra_state_attributes == {ATTR_LAST_CONFIRMED_STATE: "on"}
        assert discharge.extra_state_attributes == {
            ATTR_LAST_CONFIRMED_STATE: "off"
        }
        assert mock_api.setTimeChargeBySn.await_args.kwargs[
            "gridChargeCycle"
        ] == 1
        assert mock_api.setTimeChargeBySn.await_args.kwargs["ctrDisCycle"] == 0


class TestUnsetSlotDisplay:
    """AlphaESS stores an unused slot as start == end; HA shows it as unset
    rather than as a zero-length midnight window."""

    def _time_entity(self, coordinator, coordinator_key):
        description = next(
            d for d in CHARGE_DISCHARGE_TIMES
            if d.coordinator_key == coordinator_key
        )
        return AlphaTime(coordinator, SERIAL, FakeEntry(), description)

    def test_unset_slots_read_unknown_and_set_slots_read_their_time(
        self, make_coordinator
    ):
        """The user-probed state: charge 11:00-14:00 set, everything else
        stored as 00:00-00:00."""
        coordinator = make_coordinator()
        data = _complete_schedule_data()
        data["charge_timeChaf1"] = "11:00"
        data["charge_timeChae1"] = "14:00"
        data["discharge_timeDisf1"] = "00:00"
        data["discharge_timeDise1"] = "00:00"
        coordinator.data = {SERIAL: data}

        assert self._time_entity(
            coordinator, "charge_timeChaf1"
        )._value_from_coordinator() == dt_time(11, 0)
        assert self._time_entity(
            coordinator, "charge_timeChae1"
        )._value_from_coordinator() == dt_time(14, 0)
        # Slot 2 and both discharge slots are 00:00-00:00 -> unknown.
        for key in (
            "charge_timeChaf2", "charge_timeChae2",
            "discharge_timeDisf1", "discharge_timeDise1",
            "discharge_timeDisf2", "discharge_timeDise2",
        ):
            assert self._time_entity(
                coordinator, key
            )._value_from_coordinator() is None

    def test_any_equal_pair_reads_unset_not_just_midnight(
        self, make_coordinator
    ):
        coordinator = make_coordinator()
        data = _complete_schedule_data()
        data["discharge_timeDisf1"] = "14:00"
        data["discharge_timeDise1"] = "14:00"
        coordinator.data = {SERIAL: data}

        assert self._time_entity(
            coordinator, "discharge_timeDisf1"
        )._value_from_coordinator() is None

    async def test_staged_half_survives_a_matching_partner(
        self, make_coordinator, mock_api
    ):
        """Moving a window so its new start equals the old end must not blank
        the edit. An automation writes one half, reads it back to confirm the
        write, then writes the other; blanking here reads as a lost edit."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        start = self._time_entity(coordinator, "charge_timeChaf1")
        start.async_write_ha_state = MagicMock()

        await start.async_set_value(dt_time(5, 0))  # the stored end is 05:00

        assert start._value_from_coordinator() == dt_time(5, 0)
        start._handle_coordinator_update()
        assert start.native_value == dt_time(5, 0)
        # The half nobody staged still reads as unset until it is given a time.
        end = self._time_entity(coordinator, "charge_timeChae1")
        assert end._value_from_coordinator() is None
        mock_api.setTimeChargeBySn.assert_not_awaited()

    async def test_discarding_the_draft_restores_the_unset_display(
        self, make_coordinator
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        _seed_periodic(coordinator)
        start = self._time_entity(coordinator, "charge_timeChaf1")
        start.async_write_ha_state = MagicMock()
        await start.async_set_value(dt_time(5, 0))

        coordinator.discard_schedule_draft(SERIAL)

        assert coordinator.is_schedule_field_staged(SERIAL, "charge_timeChaf1") is False
        assert self._time_entity(
            coordinator, "charge_timeChaf1"
        )._value_from_coordinator() == dt_time(1, 0)

    def test_period_sensors_show_not_set(self, make_coordinator):
        coordinator = make_coordinator()
        data = _complete_schedule_data()
        data["charge_timeChaf1"] = "11:00"
        data["charge_timeChae1"] = "14:00"
        data["ctrDis"] = 0
        coordinator.data = {SERIAL: data}
        coordinator._periodic_readable[SERIAL] = False
        coordinator._overlay_schedule_view(SERIAL, coordinator.data[SERIAL])

        assert coordinator.data[SERIAL][AlphaESSNames.ChargeTime1] == (
            "11:00 - 14:00"
        )
        assert coordinator.data[SERIAL][AlphaESSNames.ChargeTime2] == "Not set"
        assert coordinator.data[SERIAL][AlphaESSNames.DischargeTime2] == (
            "Not set"
        )
