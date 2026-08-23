"""Edge-case coverage for safe schedule transactions, polling, and EV discovery."""

import asyncio
import logging
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from custom_components.alphaess.button import AlphaESSBatteryButton
from custom_components.alphaess.coordinator import (
    PERIODIC_NOT_ENTITLED,
    PERIODIC_READ_OK,
    PERIODIC_READ_UNAVAILABLE,
    AlphaESSDataUpdateCoordinator,
    ScheduleConflictError,
    SchedulePartialWriteError,
    SchedulePartialWriteUnknownError,
    ScheduleWriteError,
    ScheduleWriteUnknownError,
    _format_periods,
)
from custom_components.alphaess.enums import AlphaESSNames
from custom_components.alphaess.number import AlphaEVNumber, AlphaNumber
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
from .test_periodic_schedule import (
    SERIAL,
    _api_error,
    _cached,
    _daily_schedule,
    _entity_data,
    _legacy_charge,
    _legacy_discharge,
    _period,
    _seed,
)


def _description(descriptions, key):
    """Return one entity description by key."""
    return next(description for description in descriptions if description.key == key)


class TestDefensiveHelpers:
    """Reject malformed replacement resources without inventing defaults."""

    def test_empty_period_list_has_unambiguous_log_text(self):
        assert _format_periods([]) == "none"

    def test_clear_number_setting_restores_default_fallback(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.set_number_setting(SERIAL, "batHighCap", 87)

        coordinator.clear_number_setting(SERIAL, "batHighCap")

        assert coordinator.get_number_setting(SERIAL, "batHighCap", 90) == 90

    def test_reset_surface_is_backup_only(self, make_coordinator):
        """Reset acts on the legacy backup stores; a periodic-governed or
        write-denied system reports it impossible."""
        coordinator = _seed(make_coordinator(), periodic=None, readable=False)
        assert coordinator.can_reset_schedule(SERIAL) is True

        coordinator._periodic_readable[SERIAL] = True
        assert coordinator.can_reset_schedule(SERIAL) is False

        coordinator._periodic_readable[SERIAL] = False
        coordinator._periodic_write_denied.add(SERIAL)
        assert coordinator.can_reset_schedule(SERIAL) is False

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            (None, "not an object"),
            ({"executeCycleType": 2}, "valid executeCycleType"),
            (
                {
                    "executeCycleType": 0,
                    "chargeTimeList": ["not a period"],
                    "dischargeTimeList": [],
                },
                "non-object period",
            ),
            (
                {
                    "executeCycleType": 0,
                    "chargeTimeList": [{"beginTime": "01:00"}],
                    "dischargeTimeList": [],
                },
                "without both times",
            ),
        ],
        ids=("not-object", "bad-cycle", "bad-period", "missing-time"),
    )
    def test_malformed_periodic_resources_are_rejected(self, payload, message):
        with pytest.raises(ScheduleWriteError, match=message):
            AlphaESSDataUpdateCoordinator._normalise_periodic_schedule(payload)


class TestDraftEdges:
    """Draft bookkeeping must be inert or fail explicitly at its boundaries."""

    def test_empty_stage_is_a_noop(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}

        coordinator.stage_schedule_change(SERIAL)

        assert not coordinator.has_schedule_draft(SERIAL)
        coordinator.async_set_updated_data.assert_not_called()

    @pytest.mark.parametrize(
        ("readable", "periodic", "message"),
        [
            (None, None, "periodic schedule can be read"),
            (True, None, "complete periodic schedule"),
        ],
        ids=("capability-unknown", "readable-resource-missing"),
    )
    def test_stage_requires_a_complete_capability_snapshot(
        self, make_coordinator, readable, periodic, message
    ):
        coordinator = _seed(
            make_coordinator(), periodic=periodic, readable=readable
        )

        with pytest.raises(ScheduleWriteError, match=message):
            coordinator.stage_schedule_change(
                SERIAL, charge={"timeChae1": "04:30"}
            )

        assert not coordinator.has_schedule_draft(SERIAL)

    def test_write_denied_system_cannot_open_a_draft(self, make_coordinator):
        coordinator = _seed(
            make_coordinator(), periodic=_daily_schedule(), readable=True
        )
        coordinator._periodic_write_denied.add(SERIAL)

        with pytest.raises(ScheduleWriteError, match="periodic schedule API"):
            coordinator.stage_schedule_change(
                SERIAL, charge={"timeChae1": "04:30"}
            )

        assert not coordinator.has_schedule_draft(SERIAL)
        assert coordinator.can_stage_schedule(SERIAL) is False

    async def test_apply_without_pending_changes_is_rejected(self, make_coordinator):
        coordinator = make_coordinator()

        with pytest.raises(ScheduleWriteError, match="no pending schedule changes"):
            await coordinator.async_apply_schedule_draft(SERIAL)

    async def test_discard_and_second_apply_are_rejected_during_apply(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator.stage_schedule_change(
            SERIAL, charge={"timeChae1": "04:30"}
        )
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
        post_started = asyncio.Event()
        release_post = asyncio.Event()

        async def block_periodic_post(**_kwargs):
            post_started.set()
            await release_post.wait()

        mock_api.setTimeChargeBySn.side_effect = block_periodic_post
        first_apply = asyncio.create_task(
            coordinator.async_apply_schedule_draft(SERIAL)
        )
        await post_started.wait()

        try:
            with pytest.raises(ScheduleWriteError, match="Cannot discard.*Apply"):
                coordinator.discard_schedule_draft(SERIAL)
            with pytest.raises(ScheduleWriteError, match="already in progress"):
                await coordinator.async_apply_schedule_draft(SERIAL)

            # Editing remains allowed: it is newer intent which must survive
            # completion of the already-snapshotted transaction.
            coordinator.stage_schedule_change(
                SERIAL, charge={"timeChae1": "04:45"}
            )
        finally:
            release_post.set()

        await first_apply

        assert SERIAL not in coordinator._schedule_apply_in_progress
        assert coordinator.has_schedule_draft(SERIAL)
        assert coordinator._schedule_drafts[SERIAL]["charge"]["timeChae1"] == (
            "04:45"
        )

    async def test_edit_during_apply_is_retained_with_committed_conflict_base(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator.stage_schedule_change(
            SERIAL, charge={"timeChae1": "04:30"}
        )
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        async def stage_newer_edit(**_kwargs):
            coordinator.stage_schedule_change(
                SERIAL, charge={"timeChae1": "04:45"}
            )

        mock_api.setTimeChargeBySn.side_effect = stage_newer_edit

        await coordinator.async_apply_schedule_draft(SERIAL)

        assert coordinator.has_schedule_draft(SERIAL)
        assert coordinator._schedule_drafts[SERIAL]["charge"]["timeChae1"] == (
            "04:45"
        )
        assert coordinator._schedule_draft_base_periodic[SERIAL] == (
            coordinator._periodic_schedules[SERIAL]
        )

    async def test_periodic_conflict_is_cached_for_discarded_view(
        self, make_coordinator, mock_api
    ):
        original = _daily_schedule()
        changed_remotely = _daily_schedule(
            charge=[_period("01:00", "04:30", limit=90, power=3000)]
        )
        coordinator = _seed(make_coordinator(), periodic=original, readable=True)
        coordinator.stage_schedule_change(SERIAL, charge={"gridCharge": 0})
        mock_api.getTimeChargeBySn.return_value = deepcopy(changed_remotely)

        with pytest.raises(ScheduleConflictError, match="periodic schedule changed"):
            await coordinator.async_apply_schedule_draft(SERIAL)

        coordinator.discard_schedule_draft(SERIAL)

        assert coordinator._periodic_schedules[SERIAL] == _cached(changed_remotely)
        assert coordinator.data[SERIAL]["charge_timeChae1"] == "04:30"
        mock_api.setTimeChargeBySn.assert_not_awaited()


class TestPeriodicProbeEdges:
    """Periodic capability probes distinguish cancellation and bad payloads."""

    async def test_probe_propagates_cancellation(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        mock_api.getTimeChargeBySn.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await coordinator.async_probe_periodic_readable(SERIAL)

    async def test_probe_transport_failure_remains_unknown(
        self, make_coordinator, mock_api, caplog
    ):
        coordinator = make_coordinator()
        mock_api.getTimeChargeBySn.side_effect = OSError("connection reset")

        with caplog.at_level(logging.DEBUG):
            assert await coordinator.async_probe_periodic_readable(SERIAL) is None

        assert SERIAL not in coordinator._periodic_readable
        assert "connection reset" in caplog.text

    async def test_probe_malformed_payload_remains_unknown(
        self, make_coordinator, mock_api, caplog
    ):
        coordinator = make_coordinator()
        mock_api.getTimeChargeBySn.return_value = {"executeCycleType": 0}

        with caplog.at_level(logging.WARNING):
            assert await coordinator.async_probe_periodic_readable(SERIAL) is None

        assert SERIAL not in coordinator._periodic_readable
        assert "not safely writable" in caplog.text

    async def test_probe_started_before_apply_cannot_overwrite_accepted_cache(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator.stage_schedule_change(
            SERIAL, charge={"timeChae1": "04:30"}
        )
        probe_started = asyncio.Event()
        release_probe = asyncio.Event()
        read_count = 0

        async def delayed_first_read(_serial):
            nonlocal read_count
            read_count += 1
            if read_count == 1:
                probe_started.set()
                await release_probe.wait()
            return deepcopy(periodic)

        mock_api.getTimeChargeBySn.side_effect = delayed_first_read
        probe_task = asyncio.create_task(
            coordinator.async_probe_periodic_readable(SERIAL)
        )
        await probe_started.wait()
        apply_task = asyncio.create_task(
            coordinator.async_apply_schedule_draft(SERIAL)
        )

        # The Apply must queue behind the in-flight stale probe instead of
        # committing and then being overwritten when that probe returns.
        await asyncio.sleep(0)
        mock_api.setTimeChargeBySn.assert_not_awaited()
        release_probe.set()

        probe_result, _ = await asyncio.gather(probe_task, apply_task)

        assert probe_result is True
        assert read_count == 2
        assert coordinator._periodic_schedules[SERIAL]["chargeTimeList"][0][
            "endTime"
        ] == "04:30"

    async def test_existing_periodic_draft_fails_closed_after_read_becomes_6017(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator.stage_schedule_change(
            SERIAL, charge={"timeChae1": "04:30"}
        )
        mock_api.getTimeChargeBySn.side_effect = _api_error(
            PERIODIC_NOT_ENTITLED
        )

        with pytest.raises(ScheduleWriteError, match="no longer available"):
            await coordinator.async_apply_schedule_draft(SERIAL)

        assert coordinator._periodic_readable[SERIAL] is False
        assert SERIAL not in coordinator._periodic_schedules
        assert coordinator.has_schedule_draft(SERIAL)
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

        coordinator.discard_schedule_draft(SERIAL)

        # The entity data still holds the periodic projection; it must never
        # be reinterpreted as the legacy backup view.
        assert coordinator.can_stage_schedule(SERIAL) is False
        with pytest.raises(ScheduleWriteError, match="no schedule store is usable"):
            coordinator.stage_schedule_change(
                SERIAL, charge={"timeChae1": "04:15"}
            )

    async def test_staging_fails_closed_after_probe_returns_6017(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule(
            charge=[_period("02:00", "03:00", limit=88, power=1800)],
            discharge=[_period("19:00", "20:00", limit=18, power=1700)],
        )
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator._publish_schedule_view(SERIAL)
        assert coordinator.data[SERIAL]["charge_timeChae1"] == "03:00"

        mock_api.getTimeChargeBySn.side_effect = _api_error(
            PERIODIC_NOT_ENTITLED
        )
        assert await coordinator.async_probe_periodic_readable(SERIAL) is False

        assert SERIAL not in coordinator._periodic_schedules
        # The periodic projection in the entity data must never be
        # reinterpreted as the legacy backup view after the transition.
        assert coordinator.can_stage_schedule(SERIAL) is False
        for changes in (
            {"charge": {"timeChae1": "04:45"}},
            {"discharge": {"timeDise1": "21:00"}},
        ):
            with pytest.raises(
                ScheduleWriteError, match="no schedule store is usable"
            ):
                coordinator.stage_schedule_change(SERIAL, **changes)
        assert not coordinator.has_schedule_draft(SERIAL)


class TestDraftIsolation:
    """Pending entity drafts never contaminate immediate remote transactions."""

    async def test_pending_soc_does_not_leak_into_timed_charge(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator.stage_schedule_change(SERIAL, charge={"batHighCap": 99})
        coordinator.time_helper.calculate_time_window = lambda _minutes: (
            "10:00",
            "10:15",
        )
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.update_charge("timed", SERIAL, 15)

        periodic_write = mock_api.setTimeChargeBySn.await_args.kwargs
        assert periodic_write["chargeTimeList"][0]["beginTime"] == "10:00"
        assert periodic_write["chargeTimeList"][0]["chargeLimit"] == 90
        assert coordinator._schedule_drafts[SERIAL]["charge"]["batHighCap"] == 99
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_write_denial_fails_closed_for_later_staging(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
        mock_api.setTimeChargeBySn.side_effect = _api_error(
            PERIODIC_NOT_ENTITLED, "No operation permissions"
        )

        with pytest.raises(ScheduleWriteError, match="periodic schedule.*not updated"):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        assert SERIAL in coordinator._periodic_write_denied
        assert SERIAL not in coordinator._periodic_schedules
        assert coordinator.can_stage_schedule(SERIAL) is False
        with pytest.raises(ScheduleWriteError, match="Schedule control requires"):
            coordinator.stage_schedule_change(SERIAL, charge={"gridCharge": 0})
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    @pytest.mark.parametrize(
        ("side", "writer", "powers", "legacy_get", "legacy_post"),
        [
            (
                "charge",
                "async_write_charge_config",
                {"chargePower1": 1200},
                "getChargeConfigInfo",
                "updateChargeConfigInfo",
            ),
            (
                "discharge",
                "async_write_discharge_config",
                {"chargePower1": 1300},
                "getDisChargeConfigInfo",
                "updateDisChargeConfigInfo",
            ),
        ],
        ids=("charge", "discharge"),
    )
    async def test_power_only_write_skips_legacy_resource(
        self,
        make_coordinator,
        mock_api,
        side,
        writer,
        powers,
        legacy_get,
        legacy_post,
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await getattr(coordinator, writer)(SERIAL, powers=powers)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        getattr(mock_api, legacy_get).assert_not_awaited()
        getattr(mock_api, legacy_post).assert_not_awaited()
        assert coordinator._periodic_schedules[SERIAL][
            "chargeTimeList" if side == "charge" else "dischargeTimeList"
        ][0]["chargePower"] == next(iter(powers.values()))


class TestIdempotentDraftRetry:
    """A failed or lost write converges on retry without a duplicate POST."""

    async def test_rejected_periodic_write_keeps_draft_for_retry(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator.stage_schedule_change(SERIAL, charge={"gridCharge": 0})
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
        mock_api.setTimeChargeBySn.side_effect = _api_error(6042)

        with pytest.raises(ScheduleWriteError, match="not updated"):
            await coordinator.async_apply_schedule_draft(SERIAL)

        assert coordinator.has_schedule_draft(SERIAL)
        mock_api.setTimeChargeBySn.reset_mock(side_effect=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.async_apply_schedule_draft(SERIAL)

        assert mock_api.setTimeChargeBySn.await_args.kwargs["gridChargeCycle"] == 0
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        assert not coordinator.has_schedule_draft(SERIAL)

    async def test_lost_write_retry_is_a_noop_when_remote_matches(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
        mock_api.setTimeChargeBySn.side_effect = OSError("response lost")

        with pytest.raises(ScheduleWriteUnknownError, match="unknown outcome"):
            await coordinator.async_write_charge_config(
                SERIAL, times={"timeChae1": "04:30"}
            )

        # The lost POST actually landed; the retry's fresh read reports it,
        # so nothing further is sent.
        accepted = deepcopy(periodic)
        accepted["chargeTimeList"][0]["endTime"] = "04:30"
        mock_api.setTimeChargeBySn.reset_mock(side_effect=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(accepted)

        await coordinator.async_write_charge_config(
            SERIAL, times={"timeChae1": "04:30"}
        )

        mock_api.setTimeChargeBySn.assert_not_awaited()
        assert coordinator._periodic_schedules[SERIAL] == _cached(accepted)

    async def test_unknown_outcome_apply_keeps_draft_until_base_is_refreshed(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator.stage_schedule_change(
            SERIAL, charge={"timeChae1": "04:30"}
        )
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
        mock_api.setTimeChargeBySn.side_effect = OSError("response lost")

        with pytest.raises(ScheduleWriteUnknownError, match="unknown outcome"):
            await coordinator.async_apply_schedule_draft(SERIAL)

        assert coordinator.has_schedule_draft(SERIAL)

        # The lost write landed remotely. A blind retry must surface that as
        # a conflict rather than re-POST over the changed resource.
        accepted = deepcopy(periodic)
        accepted["chargeTimeList"][0]["endTime"] = "04:30"
        mock_api.setTimeChargeBySn.reset_mock(side_effect=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(accepted)

        with pytest.raises(ScheduleConflictError, match="periodic schedule changed"):
            await coordinator.async_apply_schedule_draft(SERIAL)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        assert coordinator.has_schedule_draft(SERIAL)

        # Restaging the same intent against the refreshed base already
        # matches, so Apply makes no POST and clears the draft.
        coordinator.discard_schedule_draft(SERIAL)
        coordinator.stage_schedule_change(
            SERIAL, charge={"timeChae1": "04:30"}
        )

        await coordinator.async_apply_schedule_draft(SERIAL)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        assert not coordinator.has_schedule_draft(SERIAL)
        assert coordinator._periodic_schedules[SERIAL] == _cached(accepted)


class TestTimedButtonUncertainOutcome:
    """Timed actions keep their cooldown when a write may already be active."""

    async def test_notification_and_cooldown_are_retained(
        self, make_coordinator, mock_hass, monkeypatch
    ):
        error = ScheduleWriteUnknownError("response lost")
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        coordinator.update_charge = AsyncMock(side_effect=error)
        monkeypatch.setattr(
            AlphaESSBatteryButton,
            "_notifications_disabled",
            property(lambda _self: False),
        )
        button = AlphaESSBatteryButton(
            coordinator,
            FakeEntry(),
            SERIAL,
            _description(
                SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS,
                AlphaESSNames.ButtonChargeThirty,
            ),
        )
        button.hass = mock_hass

        with pytest.raises(ScheduleWriteUnknownError, match="response lost"):
            await button.async_press()

        reserved = coordinator.last_charge_update[SERIAL]
        notification = mock_hass.services.async_call.await_args.args[2]
        assert "unknown outcome" in notification["message"]
        assert "may already be active" in notification["message"]
        assert "outcome uncertain" in notification["title"]

        # The retained cooldown now rejects the retry loudly instead of
        # absorbing the press as a silent no-op.
        with pytest.raises(HomeAssistantError, match="rate limited"):
            await button.async_press()

        assert coordinator.last_charge_update[SERIAL] == reserved
        coordinator.update_charge.assert_awaited_once()


class TestNewPeriodValidation:
    """A new period needs a complete, actionable definition before any POST."""

    @pytest.mark.parametrize(
        ("periodic", "changes", "message"),
        [
            (
                _daily_schedule(),
                {"timeChaf2": "06:00", "chargePower2": 1200},
                "both the start and end",
            ),
            (
                {
                    **_daily_schedule(),
                    "executeCycleType": 1,
                    "chargeTimeList": [
                        _period(
                            "01:00",
                            "05:00",
                            limit=90,
                            power=3000,
                            weeks=[1, 2, 3, 4, 5],
                        )
                    ],
                    "dischargeTimeList": [
                        _period(
                            "17:00",
                            "21:00",
                            limit=20,
                            power=2500,
                            weeks=[1, 2, 3, 4, 5],
                        )
                    ],
                },
                {
                    "timeChaf2": "06:00",
                    "timeChae2": "07:00",
                    "chargePower2": 1200,
                },
                "Cannot add charge period 2 to a weekly schedule",
            ),
            (
                _daily_schedule(),
                {"timeChaf2": "06:00", "timeChae2": "07:00"},
                "explicit positive power",
            ),
            (
                _daily_schedule(charge=[]),
                {
                    "timeChaf1": "06:00",
                    "timeChae1": "07:00",
                    "chargePower1": 1200,
                },
                "explicit cutoff SOC",
            ),
        ],
        ids=("incomplete-times", "weekly-new-slot", "missing-power", "missing-soc"),
    )
    async def test_incomplete_new_period_blocks_every_write(
        self, make_coordinator, mock_api, periodic, changes, message
    ):
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        with pytest.raises(ScheduleWriteError, match=message):
            await coordinator.async_write_charge_config(SERIAL, times=changes)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    @pytest.mark.parametrize(
        ("mutator", "message"),
        [
            (lambda period: period.pop("chargeLimit"), "no valid chargeLimit"),
            (
                lambda period: period.__setitem__("chargeLimit", 9),
                "accepts 10-100",
            ),
            (
                lambda period: period.__setitem__("chargePower", 0),
                "non-positive chargePower",
            ),
        ],
        ids=("missing-limit", "limit-out-of-range", "non-positive-power"),
    )
    async def test_invalid_existing_period_blocks_every_write(
        self, make_coordinator, mock_api, mutator, message
    ):
        periodic = _daily_schedule()
        mutator(periodic["chargeTimeList"][0])
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        with pytest.raises(ScheduleWriteError, match=message):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_weekly_period_without_weekdays_blocks_every_write(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        periodic["executeCycleType"] = 1
        periodic["chargeTimeList"][0]["weeks"] = [1, 2, 3]
        # Deliberately omit weeks from the existing discharge period.
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        with pytest.raises(ScheduleWriteError, match="has no weekdays"):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()


class TestHiddenPeriodPreservation:
    """SOC edits affect only slots exposed when the draft was opened."""

    @pytest.mark.parametrize(
        (
            "side",
            "list_key",
            "changes",
            "expected_limit",
            "hidden_limit",
        ),
        [
            (
                "charge",
                "chargeTimeList",
                {
                    "timeChaf1": "00:00",
                    "timeChae1": "00:00",
                    "batHighCap": 88,
                },
                88,
                95,
            ),
            (
                "discharge",
                "dischargeTimeList",
                {
                    "timeDisf1": "00:00",
                    "timeDise1": "00:00",
                    "batUseCap": 18,
                },
                18,
                25,
            ),
        ],
        ids=("charge", "discharge"),
    )
    async def test_remove_slot_one_and_soc_edit_preserve_hidden_slot_three(
        self,
        make_coordinator,
        mock_api,
        side,
        list_key,
        changes,
        expected_limit,
        hidden_limit,
    ):
        periodic = _daily_schedule(
            charge=[
                _period("01:00", "02:00", limit=80, power=1000),
                _period("03:00", "04:00", limit=85, power=1100),
                _period("05:00", "06:00", limit=95, power=1200),
            ],
            discharge=[
                _period("17:00", "18:00", limit=15, power=1300),
                _period("19:00", "20:00", limit=20, power=1400),
                _period("21:00", "22:00", limit=25, power=1500),
            ],
        )
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator.stage_schedule_change(SERIAL, **{side: changes})
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.async_apply_schedule_draft(SERIAL)

        periods = mock_api.setTimeChargeBySn.await_args.kwargs[list_key]
        assert len(periods) == 2
        assert periods[0]["chargeLimit"] == expected_limit
        assert periods[1]["chargeLimit"] == hidden_limit


class TestWriteFailureEdges:
    """Cancellation and endpoint failures remain visible to the caller."""

    async def test_public_noop_does_not_create_lock_or_call_api(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()

        await coordinator._safe_async_apply_schedule(SERIAL)

        assert SERIAL not in coordinator._schedule_locks
        mock_api.getTimeChargeBySn.assert_not_awaited()

    async def test_periodic_read_cancellation_propagates(
        self, make_coordinator, mock_api
    ):
        coordinator = _seed(
            make_coordinator(), periodic=_daily_schedule(), readable=True
        )
        mock_api.getTimeChargeBySn.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

    @pytest.mark.parametrize(
        "call_kwargs",
        [{"grid_charge": 0}, {"powers": {"chargePower1": 1200}}],
        ids=("switch", "power"),
    )
    async def test_write_denied_system_fails_closed_without_api_calls(
        self, make_coordinator, mock_api, call_kwargs
    ):
        """Write-denied is not backup mode: no store accepts the change."""
        coordinator = _seed(
            make_coordinator(), periodic=_daily_schedule(), readable=True
        )
        coordinator._periodic_write_denied.add(SERIAL)

        with pytest.raises(ScheduleWriteError, match="Schedule control requires"):
            await coordinator.async_write_charge_config(SERIAL, **call_kwargs)

        mock_api.getTimeChargeBySn.assert_not_awaited()
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_open_draft_fails_if_periodic_base_cannot_be_reread(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator.stage_schedule_change(
            SERIAL, charge={"timeChae1": "04:30"}
        )
        mock_api.getTimeChargeBySn.side_effect = _api_error(
            6042, "System is offline"
        )

        with pytest.raises(ScheduleWriteError, match="used to open this draft"):
            await coordinator.async_apply_schedule_draft(SERIAL)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_periodic_post_cancellation_propagates(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
        mock_api.setTimeChargeBySn.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

    async def test_transport_failure_on_periodic_post_reports_unknown_outcome(
        self, make_coordinator, mock_api, caplog
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
        mock_api.setTimeChargeBySn.side_effect = OSError("socket closed")

        with caplog.at_level(logging.WARNING), pytest.raises(
            ScheduleWriteUnknownError, match="unknown outcome"
        ):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.updateChargeConfigInfo.assert_not_awaited()
        assert "socket closed" in caplog.text

    async def test_locked_defensive_noop_fails_closed_without_a_store(
        self, make_coordinator
    ):
        """Write-denied has no usable store; even a defensive direct call
        must refuse rather than read as success."""
        coordinator = make_coordinator()
        coordinator._periodic_readable[SERIAL] = True
        coordinator._periodic_write_denied.add(SERIAL)

        with pytest.raises(
            ScheduleWriteError, match="Schedule control requires"
        ):
            await coordinator._safe_async_apply_schedule_locked(
                SERIAL,
                charge=None,
                discharge=None,
                expected_periodic=None,
            )


class TestDiagnosticsAndEvDiscovery:
    """Expose capability state and warn once when EV data is truncated."""

    def test_known_periodic_read_states(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator._periodic_readable["readable"] = True
        coordinator._periodic_readable["unreadable"] = False

        assert coordinator.get_periodic_read_state("readable") == PERIODIC_READ_OK
        assert (
            coordinator.get_periodic_read_state("unreadable")
            == PERIODIC_READ_UNAVAILABLE
        )

    async def test_multiple_ev_chargers_warn_once_and_use_first(
        self, make_coordinator, mock_api, caplog
    ):
        coordinator = make_coordinator()
        mock_api.getEvChargerConfigList.return_value = [
            {"evchargerSn": "EV-FIRST", "evchargerModel": "EV-A"},
            {"evchargerSn": "EV-SECOND", "evchargerModel": "EV-B"},
        ]
        mock_api.getEvChargerStatusBySn.return_value = {"evchargerStatus": 2}
        mock_api.getEvChargerCurrentsBySn.return_value = {"currentsetting": 16}

        with caplog.at_level(logging.WARNING):
            for _ in range(2):
                await coordinator._fetch_inverter_data(
                    SERIAL,
                    {"minv": "SMILE5-INV"},
                    throttle_delay=0,
                    get_ev=True,
                )

        warnings = [
            record
            for record in caplog.records
            if "currently exposes only the first charger" in record.getMessage()
        ]
        assert len(warnings) == 1
        assert SERIAL in coordinator._multi_ev_warned
        assert mock_api.getEvChargerStatusBySn.await_count == 2
        mock_api.getEvChargerStatusBySn.assert_awaited_with(SERIAL, "EV-FIRST")


class TestUpstreamPollingEdges:
    """Treat incomplete or malformed discovery responses as non-authoritative."""

    async def test_wait_for_api_interval_is_a_noop_before_first_request(
        self, make_coordinator
    ):
        coordinator = make_coordinator()

        await coordinator._async_wait_for_api_interval()

        assert coordinator._last_api_request_completed is None

    @pytest.mark.parametrize("alt", (False, True), ids=("normal", "alt"))
    async def test_none_discovery_snapshot_preserves_existing_data(
        self, make_coordinator, alt
    ):
        coordinator = make_coordinator(alt=alt)
        coordinator.data = {SERIAL: {"sentinel": True}}
        coordinator._async_get_ess_list = AsyncMock(return_value=None)

        data = await coordinator._async_update_data()

        assert data[SERIAL]["sentinel"] is True
        coordinator._async_get_ess_list.assert_awaited_once_with()

    @pytest.mark.parametrize("alt", (False, True), ids=("normal", "alt"))
    async def test_malformed_discovery_snapshot_uses_safe_fallback(
        self, make_coordinator, alt
    ):
        coordinator = make_coordinator(alt=alt)
        coordinator._async_get_ess_list = AsyncMock(
            return_value={"sysSn": SERIAL}
        )
        coordinator._fallback_to_local_data = AsyncMock(return_value={})

        assert await coordinator._async_update_data() == {}

        assert coordinator.cloud_available is False
        error = coordinator._fallback_to_local_data.await_args.args[0]
        assert isinstance(error, TypeError)
        assert "invalid response shape" in str(error)

    @pytest.mark.parametrize("alt", (False, True), ids=("normal", "alt"))
    async def test_mixed_discovery_snapshot_with_invalid_serial_fails_closed(
        self, make_coordinator, alt
    ):
        coordinator = make_coordinator(alt=alt)
        valid_unit = {"sysSn": SERIAL, "minv": "SMILE5-INV"}
        coordinator._async_get_ess_list = AsyncMock(
            return_value=[{"minv": "unknown"}, valid_unit]
        )
        coordinator._fetch_inverter_data = AsyncMock()

        data = await coordinator._async_update_data()

        assert data == {}
        assert coordinator.cloud_available is False
        coordinator._fetch_inverter_data.assert_not_awaited()

    @pytest.mark.parametrize("alt", (False, True), ids=("normal", "alt"))
    async def test_duplicate_discovery_serial_is_fetched_once(
        self, make_coordinator, alt
    ):
        coordinator = make_coordinator(alt=alt)
        first = {"sysSn": SERIAL, "minv": "stale-model"}
        current = {"sysSn": SERIAL, "minv": "SMILE5-INV"}
        coordinator._async_get_ess_list = AsyncMock(
            return_value=[first, current]
        )
        coordinator._fetch_inverter_data = AsyncMock(return_value={})
        coordinator._parse_inverter_data = lambda _payload: {
            "Model": "SMILE5-INV"
        }
        coordinator._fetch_per_inverter_local_data = AsyncMock()
        coordinator._async_resolve_unknown_periodic_read = AsyncMock()

        data = await coordinator._async_update_data()

        assert data[SERIAL]["Model"] == "SMILE5-INV"
        coordinator._fetch_inverter_data.assert_awaited_once_with(
            SERIAL,
            current,
            0.0,
            get_power=True,
            get_ev=True,
            include_local_ip=True,
        )

    async def test_fast_poll_propagates_body_auth_failure(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator(alt=True)
        coordinator._last_full_poll = float("inf")
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        mock_api.getLastPowerData.side_effect = _api_error(6007)

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()


class TestControlAvailability:
    """Control entities follow capability, parent, charger and binding state."""

    @staticmethod
    def _schedule_controls(coordinator):
        entry = FakeEntry()
        return (
            AlphaNumber(
                coordinator,
                SERIAL,
                entry,
                _description(
                    DISCHARGE_AND_CHARGE_NUMBERS, AlphaESSNames.batHighCap
                ),
            ),
            AlphaNumber(
                coordinator,
                SERIAL,
                entry,
                _description(
                    DISCHARGE_AND_CHARGE_NUMBERS, AlphaESSNames.ChargePower1
                ),
            ),
            AlphaTime(coordinator, SERIAL, entry, CHARGE_DISCHARGE_TIMES[0]),
            AlphaSwitch(coordinator, SERIAL, entry, CHARGE_DISCHARGE_SWITCHES[0]),
        )

    @pytest.mark.parametrize(
        ("readable", "periodic", "available", "power_available"),
        [
            (None, None, False, False),
            (True, None, False, False),
            # A definitive 6017 activates the legacy backup: controls come
            # back through the two-slot view, but powers stay periodic-only.
            (False, None, True, False),
            (True, _daily_schedule(), True, True),
        ],
        ids=(
            "capability-unknown",
            "periodic-base-incomplete",
            "backup-mode",
            "periodic-complete",
        ),
    )
    def test_schedule_controls_require_a_complete_known_base(
        self,
        make_coordinator,
        readable,
        periodic,
        available,
        power_available,
    ):
        coordinator = _seed(
            make_coordinator(), periodic=periodic, readable=readable
        )
        coordinator.last_update_success = True
        coordinator.cloud_available = True
        soc, power, time_entity, switch = self._schedule_controls(coordinator)

        assert coordinator.can_stage_schedule(SERIAL) is available
        assert soc.available is available
        assert power.available is power_available
        assert time_entity.available is available
        assert switch.available is available

    def test_pruned_parent_disables_schedule_and_ev_controls(
        self, make_coordinator
    ):
        coordinator = _seed(
            make_coordinator(), periodic=_daily_schedule(), readable=True
        )
        coordinator.data[SERIAL][AlphaESSNames.evchargersn] = "EV-FIRST"
        coordinator.last_update_success = True
        coordinator.cloud_available = True
        coordinator.stage_schedule_change(
            SERIAL, charge={"timeChae1": "04:30"}
        )
        entry = FakeEntry()
        soc, power, time_entity, switch = self._schedule_controls(coordinator)
        apply_button = AlphaESSBatteryButton(
            coordinator,
            entry,
            SERIAL,
            _description(
                SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS,
                AlphaESSNames.ButtonApplySchedule,
            ),
        )
        ev_button = AlphaESSBatteryButton(
            coordinator,
            entry,
            SERIAL,
            EV_DISCHARGE_AND_CHARGE_BUTTONS[0],
            ev_charger=True,
            ev_serial="EV-FIRST",
        )
        ev_number = AlphaEVNumber(
            coordinator,
            SERIAL,
            entry,
            EV_CHARGER_NUMBERS[0],
            ev_serial="EV-FIRST",
        )
        controls = (
            soc,
            power,
            time_entity,
            switch,
            apply_button,
            ev_button,
            ev_number,
        )
        assert all(control.available for control in controls)

        coordinator.data[SERIAL][AlphaESSNames.evchargersn] = "EV-REPLACED"
        assert ev_button.available is False
        assert ev_number.available is False
        coordinator.data[SERIAL][AlphaESSNames.evchargersn] = "EV-FIRST"
        assert ev_button.available is True
        assert ev_number.available is True

        coordinator._prune_unbound_systems(set())

        assert SERIAL not in coordinator.data
        assert all(not control.available for control in controls)


class TestLegacyBackupWrites:
    """In backup mode the two stores are independent resources, so a two-sided
    write can half-land. Every one of those outcomes has to be reported."""

    def _seed_backup(self, coordinator, mock_api, *, charge=None, discharge=None):
        charge = deepcopy(charge or _legacy_charge())
        discharge = deepcopy(discharge or _legacy_discharge())
        coordinator.data = {SERIAL: _entity_data(charge, discharge)}
        coordinator._periodic_readable[SERIAL] = False
        coordinator._legacy_schedules[SERIAL] = {
            "charge": deepcopy(charge),
            "discharge": deepcopy(discharge),
        }
        mock_api.getChargeConfigInfo.return_value = deepcopy(charge)
        mock_api.getDisChargeConfigInfo.return_value = deepcopy(discharge)
        return coordinator

    async def test_an_unreadable_side_blocks_the_write(
        self, make_coordinator, mock_api
    ):
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        mock_api.getChargeConfigInfo.side_effect = OSError("boom")

        with pytest.raises(ScheduleWriteError, match="nothing was changed"):
            await coordinator.async_write_charge_config(
                SERIAL, times={"timeChaf1": "02:00"}
            )

        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_a_side_the_api_did_not_return_blocks_the_write(
        self, make_coordinator, mock_api
    ):
        """A replacement built from a payload that is not there would reset
        whatever it could not see."""
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        mock_api.getChargeConfigInfo.return_value = None

        with pytest.raises(
            ScheduleWriteError, match="full current configuration could not be read"
        ):
            await coordinator.async_write_charge_config(
                SERIAL, times={"timeChaf1": "02:00"}
            )

        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_a_side_missing_fields_names_them(self, make_coordinator, mock_api):
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        partial = _legacy_charge()
        del partial["timeChae2"]
        mock_api.getChargeConfigInfo.return_value = partial

        with pytest.raises(ScheduleWriteError, match="timeChae2"):
            await coordinator.async_write_charge_config(
                SERIAL, times={"timeChaf1": "02:00"}
            )

        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_a_side_that_already_matches_is_not_rewritten(
        self, make_coordinator, mock_api
    ):
        """Reset zeroes both stores; a store already at those values costs no
        write, and a request never sent is a request that cannot fail."""
        zeroed = _legacy_charge(begin1="00:00", end1="00:00")
        coordinator = self._seed_backup(make_coordinator(), mock_api, charge=zeroed)

        await coordinator.reset_config(SERIAL)

        mock_api.updateChargeConfigInfo.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_awaited_once()

    async def test_a_backup_store_that_moved_is_a_conflict(
        self, make_coordinator, mock_api
    ):
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})
        # Somebody edited the same store in the AlphaESS app in the meantime.
        mock_api.getChargeConfigInfo.return_value = _legacy_charge(begin1="09:00")

        with pytest.raises(ScheduleConflictError, match="changed after this draft"):
            await coordinator.async_apply_schedule_draft(SERIAL)

        mock_api.updateChargeConfigInfo.assert_not_awaited()
        assert coordinator.has_schedule_draft(SERIAL)

    async def test_a_half_landed_write_names_what_applied(
        self, make_coordinator, mock_api
    ):
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})
        coordinator.stage_schedule_change(SERIAL, discharge={"timeDisf1": "18:00"})
        mock_api.updateDisChargeConfigInfo.side_effect = _api_error(6008, "Set failed")

        with pytest.raises(SchedulePartialWriteError, match="charge"):
            await coordinator.async_apply_schedule_draft(SERIAL)

        mock_api.updateChargeConfigInfo.assert_awaited_once()
        # The draft survives for a retry, rebased on what the API took.
        assert coordinator.has_schedule_draft(SERIAL)
        assert (
            coordinator._schedule_draft_base_legacy[SERIAL]["charge"]["timeChaf1"]
            == "02:00"
        )

    async def test_a_half_landed_write_with_a_lost_response_says_so(
        self, make_coordinator, mock_api
    ):
        """A timeout is not a rejection: the store may already hold the change."""
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        mock_api.updateDisChargeConfigInfo.side_effect = TimeoutError("no response")

        with pytest.raises(SchedulePartialWriteUnknownError, match="unknown outcome"):
            await coordinator.reset_config(SERIAL)

    async def test_a_rejected_single_write_reports_that_nothing_landed(
        self, make_coordinator, mock_api
    ):
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        mock_api.updateChargeConfigInfo.side_effect = _api_error(6008, "Set failed")

        with pytest.raises(ScheduleWriteError, match="No AlphaESS schedule store"):
            await coordinator.async_write_charge_config(
                SERIAL, times={"timeChaf1": "02:00"}
            )

    async def test_a_lost_single_write_reports_an_unknown_outcome(
        self, make_coordinator, mock_api
    ):
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        mock_api.updateChargeConfigInfo.side_effect = TimeoutError("no response")

        with pytest.raises(ScheduleWriteUnknownError, match="known to have accepted"):
            await coordinator.async_write_charge_config(
                SERIAL, times={"timeChaf1": "02:00"}
            )

    async def test_a_draft_opened_on_the_backup_is_refused_once_periodic_returns(
        self, make_coordinator, mock_api
    ):
        """AlphaESS granting the permission mid-draft changes which resource
        the staged values describe."""
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})

        periodic = _daily_schedule(charge=[_period("01:00", "05:00")])
        coordinator._periodic_readable[SERIAL] = True
        coordinator._periodic_schedules[SERIAL] = deepcopy(periodic)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        with pytest.raises(ScheduleConflictError, match="became available"):
            await coordinator.async_apply_schedule_draft(SERIAL)

        mock_api.setTimeChargeBySn.assert_not_awaited()


class TestRemainingWriteGuards:
    """The refusals and bookkeeping that only show up in narrow situations."""

    def _seed_backup(self, coordinator, mock_api, *, charge=None, discharge=None):
        charge = deepcopy(charge or _legacy_charge())
        discharge = deepcopy(discharge or _legacy_discharge())
        coordinator.data = {SERIAL: _entity_data(charge, discharge)}
        coordinator._periodic_readable[SERIAL] = False
        coordinator._legacy_schedules[SERIAL] = {
            "charge": deepcopy(charge),
            "discharge": deepcopy(discharge),
        }
        mock_api.getChargeConfigInfo.return_value = deepcopy(charge)
        mock_api.getDisChargeConfigInfo.return_value = deepcopy(discharge)
        return coordinator

    async def test_pruning_drops_an_idle_write_lock(self, make_coordinator):
        """A held lock must survive pruning: popping it would let a second
        lock be created beside the one a transaction is still holding."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}, "AL_OTHER": {}}
        idle = asyncio.Lock()
        held = asyncio.Lock()
        coordinator._schedule_locks[SERIAL] = idle
        coordinator._schedule_locks["AL_OTHER"] = held

        async with held:
            coordinator._prune_unbound_systems(set())

        assert SERIAL not in coordinator._schedule_locks
        assert coordinator._schedule_locks["AL_OTHER"] is held

    def test_staging_needs_a_known_inverter(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {}

        assert coordinator.can_stage_schedule(SERIAL) is False

    def test_power_cannot_be_staged_in_backup_mode(self, make_coordinator, mock_api):
        """The legacy stores have no power field, so a staged power could only
        be silently dropped at Apply."""
        coordinator = self._seed_backup(make_coordinator(), mock_api)

        with pytest.raises(ScheduleWriteError, match="no power field"):
            coordinator.stage_schedule_change(SERIAL, charge={"chargePower1": 2500})

        assert not coordinator.has_schedule_draft(SERIAL)

    def test_a_legacy_poll_that_started_before_a_write_is_ignored(
        self, make_coordinator, mock_api
    ):
        """An in-flight poll carries pre-write values; caching them would
        undo the write in the entity view."""
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        stale = _entity_data(
            _legacy_charge(begin1="23:00"), _legacy_discharge()
        )
        coordinator._schedule_write_revisions[SERIAL] = 4

        coordinator._cache_legacy_state(SERIAL, stale, expected_revision=3)

        assert (
            coordinator._legacy_schedules[SERIAL]["charge"]["timeChaf1"] == "01:00"
        )

    async def test_a_cancelled_legacy_read_is_not_reported_as_a_write_failure(
        self, make_coordinator, mock_api
    ):
        """Shutdown cancelling the task is not the API refusing the read."""
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        mock_api.getChargeConfigInfo.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await coordinator.async_write_charge_config(
                SERIAL, times={"timeChaf1": "02:00"}
            )

    async def test_a_cancelled_legacy_write_propagates(
        self, make_coordinator, mock_api
    ):
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        mock_api.updateChargeConfigInfo.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await coordinator.async_write_charge_config(
                SERIAL, times={"timeChaf1": "02:00"}
            )

    async def test_an_edit_during_apply_rebases_the_backup_draft(
        self, make_coordinator, mock_api
    ):
        """The newer edit is kept, and its conflict base becomes what this
        transaction just committed."""
        coordinator = self._seed_backup(make_coordinator(), mock_api)
        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})

        def _edit_mid_write(**_kwargs):
            coordinator.stage_schedule_change(SERIAL, charge={"timeChae1": "06:00"})
            return {}

        mock_api.updateChargeConfigInfo.side_effect = _edit_mid_write

        await coordinator.async_apply_schedule_draft(SERIAL)

        assert coordinator.has_schedule_draft(SERIAL)
        assert SERIAL not in coordinator._schedule_draft_base_periodic
        assert (
            coordinator._schedule_draft_base_legacy[SERIAL]["charge"]["timeChaf1"]
            == "02:00"
        )

    async def test_the_charge_service_will_not_disable_the_last_timer(
        self, make_coordinator, mock_api
    ):
        """0/0 is indistinguishable from a self-consumption mode afterwards."""
        coordinator = self._seed_backup(
            make_coordinator(), mock_api, discharge=_legacy_discharge(enabled=0)
        )

        with pytest.raises(ScheduleWriteError, match="last enabled timer"):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_the_discharge_service_will_not_disable_the_last_timer(
        self, make_coordinator, mock_api
    ):
        coordinator = self._seed_backup(
            make_coordinator(), mock_api, charge=_legacy_charge(enabled=0)
        )

        with pytest.raises(ScheduleWriteError, match="last enabled timer"):
            await coordinator.async_write_discharge_config(SERIAL, ctr_dis=0)

        mock_api.updateDisChargeConfigInfo.assert_not_awaited()

    def test_a_quick_button_will_not_create_a_period_without_a_cutoff(
        self, make_coordinator
    ):
        """With no period to copy a cutoff from, inventing one would write a
        SOC limit nobody chose."""
        coordinator = make_coordinator()
        schedule = _daily_schedule(
            charge=[], discharge=[_period("17:00", "21:00", limit=20, power=2500)]
        )

        with pytest.raises(ScheduleWriteError, match="without a cutoff SOC"):
            coordinator._patch_periodic_schedule(
                SERIAL,
                schedule,
                charge={
                    "timeChaf1": "01:00",
                    "timeChae1": "02:00",
                    "chargePower1": 2000,
                },
                discharge=None,
                now_window=True,
            )
