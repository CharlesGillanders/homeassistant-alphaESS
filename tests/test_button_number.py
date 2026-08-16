"""Tests for the button and number platforms."""
import time as time_mod
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigSubentry

from custom_components.alphaess.button import (
    AlphaESSBatteryButton,
    create_persistent_notification,
)
from custom_components.alphaess.button import (
    async_setup_entry as button_setup,
)
from custom_components.alphaess.const import (
    CONF_DISABLE_NOTIFICATIONS,
    CONF_INVERTER_MODEL,
    CONF_IP_ADDRESS,
    CONF_PARENT_INVERTER,
    CONF_SERIAL_NUMBER,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from custom_components.alphaess.enums import AlphaESSNames
from custom_components.alphaess.number import (
    AlphaEVNumber,
    AlphaNumber,
)
from custom_components.alphaess.number import (
    async_setup_entry as number_setup,
)
from custom_components.alphaess.sensorlist import (
    DISCHARGE_AND_CHARGE_NUMBERS,
    EV_CHARGER_NUMBERS,
    EV_DISCHARGE_AND_CHARGE_BUTTONS,
    SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS,
)

from .conftest import FakeEntry

SERIAL = "AL1000021000123"


def _inverter_subentry(serial=SERIAL, model="SMILE5-INV", disable_notifications=True):
    return ConfigSubentry(
        data={
            CONF_SERIAL_NUMBER: serial,
            CONF_INVERTER_MODEL: model,
            CONF_IP_ADDRESS: "",
            CONF_DISABLE_NOTIFICATIONS: disable_notifications,
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


def _button_description(key):
    for d in SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS + EV_DISCHARGE_AND_CHARGE_BUTTONS:
        if d.key == key:
            return d
    raise KeyError(key)


async def test_create_persistent_notification(mock_hass):
    await create_persistent_notification(mock_hass, "msg", "title")
    mock_hass.services.async_call.assert_awaited_once()


class TestButtonSetup:
    async def test_buttons_created(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await button_setup(mock_hass, entry, add_entities)
        assert len(add_entities.entities) == len(
            SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS
        )

    async def test_blacklisted_model_no_battery_buttons(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "VT1000"}}
        entry = _entry_for([_inverter_subentry(model="VT1000")], coordinator)

        await button_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_auto_discovered_ev_buttons(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                "Model": "SMILE5-INV",
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.evchargermodel: "SMILE-EVCT11",
            }
        }
        entry = _entry_for([_inverter_subentry()], coordinator)

        await button_setup(mock_hass, entry, add_entities)
        expected = len(SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS) + len(
            EV_DISCHARGE_AND_CHARGE_BUTTONS
        )
        assert len(add_entities.entities) == expected

    async def test_ev_subentry_buttons(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                "Model": "SMILE5-INV",
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.evchargermodel: "SMILE-EVCT11",
            }
        }
        entry = _entry_for([_inverter_subentry(), _ev_subentry()], coordinator)

        await button_setup(mock_hass, entry, add_entities)
        # battery buttons via inverter + EV buttons via EV subentry
        assert len(add_entities.calls) == 2

    async def test_ev_subentry_missing_parent(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {}
        entry = _entry_for([_ev_subentry(parent="GONE")], coordinator)

        await button_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_ev_subentry_no_charger_in_data(
        self, mock_hass, make_coordinator, add_entities
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        entry = _entry_for([_ev_subentry()], coordinator)

        await button_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_serial_missing_skipped(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await button_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []


class TestButtonNotificationsDisabled:
    def _make_button(self, coordinator, mock_hass, subentry=None, key=AlphaESSNames.ButtonDischargeSixty):
        entry = _entry_for([subentry] if subentry else [], coordinator)
        button = AlphaESSBatteryButton(
            coordinator, entry, SERIAL, _button_description(key), subentry=subentry,
        )
        button.hass = mock_hass
        return button, entry

    def test_no_subentry_disables(self, make_coordinator, mock_hass):
        coordinator = make_coordinator()
        button, _ = self._make_button(coordinator, mock_hass, subentry=None)
        assert button._notifications_disabled is True

    def test_live_subentry_value(self, make_coordinator, mock_hass):
        coordinator = make_coordinator()
        subentry = _inverter_subentry(disable_notifications=False)
        button, entry = self._make_button(coordinator, mock_hass, subentry=subentry)
        mock_hass.config_entries.async_get_entry.return_value = entry
        assert button._notifications_disabled is False

    def test_live_subentry_missing(self, make_coordinator, mock_hass):
        coordinator = make_coordinator()
        subentry = _inverter_subentry()
        button, entry = self._make_button(coordinator, mock_hass, subentry=subentry)
        live_entry = FakeEntry(subentries={})
        mock_hass.config_entries.async_get_entry.return_value = live_entry
        assert button._notifications_disabled is True

    def test_entry_missing(self, make_coordinator, mock_hass):
        coordinator = make_coordinator()
        subentry = _inverter_subentry()
        button, _ = self._make_button(coordinator, mock_hass, subentry=subentry)
        mock_hass.config_entries.async_get_entry.return_value = None
        assert button._notifications_disabled is True


class TestButtonPress:
    def _make_ev_button(self, coordinator, mock_hass, key, notifications_off=True):
        subentry = _inverter_subentry(disable_notifications=notifications_off)
        entry = _entry_for([subentry], coordinator)
        button = AlphaESSBatteryButton(
            coordinator, entry, SERIAL, _button_description(key),
            ev_charger=True, ev_serial="EV123", subentry=subentry,
        )
        button.hass = mock_hass
        mock_hass.config_entries.async_get_entry.return_value = entry
        return button

    def _make_battery_button(self, coordinator, mock_hass, key, notifications_off=True):
        subentry = _inverter_subentry(disable_notifications=notifications_off)
        entry = _entry_for([subentry], coordinator)
        button = AlphaESSBatteryButton(
            coordinator, entry, SERIAL, _button_description(key), subentry=subentry,
        )
        button.hass = mock_hass
        mock_hass.config_entries.async_get_entry.return_value = entry
        return button

    async def test_stop_charging_sent_even_when_state_disagrees(
        self, make_coordinator, mock_hass, mock_api
    ):
        """Status 2 says "not charging", but it may just be stale. Ask anyway."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 2}}
        button = self._make_ev_button(
            coordinator, mock_hass, AlphaESSNames.stopcharging, notifications_off=False
        )

        await button.async_press()
        mock_api.remoteControlEvCharger.assert_awaited_once_with(SERIAL, "EV123", 0)
        mock_hass.services.async_call.assert_awaited()

    async def test_stop_charging_success(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 3}}
        button = self._make_ev_button(
            coordinator, mock_hass, AlphaESSNames.stopcharging, notifications_off=False
        )

        await button.async_press()
        mock_api.remoteControlEvCharger.assert_awaited_once_with(SERIAL, "EV123", 0)
        mock_hass.services.async_call.assert_awaited()

    async def test_start_charging_sent_with_notifications_off(
        self, make_coordinator, mock_hass, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 3}}
        button = self._make_ev_button(coordinator, mock_hass, AlphaESSNames.startcharging)

        await button.async_press()
        mock_api.remoteControlEvCharger.assert_awaited_once_with(SERIAL, "EV123", 1)
        mock_hass.services.async_call.assert_not_awaited()

    async def test_rejected_ev_command_is_reported(
        self, make_coordinator, mock_hass, mock_api
    ):
        from alphaess.alphaess import AlphaESSApiError

        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 2}}
        mock_api.remoteControlEvCharger.side_effect = AlphaESSApiError(
            code=6008, description="Set failed")
        button = self._make_ev_button(
            coordinator, mock_hass, AlphaESSNames.startcharging, notifications_off=False
        )

        await button.async_press()

        message = mock_hass.services.async_call.await_args.args[2]["message"]
        assert "rejected" in message

    async def test_start_charging_success(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 2}}
        button = self._make_ev_button(coordinator, mock_hass, AlphaESSNames.startcharging)

        await button.async_press()
        mock_api.remoteControlEvCharger.assert_awaited_once_with(SERIAL, "EV123", 1)

    async def test_start_charging_success_with_notification(
        self, make_coordinator, mock_hass, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 2}}
        button = self._make_ev_button(
            coordinator, mock_hass, AlphaESSNames.startcharging, notifications_off=False
        )

        await button.async_press()
        mock_api.remoteControlEvCharger.assert_awaited_once_with(SERIAL, "EV123", 1)
        mock_hass.services.async_call.assert_awaited()

    async def test_discharge_button(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        coordinator.set_number_setting(SERIAL, "batUseCap", 10)
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonDischargeSixty,
            notifications_off=False,
        )

        await button.async_press()
        mock_api.updateDisChargeConfigInfo.assert_awaited_once()
        assert SERIAL in coordinator.last_discharge_update

    async def test_discharge_rate_limited(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        coordinator.last_discharge_update[SERIAL] = time_mod.monotonic()
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonDischargeSixty,
            notifications_off=False,
        )

        await button.async_press()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()
        mock_hass.services.async_call.assert_awaited()  # wait message

    async def test_charge_button(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        coordinator.set_number_setting(SERIAL, "batHighCap", 90)
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonChargeFifteen
        )

        await button.async_press()
        mock_api.updateChargeConfigInfo.assert_awaited_once()

    async def test_reset_button(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonRechargeConfig,
            notifications_off=False,
        )

        await button.async_press()
        mock_api.updateChargeConfigInfo.assert_awaited_once()
        mock_api.updateDisChargeConfigInfo.assert_awaited_once()
        assert SERIAL in coordinator.last_charge_update

    async def test_reset_button_throttled(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        coordinator.last_charge_update[SERIAL] = time_mod.monotonic()
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonRechargeConfig,
            notifications_off=False,
        )

        await button.async_press()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        mock_hass.services.async_call.assert_awaited()

    async def test_reset_button_throttled_silent(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        coordinator.last_discharge_update[SERIAL] = time_mod.monotonic()
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonRechargeConfig
        )

        await button.async_press()
        mock_hass.services.async_call.assert_not_awaited()

    def test_button_properties(self, make_coordinator, mock_hass):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonDischargeSixty
        )

        coordinator.last_update_success = False
        assert button.available is False
        coordinator.last_update_success = True
        coordinator.cloud_available = False
        assert button.available is False
        coordinator.cloud_available = True
        assert button.available is True

        assert SERIAL in button.unique_id
        assert button.device_class is None
        assert button.entity_category is not None
        assert button.name == "60 Minute Discharge"
        assert button.suggested_object_id == f"{SERIAL} 60 Minute Discharge"
        assert button.icon

    def test_button_with_device_info(self, make_coordinator, mock_hass):
        coordinator = make_coordinator()
        info = {"identifiers": {("alphaess", SERIAL)}}
        button = AlphaESSBatteryButton(
            coordinator, FakeEntry(), SERIAL,
            _button_description(AlphaESSNames.ButtonDischargeSixty),
            device_info=info,
        )
        assert button._attr_device_info == info


class TestNumberSetup:
    async def test_numbers_created(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await number_setup(mock_hass, entry, add_entities)
        assert len(add_entities.entities) == len(DISCHARGE_AND_CHARGE_NUMBERS)

    async def test_blacklisted_model(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "VT1000"}}
        entry = _entry_for([_inverter_subentry(model="VT1000")], coordinator)

        await number_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_auto_discovered_ev_numbers(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                "Model": "SMILE5-INV",
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.evchargermodel: "SMILE-EVCT11",
            }
        }
        entry = _entry_for([_inverter_subentry()], coordinator)

        await number_setup(mock_hass, entry, add_entities)
        expected = len(DISCHARGE_AND_CHARGE_NUMBERS) + len(EV_CHARGER_NUMBERS)
        assert len(add_entities.entities) == expected

    async def test_ev_subentry_numbers(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                "Model": "SMILE5-INV",
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.evchargermodel: "SMILE-EVCT11",
            }
        }
        entry = _entry_for([_inverter_subentry(), _ev_subentry()], coordinator)

        await number_setup(mock_hass, entry, add_entities)
        assert len(add_entities.calls) == 2

    async def test_ev_subentry_missing_parent(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {}
        entry = _entry_for([_ev_subentry(parent="GONE")], coordinator)

        await number_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_ev_subentry_no_charger(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        entry = _entry_for([_ev_subentry()], coordinator)

        await number_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []

    async def test_serial_missing(self, mock_hass, make_coordinator, add_entities):
        coordinator = make_coordinator()
        coordinator.data = {}
        entry = _entry_for([_inverter_subentry()], coordinator)

        await number_setup(mock_hass, entry, add_entities)
        assert add_entities.entities == []


def _number_description(key):
    return next(d for d in DISCHARGE_AND_CHARGE_NUMBERS if d.key == key)


class TestAlphaNumber:
    def _make(self, coordinator, key=AlphaESSNames.batUseCap, device_info=None):
        return AlphaNumber(
            coordinator, SERIAL, FakeEntry(), _number_description(key),
            device_info=device_info,
        )

    def test_initial_defaults(self, make_coordinator):
        coordinator = make_coordinator()
        high = self._make(coordinator, AlphaESSNames.batHighCap,
                          device_info={"identifiers": set()})
        low = self._make(coordinator, AlphaESSNames.batUseCap)
        assert high._def_initial_value == 90.0
        assert low._def_initial_value == 10.0

    async def test_added_to_hass_restores_state(self, make_coordinator, monkeypatch):
        from homeassistant.helpers.update_coordinator import CoordinatorEntity

        coordinator = make_coordinator()
        entity = self._make(coordinator)
        monkeypatch.setattr(
            CoordinatorEntity, "async_added_to_hass", AsyncMock()
        )
        restored = MagicMock()
        restored.native_value = 33.0
        entity.async_get_last_number_data = AsyncMock(return_value=restored)

        await entity.async_added_to_hass()
        assert entity._attr_native_value == 33.0
        assert coordinator.get_number_setting(SERIAL, "batUseCap") == 33.0

    async def test_added_to_hass_no_saved_state(self, make_coordinator, monkeypatch):
        from homeassistant.helpers.update_coordinator import CoordinatorEntity

        coordinator = make_coordinator()
        entity = self._make(coordinator)
        monkeypatch.setattr(
            CoordinatorEntity, "async_added_to_hass", AsyncMock()
        )
        entity.async_get_last_number_data = AsyncMock(return_value=None)
        entity.async_write_ha_state = MagicMock()

        await entity.async_added_to_hass()
        assert entity._attr_native_value == 10.0
        assert coordinator.get_number_setting(SERIAL, "batUseCap") == 10.0

    async def test_set_value_discharge_pushes_api(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"ctrDis": 0, "discharge_timeDise1": "23:00"}}
        entity = self._make(coordinator, AlphaESSNames.batUseCap)
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_native_value(22)
        args = mock_api.updateDisChargeConfigInfo.await_args.args
        assert args == (SERIAL, 22, 0, "23:00", "00:00", "00:00", "00:00")
        assert coordinator.get_number_setting(SERIAL, "batUseCap") == 22
        coordinator.async_request_refresh.assert_awaited_once()

    async def test_set_value_charge_pushes_api(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"gridCharge": 1, "charge_timeChaf1": "01:00"}}
        entity = self._make(coordinator, AlphaESSNames.batHighCap)
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_native_value(88)
        args = mock_api.updateChargeConfigInfo.await_args.args
        assert args == (SERIAL, 88, 1, "00:00", "00:00", "01:00", "00:00")

    def test_properties(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entity = self._make(coordinator)
        entity._attr_native_value = 10.0

        coordinator.last_update_success = False
        assert entity.available is False
        coordinator.last_update_success = True
        coordinator.cloud_available = True
        assert entity.available is True

        assert entity.native_value == 10.0
        assert entity.name == "batUseCap"
        assert entity.suggested_object_id == f"{SERIAL} batUseCap"
        assert entity.mode == "box"
        assert entity.native_unit_of_measurement == "%"
        assert SERIAL in entity.unique_id
        assert entity.entity_category is not None
        assert entity.icon


class TestAlphaEVNumber:
    def _make(self, coordinator, device_info=None):
        return AlphaEVNumber(
            coordinator, SERIAL, FakeEntry(), EV_CHARGER_NUMBERS[0],
            ev_serial="EV123", device_info=device_info,
        )

    def test_native_value(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evcurrentsetting: "16"}}
        entity = self._make(coordinator, device_info={"identifiers": set()})
        assert entity.native_value == 16.0

        coordinator.data = {SERIAL: {}}
        assert entity.native_value is None

    async def test_set_value(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entity = self._make(coordinator)

        await entity.async_set_native_value(10.0)
        mock_api.setEvChargerCurrentsBySn.assert_awaited_once_with(SERIAL, 10)

    def test_properties(self, make_coordinator):
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

        assert entity.name
        assert entity.suggested_object_id.startswith(SERIAL)
        assert entity.native_unit_of_measurement == "A"
        assert SERIAL in entity.unique_id
        assert entity.entity_category is not None
        assert entity.icon
