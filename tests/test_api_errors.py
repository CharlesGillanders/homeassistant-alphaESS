"""Tests for AlphaESSApiError handling.

The client runs with raise_on_error=True so schedule writes can tell an accepted
schedule from a rejected one. That means API-level rejections now arrive as
exceptions everywhere, including on reads that used to just return None — these
tests pin down which paths absorb them and which don't.
"""
import logging
from copy import deepcopy
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
from custom_components.alphaess.coordinator import (
    PERIODIC_NOT_ENTITLED,
    ScheduleWriteError,
)
from custom_components.alphaess.enums import AlphaESSNames

from .conftest import FakeEntry
from .test_periodic_schedule import (
    SERIAL,
    _daily_schedule,
    _seed,
)


def _api_error(code, expMsg=None):
    return AlphaESSApiError(
        code=code,
        msg="rejected",
        expMsg=expMsg,
        path="https://openapi.alphaess.com/api/x",
        description="desc",
    )


def _seed_full_schedule(make_coordinator, mock_api):
    """Seed and serve a complete periodic snapshot for safe writes."""
    periodic = _daily_schedule()
    coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
    mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
    return coordinator


def _seed_unentitled(make_coordinator):
    """Seed a system whose periodic GET was definitively rejected with 6017."""
    return _seed(make_coordinator(), periodic=None, readable=False)


def _assert_no_schedule_api_calls(mock_api):
    """Assert that no schedule endpoint — periodic or retired — was called."""
    for method in (
        mock_api.getTimeChargeBySn,
        mock_api.getChargeConfigInfo,
        mock_api.getDisChargeConfigInfo,
        mock_api.setTimeChargeBySn,
        mock_api.updateChargeConfigInfo,
        mock_api.updateDisChargeConfigInfo,
    ):
        method.assert_not_awaited()


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


class TestServiceTimeNormalisation:
    """The API wants zero-padded HH:mm on the quarter hour.

    "9:00" comes back 6001 and an off-grid minute is silently unusable, and the
    services used to pass whatever was typed straight through.
    """

    def test_pads_a_single_digit_hour(self):
        assert init_mod._api_time("9:00") == "09:00"

    def test_rounds_to_the_quarter_hour(self):
        assert init_mod._api_time("02:07") == "02:00"
        assert init_mod._api_time("02:08") == "02:15"

    def test_leaves_a_valid_time_alone(self):
        assert init_mod._api_time("13:45") == "13:45"

    def test_midnight_wrap(self):
        assert init_mod._api_time("23:53") == "00:00"

    def test_rejects_nonsense(self):
        import voluptuous as vol

        with pytest.raises(vol.Invalid):
            init_mod._api_time("not a time")


class TestReadsStayTolerant:
    """A refused read must not take the whole inverter down with it.

    Before raise_on_error these returned None and the sensor simply went
    missing. That has to stay true, otherwise an endpoint the account isn't
    entitled to would abort the fetch and trip the per-inverter error backoff.
    """

    async def test_read_swallows_api_error_and_returns_none(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        mock_api.getSumDataForCustomer.side_effect = _api_error(6017)

        assert await coordinator._read(mock_api.getSumDataForCustomer, SERIAL) is None

    async def test_read_lets_transport_errors_through(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        mock_api.getSumDataForCustomer.side_effect = OSError("connection reset")

        with pytest.raises(OSError):
            await coordinator._read(mock_api.getSumDataForCustomer, SERIAL)

    async def test_refused_endpoint_does_not_fail_the_poll(self, make_coordinator, mock_api):
        """One dead endpoint should cost one value, not the whole inverter."""
        mock_api.getESSList.return_value = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]
        mock_api.getSumDataForCustomer.side_effect = _api_error(6017)
        coordinator = make_coordinator(models=["SMILE5-INV"])

        result = await coordinator._async_update_data()

        assert SERIAL in result
        assert coordinator._inverter_error_count.get(SERIAL, 0) == 0
        assert coordinator.cloud_available is True
        # Polling never touches the retired legacy schedule endpoints.
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.getDisChargeConfigInfo.assert_not_awaited()


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
    def coordinator(self, make_coordinator, mock_api):
        return _seed_full_schedule(make_coordinator, mock_api)

    async def test_write_6017_is_visible_then_refuses_every_write(
        self, coordinator, mock_api, caplog
    ):
        caplog.set_level(logging.INFO)
        mock_api.setTimeChargeBySn.side_effect = _api_error(PERIODIC_NOT_ENTITLED)

        with pytest.raises(ScheduleWriteError, match="periodic schedule.*not updated"):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        assert SERIAL in coordinator._periodic_write_denied
        assert "not entitled" in caplog.text
        mock_api.updateChargeConfigInfo.assert_not_awaited()

        mock_api.getTimeChargeBySn.reset_mock()
        mock_api.setTimeChargeBySn.reset_mock()
        with pytest.raises(ScheduleWriteError, match="periodic schedule API"):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.getTimeChargeBySn.assert_not_awaited()
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_other_rejections_are_visible_and_logged_with_expmsg(
        self, coordinator, mock_api, caplog
    ):
        mock_api.setTimeChargeBySn.side_effect = _api_error(6001, expMsg="time list is null")

        with pytest.raises(ScheduleWriteError, match="periodic schedule.*not updated"):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        assert "time list is null" in caplog.text
        # Not permanent, so keep trying on the next write.
        assert SERIAL not in coordinator._periodic_write_denied
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_public_write_posts_only_the_periodic_store(
        self, coordinator, mock_api
    ):
        await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()


class TestUnentitledSystemsUseTheBackup:
    """A definitive 6017 activates the legacy backup instead of going dead."""

    def test_stage_works_from_the_backup_view_without_api_calls(
        self, make_coordinator, mock_api
    ):
        coordinator = _seed_unentitled(make_coordinator)

        assert coordinator.can_stage_schedule(SERIAL)
        coordinator.stage_schedule_change(SERIAL, charge={"gridCharge": 0})

        assert coordinator.has_schedule_draft(SERIAL)
        _assert_no_schedule_api_calls(mock_api)

    async def test_quick_charge_write_hits_the_legacy_store(
        self, make_coordinator, mock_api
    ):
        coordinator = _seed_unentitled(make_coordinator)
        mock_api.getChargeConfigInfo.return_value = {
            "batHighCap": 90, "gridCharge": 0,
            "timeChaf1": "00:00", "timeChae1": "00:00",
            "timeChaf2": "00:00", "timeChae2": "00:00",
        }

        await coordinator.update_charge("batHighCap", SERIAL, 30)

        mock_api.updateChargeConfigInfo.assert_awaited_once()
        mock_api.getTimeChargeBySn.assert_not_awaited()
        mock_api.setTimeChargeBySn.assert_not_awaited()

    async def test_write_denied_is_not_backup_and_refuses(
        self, make_coordinator, mock_api
    ):
        """Read OK but write 6017: the periodic store still governs the
        inverter, so no fallback — the write is refused outright."""
        coordinator = _seed_full_schedule(make_coordinator, mock_api)
        coordinator._periodic_write_denied.add(SERIAL)

        with pytest.raises(ScheduleWriteError, match="Schedule control requires"):
            await coordinator.update_charge("batHighCap", SERIAL, 30)

        _assert_no_schedule_api_calls(mock_api)


class TestResetIsBackupOnly:
    """Reset clears the legacy backup stores; the periodic store cannot be
    cleared, so it reports impossible there."""

    def test_reset_capability_tracks_the_mode(self, make_coordinator, mock_api):
        backup = _seed_unentitled(make_coordinator)
        assert backup.can_reset_schedule(SERIAL) is True

    def test_reset_impossible_on_periodic_systems(self, make_coordinator, mock_api):
        periodic = _seed_full_schedule(make_coordinator, mock_api)
        assert periodic.can_reset_schedule(SERIAL) is False


class TestEntitiesStageWithoutWriting:
    """Individual entity edits stay local until the explicit Apply action."""

    async def test_switch_stages_without_api_calls(self, make_coordinator, mock_api):
        from custom_components.alphaess.switch import AlphaSwitch

        coordinator = _seed_full_schedule(make_coordinator, mock_api)
        # MagicMock(name=...) names the mock rather than setting .name.
        description = MagicMock(
            icon=None,
            entity_category=None,
            coordinator_key="gridCharge",
        )
        description.name = "Grid Charge Enabled"
        switch = AlphaSwitch(coordinator, SERIAL, FakeEntry(), description)
        switch.async_write_ha_state = MagicMock()
        switch._optimistic_state = False

        await switch.async_turn_on()

        assert switch._optimistic_state is True
        assert coordinator.has_schedule_draft(SERIAL)
        assert coordinator._schedule_drafts[SERIAL]["charge"]["gridCharge"] == 1
        _assert_no_schedule_api_calls(mock_api)

    async def test_number_stages_and_keeps_its_local_value(
        self, make_coordinator, mock_api
    ):
        from custom_components.alphaess.number import AlphaNumber

        coordinator = _seed_full_schedule(make_coordinator, mock_api)
        description = MagicMock(
            key=AlphaESSNames.batHighCap,
            entity_category=None,
            native_unit_of_measurement="%",
            native_min_value=10,
            native_max_value=100,
            native_step=1,
            icon=None,
        )
        description.name = "batHighCap"
        number = AlphaNumber(coordinator, SERIAL, FakeEntry(), description)
        number.async_write_ha_state = MagicMock()
        number._attr_native_value = 90.0

        await number.async_set_native_value(55.0)

        assert number._attr_native_value == 55.0
        assert coordinator.get_number_setting(SERIAL, "batHighCap") == 55.0
        assert coordinator._schedule_drafts[SERIAL]["charge"]["batHighCap"] == 55.0
        _assert_no_schedule_api_calls(mock_api)


class TestServicesReportCleanly:
    """Schedule failures cross the service boundary as HA-visible errors."""

    def _call(self, mock_hass, coordinator, data):
        entry = FakeEntry()
        entry.runtime_data = coordinator
        mock_hass.config_entries.async_entries.return_value = [entry]
        return SimpleNamespace(hass=mock_hass, data=data)

    async def test_charge_service(self, mock_hass, make_coordinator, mock_api):
        coordinator = _seed_full_schedule(make_coordinator, mock_api)
        mock_api.setTimeChargeBySn.side_effect = _api_error(6042)
        call = self._call(mock_hass, coordinator, {
            "serial": SERIAL, "chargestopsoc": 90, "enabled": True,
            "cp1start": "01:00", "cp1end": "05:15",
            "cp2start": "00:00", "cp2end": "00:00",
        })

        with pytest.raises(HomeAssistantError, match="periodic schedule.*not updated"):
            await init_mod._async_service_battery_charge(call)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_discharge_service_surfaces_transport_failure(
        self, mock_hass, make_coordinator, mock_api
    ):
        coordinator = _seed_full_schedule(make_coordinator, mock_api)
        mock_api.getTimeChargeBySn.side_effect = OSError("connection reset")
        call = self._call(mock_hass, coordinator, {
            "serial": SERIAL, "dischargecutoffsoc": 10, "enabled": True,
            "dp1start": "18:00", "dp1end": "22:00",
            "dp2start": "00:00", "dp2end": "00:00",
        })

        with pytest.raises(HomeAssistantError, match="connection reset"):
            await init_mod._async_service_battery_discharge(call)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()

    @pytest.mark.parametrize(
        ("handler", "writer", "data", "side"),
        [
            (
                init_mod._async_service_battery_charge,
                "async_write_charge_config",
                {
                    "serial": SERIAL,
                    "chargestopsoc": 90,
                    "enabled": True,
                    "cp1start": "01:00",
                    "cp1end": "05:00",
                    "cp2start": "00:00",
                    "cp2end": "00:00",
                },
                "charge",
            ),
            (
                init_mod._async_service_battery_discharge,
                "async_write_discharge_config",
                {
                    "serial": SERIAL,
                    "dischargecutoffsoc": 20,
                    "enabled": True,
                    "dp1start": "17:00",
                    "dp1end": "21:00",
                    "dp2start": "00:00",
                    "dp2end": "00:00",
                },
                "discharge",
            ),
        ],
    )
    async def test_raw_api_rejection_is_wrapped_for_home_assistant(
        self, mock_hass, make_coordinator, handler, writer, data, side
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        setattr(coordinator, writer, AsyncMock(side_effect=_api_error(6008)))
        call = self._call(mock_hass, coordinator, data)

        with pytest.raises(HomeAssistantError, match=f"rejected the {side} settings"):
            await handler(call)


class TestButtonsDoNotBurnTheRateLimit:
    """Button failures remain visible and roll back reserved cooldowns."""

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
        return [
            call
            for call in button.hass.services.async_call.await_args_list
            if call.args[:2] == ("persistent_notification", "create")
        ]

    async def test_apply_rejection_bubbles_and_keeps_the_draft(
        self, make_coordinator, mock_api
    ):
        coordinator = _seed_full_schedule(make_coordinator, mock_api)
        coordinator.stage_schedule_change(SERIAL, charge={"gridCharge": 0})
        mock_api.setTimeChargeBySn.side_effect = _api_error(6042)
        button = self._button(
            coordinator,
            AlphaESSNames.ButtonApplySchedule,
            "Apply Charge Discharge Schedule",
        )

        with pytest.raises(HomeAssistantError, match="periodic schedule.*not updated"):
            await button.async_press()

        assert coordinator.has_schedule_draft(SERIAL)
        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    @pytest.mark.parametrize("failure_kind", ["api_rejection", "transport"])
    async def test_timed_failure_restores_timestamp_and_is_visible(
        self, make_coordinator, mock_api, failure_kind
    ):
        coordinator = _seed_full_schedule(make_coordinator, mock_api)
        if failure_kind == "api_rejection":
            mock_api.setTimeChargeBySn.side_effect = _api_error(6042)
        else:
            mock_api.getTimeChargeBySn.side_effect = OSError("connection reset")
        button = self._button(
            coordinator,
            AlphaESSNames.ButtonChargeThirty,
            "30 Minute Charge",
            notify=True,
        )

        with pytest.raises(HomeAssistantError):
            await button.async_press()

        assert coordinator.last_charge_update.get(SERIAL) is None
        notifications = self._notifications(button)
        assert notifications, "expected a failure notification"
        assert "could not apply" in notifications[-1].args[2]["message"]

        if failure_kind == "api_rejection":
            mock_api.setTimeChargeBySn.assert_awaited_once()
        else:
            mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()


class TestConfigFlowRejectsBadCredentials:
    """Previously a wrong AppSecret still created an entry."""

    async def _validate(self, mock_hass, monkeypatch, err):
        from custom_components.alphaess import config_flow as cf

        client = MagicMock()
        client.getESSList = AsyncMock(side_effect=err)
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
