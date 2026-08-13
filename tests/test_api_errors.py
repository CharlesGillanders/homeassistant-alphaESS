"""Tests for AlphaESSApiError handling.

The client runs with raise_on_error=True so schedule writes can tell an accepted
schedule from a rejected one. That means API-level rejections now arrive as
exceptions everywhere, including on reads that used to just return None — these
tests pin down which paths absorb them and which don't.
"""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from alphaess.alphaess import AlphaESSApiError
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)

import custom_components.alphaess as init_mod
from custom_components.alphaess import async_setup_entry
from custom_components.alphaess.coordinator import PERIODIC_NOT_ENTITLED

from .conftest import FakeEntry
from .test_periodic_schedule import SERIAL, _schedule_data


def _api_error(code, expMsg=None):
    return AlphaESSApiError(code=code, msg="rejected", expMsg=expMsg,
                            path="https://openapi.alphaess.com/api/x",
                            description="desc")


class TestErrorDescriptions:
    """Codes are rendered with the library's own wording, not a local copy."""

    def test_known_code_shows_its_meaning(self):
        from alphaess.alphaess import UNDOCUMENTED_RETURN_CODES

        from custom_components.alphaess.coordinator import describe_api_error

        err = AlphaESSApiError(
            code=6017, msg="No operation permissions",
            description=UNDOCUMENTED_RETURN_CODES[6017])
        assert describe_api_error(err) == "6017 (No operation permissions)"

    def test_expmsg_is_appended_when_present(self):
        err = AlphaESSApiError(code=6001, description="Parameter error",
                               expMsg="time list is null")
        from custom_components.alphaess.coordinator import describe_api_error

        assert describe_api_error(err) == "6001 (Parameter error) - time list is null"

    def test_unknown_code_still_shows_the_number(self):
        from custom_components.alphaess.coordinator import describe_api_error

        assert describe_api_error(AlphaESSApiError(code=9999)) == "9999"


class TestReadsStayTolerant:
    """A refused read must not take the whole inverter down with it.

    Before raise_on_error these returned None and the sensor simply went
    missing. That has to stay true, otherwise an endpoint the account isn't
    entitled to would abort the fetch and trip the per-inverter error backoff.
    """

    async def test_read_swallows_api_error_and_returns_none(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        mock_api.getChargeConfigInfo.side_effect = _api_error(6017)

        assert await coordinator._read(mock_api.getChargeConfigInfo, SERIAL) is None

    async def test_read_lets_transport_errors_through(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        mock_api.getChargeConfigInfo.side_effect = OSError("connection reset")

        with pytest.raises(OSError):
            await coordinator._read(mock_api.getChargeConfigInfo, SERIAL)

    async def test_refused_endpoint_does_not_fail_the_poll(self, make_coordinator, mock_api):
        """One dead endpoint should cost one value, not the whole inverter."""
        mock_api.getESSList.return_value = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]
        mock_api.getChargeConfigInfo.side_effect = _api_error(6017)
        coordinator = make_coordinator(models=["SMILE5-INV"])

        result = await coordinator._async_update_data()

        assert SERIAL in result
        assert coordinator._inverter_error_count.get(SERIAL, 0) == 0
        assert coordinator.cloud_available is True


class TestProbeDistinguishesRejections:
    async def test_6017_is_cached_as_unreadable(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        mock_api.getTimeChargeBySn.side_effect = _api_error(PERIODIC_NOT_ENTITLED)

        assert await coordinator.async_probe_periodic_readable(SERIAL) is False
        assert coordinator._periodic_readable[SERIAL] is False

    async def test_other_codes_leave_the_answer_open(self, make_coordinator, mock_api):
        """6042 "system offline" could well succeed on the next poll."""
        coordinator = make_coordinator()
        mock_api.getTimeChargeBySn.side_effect = _api_error(6042)

        assert await coordinator.async_probe_periodic_readable(SERIAL) is None
        assert SERIAL not in coordinator._periodic_readable


class TestWriteDetectsRejection:
    @pytest.fixture
    def coordinator(self, make_coordinator):
        c = make_coordinator()
        c.data = {SERIAL: _schedule_data()}
        c._periodic_readable[SERIAL] = True
        return c

    async def test_6017_stops_further_attempts(self, coordinator, mock_api, caplog):
        caplog.set_level(logging.INFO)
        mock_api.setTimeChargeBySn.side_effect = _api_error(PERIODIC_NOT_ENTITLED)

        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)
        assert SERIAL in coordinator._periodic_write_denied
        assert "not entitled" in caplog.text

        mock_api.setTimeChargeBySn.reset_mock()
        await coordinator.async_write_charge_config(SERIAL, grid_charge=0)
        mock_api.setTimeChargeBySn.assert_not_awaited()
        # The legacy endpoint still gets written both times.
        assert mock_api.updateChargeConfigInfo.await_count == 2

    async def test_other_rejections_are_logged_with_expmsg(self, coordinator, mock_api, caplog):
        mock_api.setTimeChargeBySn.side_effect = _api_error(6001, expMsg="time list is null")

        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)

        assert "time list is null" in caplog.text
        # Not permanent, so keep trying on the next write.
        assert SERIAL not in coordinator._periodic_write_denied
        mock_api.updateChargeConfigInfo.assert_awaited_once()

    async def test_acceptance_now_means_something(self, coordinator, mock_api):
        """No exception means the API took it -- previously unknowable."""
        assert await coordinator._async_write_periodic_schedule(
            SERIAL, coordinator._current_schedule_state(SERIAL)) is True


class TestEntitiesRevertOnRejection:
    """A refused write must not leave the UI showing a value that never landed."""

    async def test_switch_reverts(self, make_coordinator, mock_api):
        from custom_components.alphaess.switch import AlphaSwitch

        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        # MagicMock(name=...) names the mock rather than setting .name
        description = MagicMock(icon=None, entity_category=None,
                                coordinator_key="gridCharge")
        description.name = "Grid Charge Enabled"
        switch = AlphaSwitch(coordinator, SERIAL, FakeEntry(), description)
        switch.async_write_ha_state = MagicMock()
        switch._optimistic_state = False
        mock_api.updateChargeConfigInfo.side_effect = _api_error(6042)
        mock_api.setTimeChargeBySn.side_effect = _api_error(6042)

        await switch.async_turn_on()

        assert switch._optimistic_state is False

    async def test_number_reverts_stored_value_too(self, make_coordinator, mock_api):
        """The stored value is what the charge/discharge buttons send next."""
        from custom_components.alphaess.enums import AlphaESSNames
        from custom_components.alphaess.number import AlphaNumber

        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        description = MagicMock(key=AlphaESSNames.batHighCap, entity_category=None,
                                native_unit_of_measurement="%", icon=None)
        description.name = "batHighCap"
        number = AlphaNumber(coordinator, SERIAL, FakeEntry(), description)
        number.async_write_ha_state = MagicMock()
        number._attr_native_value = 90.0
        mock_api.updateChargeConfigInfo.side_effect = _api_error(6042)
        mock_api.setTimeChargeBySn.side_effect = _api_error(6042)

        await number.async_set_native_value(55.0)

        assert number._attr_native_value == 90.0
        assert coordinator.get_number_setting(SERIAL, "batHighCap") == 90.0


class TestServicesReportCleanly:
    """A rejection should read as an HA error, not a raw library traceback."""

    def _call(self, mock_hass, coordinator, data):
        entry = FakeEntry()
        entry.runtime_data = coordinator
        mock_hass.config_entries.async_entries.return_value = [entry]
        return SimpleNamespace(hass=mock_hass, data=data)

    async def test_charge_service(self, mock_hass, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        mock_api.setTimeChargeBySn.side_effect = _api_error(6042)
        mock_api.updateChargeConfigInfo.side_effect = _api_error(6042)
        call = self._call(mock_hass, coordinator, {
            "serial": SERIAL, "chargestopsoc": 90, "enabled": True,
            "cp1start": "01:00", "cp1end": "05:00",
            "cp2start": "00:00", "cp2end": "00:00",
        })

        with pytest.raises(HomeAssistantError, match="rejected the charge settings"):
            await init_mod._async_service_battery_charge(call)

    async def test_discharge_service(self, mock_hass, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        mock_api.setTimeChargeBySn.side_effect = _api_error(6042)
        mock_api.updateDisChargeConfigInfo.side_effect = _api_error(6042)
        call = self._call(mock_hass, coordinator, {
            "serial": SERIAL, "dischargecutoffsoc": 10, "enabled": True,
            "dp1start": "18:00", "dp1end": "22:00",
            "dp2start": "00:00", "dp2end": "00:00",
        })

        with pytest.raises(HomeAssistantError, match="rejected the discharge settings"):
            await init_mod._async_service_battery_discharge(call)


class TestButtonsDoNotBurnTheRateLimit:
    """A refused command shouldn't cost the user a 30 second cooldown."""

    def _button(self, coordinator, key, name, notify=False):
        from custom_components.alphaess.button import AlphaESSBatteryButton
        from custom_components.alphaess.const import CONF_DISABLE_NOTIFICATIONS

        description = MagicMock(key=key, icon=None, entity_category=None)
        description.name = name

        subentry = None
        if notify:
            subentry = MagicMock(subentry_id="sub-1")
            subentry.data = {CONF_DISABLE_NOTIFICATIONS: False}

        button = AlphaESSBatteryButton(
            coordinator, FakeEntry(), SERIAL, description, subentry=subentry)
        button.hass = MagicMock()
        button.hass.services.async_call = AsyncMock()
        if notify:
            entry = MagicMock()
            entry.subentries = {"sub-1": subentry}
            button.hass.config_entries.async_get_entry = MagicMock(return_value=entry)
        return button

    def _notifications(self, button):
        return [c for c in button.hass.services.async_call.await_args_list
                if c.args[:2] == ("persistent_notification", "create")]

    async def test_timed_command_rejection_restores_the_timestamp(
        self, make_coordinator, mock_api
    ):
        from custom_components.alphaess.enums import AlphaESSNames

        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        mock_api.setTimeChargeBySn.side_effect = _api_error(6042)
        mock_api.updateChargeConfigInfo.side_effect = _api_error(6042)
        button = self._button(coordinator, AlphaESSNames.ButtonChargeThirty, "30 Minute Charge")

        await button.async_press()

        assert coordinator.last_charge_update.get(SERIAL) is None

    async def test_reset_rejection_restores_both_timestamps(self, make_coordinator, mock_api):
        from custom_components.alphaess.enums import AlphaESSNames

        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        mock_api.updateChargeConfigInfo.side_effect = _api_error(6042)
        button = self._button(coordinator, AlphaESSNames.ButtonRechargeConfig, "Reset Config")

        await button.async_press()

        assert coordinator.last_charge_update.get(SERIAL) is None
        assert coordinator.last_discharge_update.get(SERIAL) is None

    async def test_failures_are_notified_when_notifications_are_on(
        self, make_coordinator, mock_api
    ):
        from custom_components.alphaess.enums import AlphaESSNames

        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        mock_api.setTimeChargeBySn.side_effect = _api_error(6042)
        mock_api.updateChargeConfigInfo.side_effect = _api_error(6042)
        button = self._button(coordinator, AlphaESSNames.ButtonChargeThirty,
                              "30 Minute Charge", notify=True)

        await button.async_press()

        notifications = self._notifications(button)
        assert notifications, "expected a failure notification"
        assert "rejected" in notifications[-1].args[2]["message"]

    async def test_reset_failure_is_notified(self, make_coordinator, mock_api):
        from custom_components.alphaess.enums import AlphaESSNames

        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        mock_api.updateChargeConfigInfo.side_effect = _api_error(6042)
        button = self._button(coordinator, AlphaESSNames.ButtonRechargeConfig,
                              "Reset Config", notify=True)

        await button.async_press()

        notifications = self._notifications(button)
        assert notifications
        assert "Reset failed" in notifications[-1].args[2]["title"]


class TestConfigFlowRejectsBadCredentials:
    """Previously a wrong AppSecret still created an entry."""

    async def _validate(self, mock_hass, monkeypatch, err):
        from custom_components.alphaess import config_flow as cf

        client = MagicMock()
        client.authenticate = AsyncMock(side_effect=err)
        client.getESSList = AsyncMock()
        monkeypatch.setattr(cf.alphaess, "alphaess", MagicMock(return_value=client))
        monkeypatch.setattr(cf, "async_get_clientsession", MagicMock())
        return await cf.validate_input(
            mock_hass, {"AppID": "id", "AppSecret": "secret"})

    async def test_sign_failure_is_invalid_auth(self, mock_hass, monkeypatch):
        from custom_components.alphaess.config_flow import InvalidAuth

        with pytest.raises(InvalidAuth):
            await self._validate(mock_hass, monkeypatch, _api_error(6007))

    async def test_other_codes_are_cannot_connect(self, mock_hass, monkeypatch):
        from custom_components.alphaess.config_flow import CannotConnect

        with pytest.raises(CannotConnect):
            await self._validate(mock_hass, monkeypatch, _api_error(6042))


class TestSetupHandlesRejection:
    def _entry(self):
        entry = FakeEntry()
        entry.options = {}
        return entry

    async def _run(self, mock_hass, monkeypatch, err):
        client = MagicMock()
        client.getESSList = AsyncMock(side_effect=err)
        monkeypatch.setattr(init_mod.alphaess, "alphaess", MagicMock(return_value=client))
        monkeypatch.setattr(init_mod, "async_get_clientsession", MagicMock())
        return await async_setup_entry(mock_hass, self._entry())

    async def test_credential_codes_raise_auth_failed(self, mock_hass, monkeypatch):
        """Bad credentials come back as a return code, not an HTTP 401."""
        with pytest.raises(ConfigEntryAuthFailed):
            await self._run(mock_hass, monkeypatch, _api_error(6007))

    async def test_other_codes_raise_not_ready(self, mock_hass, monkeypatch):
        with pytest.raises(ConfigEntryNotReady):
            await self._run(mock_hass, monkeypatch, _api_error(6042))
