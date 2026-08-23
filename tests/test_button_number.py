"""Tests for the button and number platforms."""
import time as time_mod
from datetime import time as dt_time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.exceptions import HomeAssistantError

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
from custom_components.alphaess.coordinator import (
    SchedulePartialWriteError,
    ScheduleWriteUnknownError,
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
    CHARGE_DISCHARGE_SWITCHES,
    CHARGE_DISCHARGE_TIMES,
    DISCHARGE_AND_CHARGE_NUMBERS,
    EV_CHARGER_NUMBERS,
    EV_DISCHARGE_AND_CHARGE_BUTTONS,
    SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS,
)
from custom_components.alphaess.switch import AlphaSwitch
from custom_components.alphaess.time import AlphaTime

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
        "charge_timeChaf2": "06:00",
        "charge_timeChae2": "07:00",
        AlphaESSNames.batUseCap: 20,
        "ctrDis": 1,
        "discharge_timeDisf1": "17:00",
        "discharge_timeDise1": "21:00",
        "discharge_timeDisf2": "22:00",
        "discharge_timeDise2": "23:00",
    }


def _seed_periodic_unreadable(coordinator) -> None:
    """Seed entity data for a system whose periodic read answered 6017."""
    coordinator.data = {SERIAL: _complete_schedule_data()}
    coordinator._periodic_readable[SERIAL] = False


def _periodic_schedule() -> dict:
    return {
        "executeCycleType": 0,
        "gridChargeCycle": 1,
        "ctrDisCycle": 1,
        "chargeTimeList": [
            {
                "beginTime": "01:00",
                "endTime": "05:00",
                "chargeLimit": 90,
                "chargePower": 3000,
            },
            {
                "beginTime": "06:00",
                "endTime": "07:00",
                "chargeLimit": 90,
                "chargePower": 2500,
            },
        ],
        "dischargeTimeList": [
            {
                "beginTime": "17:00",
                "endTime": "21:00",
                "chargeLimit": 20,
                "chargePower": 2800,
            },
            {
                "beginTime": "22:00",
                "endTime": "23:00",
                "chargeLimit": 20,
                "chargePower": 2200,
            },
        ],
    }


def _seed_periodic_schedule(coordinator) -> None:
    coordinator.data = {SERIAL: _complete_schedule_data()}
    coordinator._periodic_readable[SERIAL] = True
    coordinator._periodic_schedules[SERIAL] = (
        coordinator._normalise_periodic_schedule(_periodic_schedule())
    )
    coordinator.set_periodic_enable_intent(SERIAL, grid_charge=1, ctr_dis=1)
    coordinator._publish_schedule_view(SERIAL)


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
        keys = {entity._key for entity in add_entities.entities}
        assert AlphaESSNames.ButtonApplySchedule in keys
        assert AlphaESSNames.ButtonDiscardSchedule in keys

    def test_reset_button_exists_for_backup_mode(self, make_coordinator, mock_hass):
        """The Reset button exists again for the legacy backup stores, and
        reports unavailable on periodic-governed systems."""
        keys = {d.key for d in SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS}
        assert AlphaESSNames.ButtonRechargeConfig in keys

        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        button = AlphaESSBatteryButton(
            coordinator, FakeEntry(), SERIAL,
            _button_description(AlphaESSNames.ButtonRechargeConfig),
        )
        button.hass = mock_hass
        assert button.available is False

        coordinator._periodic_readable[SERIAL] = False
        coordinator._periodic_schedules.pop(SERIAL, None)
        assert button.available is True

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
        mock_api.remoteControlEvCharger.assert_awaited_once_with(
            sysSn=SERIAL, evchargerSn="EV123", controlMode=0
        )
        mock_hass.services.async_call.assert_awaited()

    async def test_stop_charging_success(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 3}}
        button = self._make_ev_button(
            coordinator, mock_hass, AlphaESSNames.stopcharging, notifications_off=False
        )

        await button.async_press()
        mock_api.remoteControlEvCharger.assert_awaited_once_with(
            sysSn=SERIAL, evchargerSn="EV123", controlMode=0
        )
        mock_hass.services.async_call.assert_awaited()

    async def test_start_charging_sent_with_notifications_off(
        self, make_coordinator, mock_hass, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 3}}
        button = self._make_ev_button(coordinator, mock_hass, AlphaESSNames.startcharging)

        await button.async_press()
        mock_api.remoteControlEvCharger.assert_awaited_once_with(
            sysSn=SERIAL, evchargerSn="EV123", controlMode=1
        )
        mock_hass.services.async_call.assert_not_awaited()

    async def test_rejected_ev_command_is_reported_and_propagated(
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

        # The raw library exception would surface in the frontend as an
        # anonymous "Unknown error"; the button must translate it and keep
        # the API's own explanation.
        with pytest.raises(HomeAssistantError, match="6008"):
            await button.async_press()

        mock_api.remoteControlEvCharger.assert_awaited_once_with(
            sysSn=SERIAL, evchargerSn="EV123", controlMode=1
        )
        message = mock_hass.services.async_call.await_args.args[2]["message"]
        assert "rejected" in message

    async def test_start_charging_success(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 2}}
        button = self._make_ev_button(coordinator, mock_hass, AlphaESSNames.startcharging)

        await button.async_press()
        mock_api.remoteControlEvCharger.assert_awaited_once_with(
            sysSn=SERIAL, evchargerSn="EV123", controlMode=1
        )

    async def test_start_charging_success_with_notification(
        self, make_coordinator, mock_hass, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 2}}
        button = self._make_ev_button(
            coordinator, mock_hass, AlphaESSNames.startcharging, notifications_off=False
        )

        await button.async_press()
        mock_api.remoteControlEvCharger.assert_awaited_once_with(
            sysSn=SERIAL, evchargerSn="EV123", controlMode=1
        )
        mock_hass.services.async_call.assert_awaited()

    async def test_discharge_button_writes_periodic_store(
        self, make_coordinator, mock_hass, mock_api
    ):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        mock_api.getTimeChargeBySn.return_value = _periodic_schedule()
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonDischargeSixty,
            notifications_off=False,
        )

        await button.async_press()
        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()
        mock_api.getDisChargeConfigInfo.assert_not_awaited()
        assert SERIAL in coordinator.last_discharge_update

    async def test_discharge_button_uses_legacy_backup_on_6017(
        self, make_coordinator, mock_hass, mock_api
    ):
        """A definitive 6017 selects the legacy backup: the press is
        available and writes the two-slot discharge store."""
        coordinator = make_coordinator()
        _seed_periodic_unreadable(coordinator)
        mock_api.getDisChargeConfigInfo.return_value = {
            "batUseCap": 20, "ctrDis": 1,
            "timeDisf1": "17:00", "timeDise1": "21:00",
            "timeDisf2": "22:00", "timeDise2": "23:00",
        }
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonDischargeSixty,
            notifications_off=False,
        )

        assert button.available is True
        await button.async_press()

        kwargs = mock_api.updateDisChargeConfigInfo.await_args.kwargs
        assert kwargs["ctrDis"] == 1
        assert kwargs["timeDisf2"] == "22:00"  # slot 2 preserved
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.getTimeChargeBySn.assert_not_awaited()
        assert SERIAL in coordinator.last_discharge_update

    async def test_charge_button_uses_legacy_backup_on_6017(
        self, make_coordinator, mock_hass, mock_api
    ):
        """Charge presses use the same backup on 6017 systems."""
        coordinator = make_coordinator()
        _seed_periodic_unreadable(coordinator)
        mock_api.getChargeConfigInfo.return_value = {
            "batHighCap": 90, "gridCharge": 0,
            "timeChaf1": "00:00", "timeChae1": "00:00",
            "timeChaf2": "06:00", "timeChae2": "07:00",
        }
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonChargeFifteen,
            notifications_off=False,
        )

        assert button.available is True
        await button.async_press()

        kwargs = mock_api.updateChargeConfigInfo.await_args.kwargs
        assert kwargs["gridCharge"] == 1
        assert kwargs["timeChaf2"] == "06:00"  # slot 2 preserved
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.getTimeChargeBySn.assert_not_awaited()
        assert SERIAL in coordinator.last_charge_update

    async def test_an_already_translated_ev_error_is_not_rewrapped(
        self, make_coordinator, mock_hass
    ):
        """control_ev can raise HomeAssistantError itself; wrapping it again
        would bury the message it already chose."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 2}}
        coordinator.control_ev = AsyncMock(
            side_effect=HomeAssistantError("charger control is unavailable")
        )
        button = self._make_ev_button(
            coordinator, mock_hass, AlphaESSNames.startcharging, notifications_off=False
        )

        with pytest.raises(HomeAssistantError, match="charger control is unavailable"):
            await button.async_press()
        mock_hass.services.async_call.assert_awaited()

    async def test_reset_clears_both_legacy_stores(
        self, make_coordinator, mock_hass
    ):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        coordinator.reset_config = AsyncMock()
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonRechargeConfig,
            notifications_off=False,
        )

        await button.async_press()

        coordinator.reset_config.assert_awaited_once_with(SERIAL)
        # Both sides share the reset cooldown, so neither duration button can
        # follow it straight away.
        assert SERIAL in coordinator.last_charge_update
        assert SERIAL in coordinator.last_discharge_update
        assert "reset" in mock_hass.services.async_call.await_args.args[2]["message"].lower()

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (ScheduleWriteUnknownError("response lost"), "unknown outcome"),
            (SchedulePartialWriteError("charge applied"), "partly applied"),
        ],
    )
    async def test_reset_reports_an_uncertain_outcome(
        self, make_coordinator, mock_hass, error, expected
    ):
        """Neither outcome is proof nothing changed, so the cooldown stands and
        the press still fails visibly."""
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        coordinator.reset_config = AsyncMock(side_effect=error)
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonRechargeConfig,
            notifications_off=False,
        )

        with pytest.raises(type(error)):
            await button.async_press()

        assert SERIAL in coordinator.last_charge_update
        assert SERIAL in coordinator.last_discharge_update
        message = mock_hass.services.async_call.await_args.args[2]["message"]
        assert expected in message

    async def test_a_refused_reset_gives_the_cooldown_back(
        self, make_coordinator, mock_hass
    ):
        """A reset that was refused outright changed nothing, so it must not
        lock the button out for the next thirty seconds."""
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        coordinator.reset_config = AsyncMock(side_effect=HomeAssistantError("refused"))
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonRechargeConfig,
            notifications_off=False,
        )

        with pytest.raises(HomeAssistantError, match="refused"):
            await button.async_press()

        assert coordinator.last_charge_update.get(SERIAL) is None
        assert coordinator.last_discharge_update.get(SERIAL) is None
        assert "could not fully reset" in (
            mock_hass.services.async_call.await_args.args[2]["message"]
        )

    async def test_reset_is_rate_limited(self, make_coordinator, mock_hass):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        coordinator.reset_config = AsyncMock()
        coordinator.last_charge_update[SERIAL] = time_mod.monotonic()
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonRechargeConfig,
            notifications_off=False,
        )

        with pytest.raises(HomeAssistantError, match="rate limited"):
            await button.async_press()

        coordinator.reset_config.assert_not_awaited()
        assert "Please wait" in mock_hass.services.async_call.await_args.args[2]["message"]

    async def test_discharge_rate_limited(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        coordinator.last_discharge_update[SERIAL] = time_mod.monotonic()
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonDischargeSixty,
            notifications_off=False,
        )

        # A silently absorbed press reads as success; it must fail visibly.
        with pytest.raises(HomeAssistantError, match="rate limited"):
            await button.async_press()
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()
        mock_hass.services.async_call.assert_awaited()  # wait message

    async def test_charge_rate_limited_silent(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        coordinator.last_charge_update[SERIAL] = time_mod.monotonic()
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonChargeFifteen
        )

        # Even with notifications disabled the press must fail visibly
        # rather than complete as a silent no-op.
        with pytest.raises(HomeAssistantError, match="rate limited"):
            await button.async_press()
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_hass.services.async_call.assert_not_awaited()

    async def test_charge_button(self, make_coordinator, mock_hass, mock_api):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        mock_api.getTimeChargeBySn.return_value = _periodic_schedule()
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonChargeFifteen
        )

        await button.async_press()
        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        mock_api.getChargeConfigInfo.assert_not_awaited()
        assert SERIAL in coordinator.last_charge_update

    async def test_apply_button_notifies_on_unknown_outcome(
        self, make_coordinator, mock_hass
    ):
        """A lost Apply response may already be live; that must be announced."""
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})
        coordinator.async_apply_schedule_draft = AsyncMock(
            side_effect=ScheduleWriteUnknownError("response lost")
        )
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonApplySchedule,
            notifications_off=False,
        )

        with pytest.raises(ScheduleWriteUnknownError):
            await button.async_press()

        notification = mock_hass.services.async_call.await_args.args[2]
        assert "unknown outcome" in notification["message"]
        assert "Check the AlphaESS app" in notification["message"]
        assert "Apply outcome uncertain" in notification["title"]

    async def test_apply_button_notifies_on_failure(
        self, make_coordinator, mock_hass
    ):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})
        coordinator.async_apply_schedule_draft = AsyncMock(
            side_effect=RuntimeError("write rejected")
        )
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonApplySchedule,
            notifications_off=False,
        )

        with pytest.raises(RuntimeError, match="write rejected"):
            await button.async_press()

        notification = mock_hass.services.async_call.await_args.args[2]
        assert "could not apply" in notification["message"]
        assert "Apply failed" in notification["title"]

    def test_apply_and_discard_available_only_with_a_draft(
        self, make_coordinator, mock_hass
    ):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        apply_button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonApplySchedule
        )
        discard_button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonDiscardSchedule
        )

        assert apply_button.available is False
        assert discard_button.available is False

        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})
        assert apply_button.available is True
        assert discard_button.available is True

        coordinator.cloud_available = False
        assert apply_button.available is False
        assert discard_button.available is False
        coordinator.cloud_available = True
        coordinator.last_update_success = False
        assert apply_button.available is False
        assert discard_button.available is False

    async def test_apply_button_delegates_and_propagates_failures(
        self, make_coordinator, mock_hass
    ):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})
        coordinator.async_apply_schedule_draft = AsyncMock(
            side_effect=RuntimeError("write rejected")
        )
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonApplySchedule
        )

        with pytest.raises(RuntimeError, match="write rejected"):
            await button.async_press()

        coordinator.async_apply_schedule_draft.assert_awaited_once_with(SERIAL)
        assert coordinator.has_schedule_draft(SERIAL)

    async def test_apply_button_delegates_successfully(
        self, make_coordinator, mock_hass
    ):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})
        coordinator.async_apply_schedule_draft = AsyncMock(
            side_effect=lambda serial: coordinator.discard_schedule_draft(serial)
        )
        button = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonApplySchedule
        )

        await button.async_press()

        coordinator.async_apply_schedule_draft.assert_awaited_once_with(SERIAL)
        mock_hass.services.async_call.assert_not_awaited()

    async def test_apply_and_discard_buttons_notify_when_enabled(
        self, make_coordinator, mock_hass
    ):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})
        coordinator.async_apply_schedule_draft = AsyncMock(
            side_effect=lambda serial: coordinator.discard_schedule_draft(serial)
        )
        apply_button = self._make_battery_button(
            coordinator,
            mock_hass,
            AlphaESSNames.ButtonApplySchedule,
            notifications_off=False,
        )

        await apply_button.async_press()
        assert "Schedule Accepted" in mock_hass.services.async_call.await_args.args[2][
            "title"
        ]

        mock_hass.services.async_call.reset_mock()
        discard_button = self._make_battery_button(
            coordinator,
            mock_hass,
            AlphaESSNames.ButtonDiscardSchedule,
            notifications_off=False,
        )
        await discard_button.async_press()
        assert "Schedule Restored" in mock_hass.services.async_call.await_args.args[2][
            "title"
        ]

    async def test_discard_restores_all_schedule_entity_views(
        self, make_coordinator, mock_hass, mock_api
    ):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)

        charge_start = AlphaTime(
            coordinator,
            SERIAL,
            FakeEntry(),
            next(
                description
                for description in CHARGE_DISCHARGE_TIMES
                if description.coordinator_key == "charge_timeChaf1"
            ),
        )
        grid_charge = AlphaSwitch(
            coordinator,
            SERIAL,
            FakeEntry(),
            next(
                description
                for description in CHARGE_DISCHARGE_SWITCHES
                if description.coordinator_key == "gridCharge"
            ),
        )
        charge_soc = AlphaNumber(
            coordinator,
            SERIAL,
            FakeEntry(),
            _number_description(AlphaESSNames.batHighCap),
        )
        charge_power = AlphaNumber(
            coordinator,
            SERIAL,
            FakeEntry(),
            _number_description(AlphaESSNames.ChargePower1),
        )
        entities = (charge_start, grid_charge, charge_soc, charge_power)
        for entity in entities:
            entity.async_write_ha_state = MagicMock()
            entity._handle_coordinator_update()

        await charge_start.async_set_value(dt_time(2, 0))
        await grid_charge.async_turn_off()
        await charge_soc.async_set_native_value(85)
        await charge_power.async_set_native_value(1500)

        assert charge_start.native_value == dt_time(2, 0)
        assert grid_charge.is_on is False
        assert charge_soc.native_value == 85
        assert charge_power.native_value == 1500
        assert coordinator.has_schedule_draft(SERIAL)

        discard = self._make_battery_button(
            coordinator, mock_hass, AlphaESSNames.ButtonDiscardSchedule
        )
        await discard.async_press()
        for entity in entities:
            entity._handle_coordinator_update()

        assert coordinator.has_schedule_draft(SERIAL) is False
        assert discard.available is False
        assert charge_start.native_value == dt_time(1, 0)
        assert grid_charge.is_on is True
        assert charge_soc.native_value == 90
        assert charge_power.native_value == 3000
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()

    def test_button_properties(self, make_coordinator, mock_hass):
        coordinator = make_coordinator()
        # Discharge buttons need the periodic API to be pressable at all.
        _seed_periodic_schedule(coordinator)
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
        power_keys = {
            AlphaESSNames.ChargePower1,
            AlphaESSNames.ChargePower2,
            AlphaESSNames.DischargePower1,
            AlphaESSNames.DischargePower2,
        }
        assert {entity.key for entity in add_entities.entities} >= power_keys

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
        for key in (
            AlphaESSNames.ChargePower1,
            AlphaESSNames.ChargePower2,
            AlphaESSNames.DischargePower1,
            AlphaESSNames.DischargePower2,
        ):
            assert self._make(coordinator, key)._def_initial_value is None

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
        entity.async_write_ha_state = MagicMock()

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

    async def test_added_to_hass_prefers_current_coordinator_value(
        self, make_coordinator, monkeypatch
    ):
        from homeassistant.helpers.update_coordinator import CoordinatorEntity

        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.batUseCap: 27}}
        entity = self._make(coordinator)
        monkeypatch.setattr(CoordinatorEntity, "async_added_to_hass", AsyncMock())
        entity.async_get_last_number_data = AsyncMock()

        await entity.async_added_to_hass()

        assert entity.native_value == 27
        assert coordinator.get_number_setting(SERIAL, "batUseCap") == 27
        entity.async_get_last_number_data.assert_not_awaited()

    async def test_added_to_hass_does_not_restore_unknown_period_power(
        self, make_coordinator, monkeypatch
    ):
        from homeassistant.helpers.update_coordinator import CoordinatorEntity

        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.ChargePower1: None}}
        entity = self._make(coordinator, AlphaESSNames.ChargePower1)
        monkeypatch.setattr(CoordinatorEntity, "async_added_to_hass", AsyncMock())
        entity.async_get_last_number_data = AsyncMock()

        await entity.async_added_to_hass()

        assert entity.native_value is None
        entity.async_get_last_number_data.assert_not_awaited()

    async def test_set_value_discharge_stages_without_writing(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        entity = self._make(coordinator, AlphaESSNames.batUseCap)
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_native_value(22)

        assert entity.native_value == 22
        assert coordinator.data[SERIAL][AlphaESSNames.batUseCap] == 22
        assert coordinator._schedule_drafts[SERIAL]["discharge"]["batUseCap"] == 22
        assert coordinator.get_number_setting(SERIAL, "batUseCap") == 22
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()
        coordinator.async_request_refresh.assert_not_awaited()

    async def test_set_value_discharge_stages_on_the_backup_view(
        self, make_coordinator, mock_api
    ):
        """On a 6017 system the discharge cutoff stages against the legacy
        backup view without any write."""
        coordinator = make_coordinator()
        _seed_periodic_unreadable(coordinator)
        entity = self._make(coordinator, AlphaESSNames.batUseCap)
        entity.async_write_ha_state = MagicMock()

        assert entity.available is True
        await entity.async_set_native_value(22)

        assert coordinator._schedule_drafts[SERIAL]["discharge"]["batUseCap"] == 22
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()
        mock_api.getDisChargeConfigInfo.assert_not_awaited()

    async def test_power_number_unavailable_in_backup_mode(
        self, make_coordinator, mock_api
    ):
        """Per-period power exists only in the periodic store; in backup
        mode the power entities stay unavailable."""
        coordinator = make_coordinator()
        _seed_periodic_unreadable(coordinator)
        entity = self._make(coordinator, AlphaESSNames.DischargePower1)
        assert entity.available is False

    async def test_set_value_charge_stages_without_writing(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        entity = self._make(coordinator, AlphaESSNames.batHighCap)
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_native_value(88)

        assert entity.native_value == 88
        assert coordinator.data[SERIAL][AlphaESSNames.batHighCap] == 88
        assert coordinator._schedule_drafts[SERIAL]["charge"]["batHighCap"] == 88
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.parametrize(
        ("key", "side", "field", "initial_value"),
        [
            (AlphaESSNames.ChargePower1, "charge", "chargePower1", 3000),
            (AlphaESSNames.ChargePower2, "charge", "chargePower2", 2500),
            (AlphaESSNames.DischargePower1, "discharge", "chargePower1", 2800),
            (AlphaESSNames.DischargePower2, "discharge", "chargePower2", 2200),
        ],
    )
    async def test_power_number_stages_the_matching_period(
        self, make_coordinator, mock_api, key, side, field, initial_value
    ):
        coordinator = make_coordinator()
        _seed_periodic_schedule(coordinator)
        entity = self._make(coordinator, key)
        entity.async_write_ha_state = MagicMock()
        entity._handle_coordinator_update()

        assert entity.native_value == initial_value
        assert entity.available is True
        assert entity.native_unit_of_measurement == "W"
        assert entity.native_min_value == 1
        assert entity.native_max_value == 100000
        assert entity.native_step == 100

        await entity.async_set_native_value(1800)

        assert entity.native_value == 1800
        assert coordinator.data[SERIAL][key] == 1800
        assert coordinator._schedule_drafts[SERIAL][side][field] == 1800
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()

    def test_power_numbers_require_a_readable_periodic_schedule(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        entity = self._make(coordinator, AlphaESSNames.ChargePower1)

        assert entity.available is False
        coordinator._periodic_readable[SERIAL] = True
        coordinator._periodic_schedules[SERIAL] = _periodic_schedule()
        assert entity.available is True
        coordinator._periodic_write_denied.add(SERIAL)
        assert entity.available is False

    async def test_staging_failure_reverts_number_and_propagates(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        coordinator.set_number_setting(SERIAL, "batUseCap", 20)
        coordinator.stage_schedule_change = MagicMock(
            side_effect=RuntimeError("schedule seed unavailable")
        )
        entity = self._make(coordinator, AlphaESSNames.batUseCap)
        entity._attr_native_value = 20
        entity.async_write_ha_state = MagicMock()

        with pytest.raises(RuntimeError, match="schedule seed unavailable"):
            await entity.async_set_native_value(22)

        assert entity.native_value == 20
        assert coordinator.get_number_setting(SERIAL, "batUseCap") == 20
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()

    async def test_first_value_staging_failure_clears_transient_setting(
        self, make_coordinator
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _complete_schedule_data()}
        coordinator.stage_schedule_change = MagicMock(
            side_effect=RuntimeError("schedule seed unavailable")
        )
        entity = self._make(coordinator, AlphaESSNames.batUseCap)
        entity._attr_native_value = None
        entity.async_write_ha_state = MagicMock()

        with pytest.raises(RuntimeError, match="schedule seed unavailable"):
            await entity.async_set_native_value(22)

        assert entity.native_value is None
        assert coordinator.get_number_setting(SERIAL, "batUseCap") is None

    def test_properties(self, make_coordinator):
        coordinator = make_coordinator()
        # The discharge cutoff needs the periodic API to be editable at all.
        _seed_periodic_schedule(coordinator)
        entity = self._make(coordinator)
        entity._attr_native_value = 10.0

        coordinator.last_update_success = False
        assert entity.available is False
        coordinator.last_update_success = True
        coordinator.cloud_available = True
        assert entity.available is True

        assert entity.native_value == 10.0
        assert entity.name == "Discharge To SOC"
        assert entity.suggested_object_id == f"{SERIAL} Discharge To SOC"
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
        mock_api.setEvChargerCurrentsBySn.assert_awaited_once_with(
            sysSn=SERIAL, currentsetting=10
        )

    async def test_rejected_set_value_keeps_the_api_explanation(
        self, make_coordinator, mock_api
    ):
        from alphaess.alphaess import AlphaESSApiError

        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entity = self._make(coordinator)
        mock_api.setEvChargerCurrentsBySn.side_effect = AlphaESSApiError(
            code=6008, description="Set failed"
        )

        # A raw library error reaches the frontend as "Unknown error"; the
        # entity must translate it and keep the return code visible.
        with pytest.raises(HomeAssistantError, match="6008"):
            await entity.async_set_native_value(10.0)

    async def test_an_already_translated_error_passes_through_unchanged(
        self, make_coordinator, mock_api
    ):
        """The coordinator can raise HomeAssistantError itself; re-wrapping it
        would bury the message it already chose."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entity = self._make(coordinator)
        coordinator.set_ev_charger_current = AsyncMock(
            side_effect=HomeAssistantError("charger control is unavailable")
        )

        with pytest.raises(HomeAssistantError, match="charger control is unavailable"):
            await entity.async_set_native_value(10.0)

    async def test_transport_failure_on_set_value_is_translated(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entity = self._make(coordinator)
        mock_api.setEvChargerCurrentsBySn.side_effect = OSError("boom")

        with pytest.raises(HomeAssistantError, match="could not be sent"):
            await entity.async_set_native_value(10.0)

    def test_properties(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {AlphaESSNames.evchargersn: "EV123"}
        }
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
