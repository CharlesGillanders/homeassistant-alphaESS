"""Tests for the periodic (setTimeChargeBySn) charge/discharge schedule.

Covers the dual-write behaviour added for issues #267 and #269: every schedule
change is pushed to the periodic API first (the only one migrated AlphaESS
backends act on) and then to the legacy two-slot endpoints — unconditionally,
because which backend a system is on cannot be detected up front.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.alphaess.coordinator import (
    PERIODIC_DAILY,
    PERIODIC_READ_OK,
    PERIODIC_READ_UNAVAILABLE,
    PERIODIC_READ_UNKNOWN,
)
from custom_components.alphaess.enums import AlphaESSNames

SERIAL = "ALP123456"


def _period(begin, end):
    return {"beginTime": begin, "endTime": end, "chargeLimit": 90}


def _schedule_data(**overrides):
    """Coordinator data for a system with one charge and one discharge window."""
    data = {
        "gridCharge": 1,
        AlphaESSNames.batHighCap: 90,
        "charge_timeChaf1": "01:00",
        "charge_timeChae1": "05:00",
        "charge_timeChaf2": "00:00",
        "charge_timeChae2": "00:00",
        "ctrDis": 1,
        AlphaESSNames.batUseCap: 20,
        "discharge_timeDisf1": "17:00",
        "discharge_timeDise1": "21:00",
        "discharge_timeDisf2": "00:00",
        "discharge_timeDise2": "00:00",
    }
    data.update(overrides)
    return data


@pytest.fixture
def readable(make_coordinator, mock_api):
    """A coordinator whose system returns a periodic schedule when read."""
    coordinator = make_coordinator()
    coordinator.data = {SERIAL: _schedule_data()}
    coordinator._periodic_readable[SERIAL] = True
    return coordinator


@pytest.fixture
def unreadable(make_coordinator, mock_api):
    """A coordinator whose read came back 6017 "no operation permissions"."""
    coordinator = make_coordinator()
    coordinator.data = {SERIAL: _schedule_data()}
    coordinator._periodic_readable[SERIAL] = False
    return coordinator


class TestBuildPeriodList:
    def test_drops_disabled_slots(self, readable):
        periods = readable._build_period_list(
            [("01:00", "05:00"), ("00:00", "00:00")], 90,
        )
        assert periods == [{"beginTime": "01:00", "endTime": "05:00", "chargeLimit": 90}]

    def test_all_disabled_gives_empty_list(self, readable):
        periods = readable._build_period_list(
            [("00:00", "00:00"), ("12:00", "12:00")], 90,
        )
        assert periods == []

    def test_missing_times_are_skipped(self, readable):
        assert readable._build_period_list([(None, "05:00"), ("01:00", None)], 90) == []

    def test_charge_limit_clamped_to_minimum(self, readable):
        # The number entity allows 0-100 but the periodic API rejects <10 (6001).
        periods = readable._build_period_list([("17:00", "21:00")], 5)
        assert periods[0]["chargeLimit"] == 10

    def test_charge_limit_clamped_to_maximum(self, readable):
        periods = readable._build_period_list([("01:00", "05:00")], 150)
        assert periods[0]["chargeLimit"] == 100

    def test_invalid_charge_limit_falls_back_to_minimum(self, readable):
        periods = readable._build_period_list([("01:00", "05:00")], None)
        assert periods[0]["chargeLimit"] == 10

    def test_charge_power_included_when_known(self, readable):
        periods = readable._build_period_list([("01:00", "05:00")], 90, 5000)
        assert periods[0]["chargePower"] == 5000

    def test_charge_power_omitted_when_unknown(self, readable):
        periods = readable._build_period_list([("01:00", "05:00")], 90)
        assert "chargePower" not in periods[0]


class TestChargePower:
    """A period with no chargePower is accepted and then ignored.

    Reported on #269: the schedule appeared in the AlphaESS portal but the
    battery did nothing, because the entry carried no rate to run at.
    """

    async def test_rated_power_is_sent_when_nothing_is_cached(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data(**{AlphaESSNames.poinv: 5.0})}
        coordinator._periodic_readable[SERIAL] = True

        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)

        _, _, charge_list, discharge_list = mock_api.setTimeChargeBySn.await_args.args
        assert charge_list[0]["chargePower"] == 5000
        assert discharge_list[0]["chargePower"] == 5000

    async def test_a_cached_setpoint_wins_over_the_rating(
        self, make_coordinator, mock_api
    ):
        """Don't trample a power the owner set in the AlphaESS app."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data(**{AlphaESSNames.poinv: 5.0})}
        coordinator._periodic_readable[SERIAL] = True
        coordinator._periodic_power[SERIAL] = {"charge": 3000}

        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)

        _, _, charge_list, discharge_list = mock_api.setTimeChargeBySn.await_args.args
        assert charge_list[0]["chargePower"] == 3000
        assert discharge_list[0]["chargePower"] == 5000

    async def test_omitted_when_the_rating_is_unknown(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        coordinator._periodic_readable[SERIAL] = True

        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)

        _, _, charge_list, _ = mock_api.setTimeChargeBySn.await_args.args
        assert "chargePower" not in charge_list[0]

    def test_empty_period_list_renders_as_none(self):
        from custom_components.alphaess.coordinator import _format_periods

        assert _format_periods([]) == "none"
        assert _format_periods([_period("01:00", "05:00")]) == "01:00-05:00"

    def test_rating_conversion(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.poinv: 5.0}}
        assert coordinator._default_charge_power(SERIAL) == 5000

        coordinator.data = {SERIAL: {AlphaESSNames.poinv: "10"}}
        assert coordinator._default_charge_power(SERIAL) == 10000

        coordinator.data = {SERIAL: {AlphaESSNames.poinv: None}}
        assert coordinator._default_charge_power(SERIAL) is None

        coordinator.data = {SERIAL: {AlphaESSNames.poinv: 0}}
        assert coordinator._default_charge_power(SERIAL) is None


class TestProbe:
    async def test_payload_marks_supported(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        mock_api.getTimeChargeBySn.return_value = {
            "chargeTimeList": [{"beginTime": "01:00", "chargePower": 5000}],
            "dischargeTimeList": [],
        }

        assert await coordinator.async_probe_periodic_readable(SERIAL) is True
        assert coordinator._periodic_readable[SERIAL] is True
        # chargePower is remembered so our writes don't wipe the app's setting.
        assert coordinator._periodic_power[SERIAL]["charge"] == 5000

    async def test_none_marks_unsupported(self, make_coordinator, mock_api):
        # The library returns None for 6017 "no operation permissions".
        mock_api.getTimeChargeBySn.return_value = None
        coordinator = make_coordinator()

        assert await coordinator.async_probe_periodic_readable(SERIAL) is False
        assert coordinator._periodic_readable[SERIAL] is False

    async def test_transport_error_leaves_state_unknown(self, make_coordinator, mock_api):
        mock_api.getTimeChargeBySn.side_effect = OSError("connection reset")
        coordinator = make_coordinator()

        assert await coordinator.async_probe_periodic_readable(SERIAL) is None
        # Nothing cached, so the next write retries rather than giving up.
        assert SERIAL not in coordinator._periodic_readable


class TestDualWrite:
    async def test_periodic_written_before_legacy(self, readable, mock_api):
        order = []
        mock_api.setTimeChargeBySn = AsyncMock(side_effect=lambda *a, **k: order.append("periodic"))
        mock_api.updateChargeConfigInfo = AsyncMock(side_effect=lambda *a, **k: order.append("legacy"))

        await readable.async_write_charge_config(SERIAL, times={"timeChaf1": "02:00"})

        assert order == ["periodic", "legacy"]

    async def test_periodic_payload_is_daily_with_no_weeks(self, readable, mock_api):
        await readable.async_write_charge_config(SERIAL, times={"timeChaf1": "02:00"})

        args, kwargs = mock_api.setTimeChargeBySn.await_args
        serial, cycle_type, charge_list, discharge_list = args
        assert serial == SERIAL
        assert cycle_type == PERIODIC_DAILY == 0
        assert charge_list == [{"beginTime": "02:00", "endTime": "05:00", "chargeLimit": 90}]
        assert discharge_list == [{"beginTime": "17:00", "endTime": "21:00", "chargeLimit": 20}]
        assert all("weeks" not in period for period in charge_list + discharge_list)
        assert kwargs == {"gridChargeCycle": 1, "ctrDisCycle": 1}

    async def test_both_lists_always_sent(self, readable, mock_api):
        """A charge-only edit must still carry the current discharge periods."""
        await readable.async_write_charge_config(SERIAL, bat_high_cap=80)

        _, _, charge_list, discharge_list = mock_api.setTimeChargeBySn.await_args.args
        assert charge_list and discharge_list

    async def test_skipped_when_a_list_would_be_empty(
        self, make_coordinator, mock_api, caplog
    ):
        """The live API rejects an empty list with 6001 "time list is null"."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data(
            discharge_timeDisf1="00:00", discharge_timeDise1="00:00",
        )}
        coordinator._periodic_readable[SERIAL] = True

        await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_awaited_once()
        assert "the discharge list is empty" in caplog.text

    async def test_skipped_when_charge_list_would_be_empty(
        self, make_coordinator, mock_api, caplog
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data(
            charge_timeChaf1="00:00", charge_timeChae1="00:00",
        )}
        coordinator._periodic_readable[SERIAL] = True

        await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        assert "the charge list is empty" in caplog.text

    async def test_legacy_argument_order_unchanged(self, readable, mock_api):
        """The legacy call must keep passing end times before start times."""
        await readable.async_write_charge_config(SERIAL, times={"timeChaf1": "02:00"})

        args = mock_api.updateChargeConfigInfo.await_args.args
        # (serial, batHighCap, gridCharge, timeChae1, timeChae2, timeChaf1, timeChaf2)
        assert args == (SERIAL, 90, 1, "05:00", "00:00", "02:00", "00:00")

    async def test_discharge_legacy_argument_order_unchanged(self, readable, mock_api):
        await readable.async_write_discharge_config(SERIAL, ctr_dis=0)

        args = mock_api.updateDisChargeConfigInfo.await_args.args
        # (serial, batUseCap, ctrDis, timeDise1, timeDise2, timeDisf1, timeDisf2)
        assert args == (SERIAL, 20, 0, "21:00", "00:00", "17:00", "00:00")

    async def test_null_config_values_fall_back_to_defaults(self, make_coordinator, mock_api):
        """The parsers store None when the API omits a field; don't send that on."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data(
            gridCharge=None, ctrDis=None,
            **{AlphaESSNames.batHighCap: None, AlphaESSNames.batUseCap: None},
        )}
        coordinator._periodic_readable[SERIAL] = True

        await coordinator.async_write_charge_config(SERIAL, times={"timeChaf1": "02:00"})

        kwargs = mock_api.setTimeChargeBySn.await_args.kwargs
        assert kwargs == {"gridChargeCycle": 1, "ctrDisCycle": 1}
        args = mock_api.updateChargeConfigInfo.await_args.args
        assert args[1:3] == (90, 1)

    async def test_disabled_switch_value_of_zero_is_preserved(self, readable, mock_api):
        """0 is a valid gridCharge/ctrDis value and must not become the default 1."""
        await readable.async_write_charge_config(SERIAL, grid_charge=0)

        assert mock_api.setTimeChargeBySn.await_args.kwargs["gridChargeCycle"] == 0
        assert mock_api.updateChargeConfigInfo.await_args.args[2] == 0

    async def test_only_the_changed_side_writes_legacy(self, readable, mock_api):
        await readable.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.updateChargeConfigInfo.assert_awaited_once()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()


class TestWriteIsNeverGated:
    """The periodic write must not depend on the read endpoint.

    getTimeChargeBySn is separately permissioned and returns 6017 on exactly
    the accounts that need the periodic write most, so gating on it would skip
    the write for the users this is meant to fix (issues #267, #269).
    """

    async def test_written_even_when_schedule_is_unreadable(self, unreadable, mock_api):
        await unreadable.async_write_charge_config(SERIAL, times={"timeChaf1": "02:00"})

        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.updateChargeConfigInfo.assert_awaited_once()

    async def test_written_even_when_read_state_is_unknown(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}

        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.updateChargeConfigInfo.assert_awaited_once()

    async def test_write_does_not_trigger_a_read(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}

        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)

        mock_api.getTimeChargeBySn.assert_not_awaited()


class TestFailureHandling:
    async def test_periodic_failure_falls_through_to_legacy(
        self, readable, mock_api, caplog
    ):
        """A failed periodic write must not abandon the legacy one."""
        mock_api.setTimeChargeBySn.side_effect = OSError("api down")

        await readable.async_write_charge_config(SERIAL, grid_charge=1)

        mock_api.updateChargeConfigInfo.assert_awaited_once()
        assert "Periodic schedule write failed" in caplog.text

    async def test_fails_only_when_both_writes_fail(self, readable, mock_api):
        mock_api.setTimeChargeBySn.side_effect = OSError("api down")
        mock_api.updateChargeConfigInfo.side_effect = OSError("api down")

        with pytest.raises(OSError):
            await readable.async_write_charge_config(SERIAL, grid_charge=1)

    async def test_legacy_failure_swallowed_after_periodic_success(
        self, readable, mock_api, caplog
    ):
        """The periodic request went through — don't fail the whole write."""
        mock_api.updateChargeConfigInfo.side_effect = OSError("api down")

        await readable.async_write_charge_config(SERIAL, grid_charge=1)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        assert "periodic schedule request went through" in caplog.text

    async def test_overlapping_periods_are_sent_and_let_the_api_decide(
        self, make_coordinator, mock_api
    ):
        """Don't pre-judge overlap.

        Guessing which combinations the API dislikes blocked writes that would
        have been accepted -- a wrap-around window such as 13:30-02:45 spans
        most of the day and collides with everything, so nothing could ever be
        written. The API knows its own rules; ask it (issue #269).
        """
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data(
            charge_timeChaf1="01:00", charge_timeChae1="18:00",
        )}
        coordinator._periodic_readable[SERIAL] = True

        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)

        mock_api.setTimeChargeBySn.assert_awaited_once()

    async def test_wraparound_window_is_still_sent(self, make_coordinator, mock_api):
        """13:30-02:45 is the exact window that used to block every write."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data(
            charge_timeChaf1="13:30", charge_timeChae1="02:45",
        )}
        coordinator._periodic_readable[SERIAL] = True

        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)

        _, _, charge_list, _ = mock_api.setTimeChargeBySn.await_args.args
        assert charge_list[0]["beginTime"] == "13:30"
        assert charge_list[0]["endTime"] == "02:45"

    async def test_api_reported_overlap_is_explained(
        self, readable, mock_api, caplog
    ):
        from alphaess.alphaess import AlphaESSApiError

        from custom_components.alphaess.coordinator import PERIODIC_OVERLAP

        mock_api.setTimeChargeBySn.side_effect = AlphaESSApiError(
            code=PERIODIC_OVERLAP, description="Set failed")

        await readable.async_write_charge_config(SERIAL, grid_charge=1)

        assert "overlap" in caplog.text
        # The periods actually sent are named, so the report is actionable.
        assert "01:00-05:00" in caplog.text
        mock_api.updateChargeConfigInfo.assert_awaited_once()

    async def test_cancellation_during_periodic_write_is_not_swallowed(
        self, readable, mock_api
    ):
        """Shutdown must cancel the write, not fall through to the legacy call."""
        mock_api.setTimeChargeBySn.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await readable.async_write_charge_config(SERIAL, grid_charge=1)

        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_cancellation_during_legacy_write_is_not_swallowed(
        self, readable, mock_api
    ):
        """Shutdown must cancel the write, not be logged as a legacy failure."""
        mock_api.updateChargeConfigInfo.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await readable.async_write_charge_config(SERIAL, grid_charge=1)

    async def test_cancellation_during_probe_is_not_swallowed(
        self, make_coordinator, mock_api
    ):
        mock_api.getTimeChargeBySn.side_effect = asyncio.CancelledError()
        coordinator = make_coordinator()

        with pytest.raises(asyncio.CancelledError):
            await coordinator.async_probe_periodic_readable(SERIAL)

    async def test_skipped_periodic_still_surfaces_legacy_errors(
        self, make_coordinator, mock_api
    ):
        """When periodic is skipped, legacy is primary again, errors included."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data(
            discharge_timeDisf1="00:00", discharge_timeDise1="00:00",
        )}
        coordinator._periodic_readable[SERIAL] = True
        mock_api.updateChargeConfigInfo.side_effect = OSError("api down")

        with pytest.raises(OSError):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=1)


class TestPeriodicScheduleReadDiagnostic:
    def test_reports_periodic_when_supported(self, readable):
        assert readable.get_periodic_read_state(SERIAL) == PERIODIC_READ_OK

    def test_reports_legacy_when_not_entitled(self, unreadable):
        assert unreadable.get_periodic_read_state(SERIAL) == PERIODIC_READ_UNAVAILABLE

    def test_reports_unknown_before_the_probe_answers(self, make_coordinator):
        assert make_coordinator().get_periodic_read_state(SERIAL) == PERIODIC_READ_UNKNOWN

    def test_published_into_coordinator_data(self, readable):
        readable._update_diagnostics()
        assert readable.data[SERIAL][AlphaESSNames.PeriodicScheduleRead] == PERIODIC_READ_OK

    async def test_unknown_support_is_retried(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        mock_api.getTimeChargeBySn.return_value = {"chargeTimeList": [], "dischargeTimeList": []}

        await coordinator._async_resolve_unknown_periodic_read()

        mock_api.getTimeChargeBySn.assert_awaited_once_with(SERIAL)
        assert coordinator.get_periodic_read_state(SERIAL) == PERIODIC_READ_OK

    async def test_known_support_is_not_reprobed(self, readable, mock_api):
        await readable._async_resolve_unknown_periodic_read()

        mock_api.getTimeChargeBySn.assert_not_awaited()


class TestButtonAndResetPaths:
    async def test_reset_cannot_clear_the_periodic_schedule(self, readable, mock_api):
        """Reset zeroes every slot, which leaves no periods to send.

        setTimeChargeBySn has no representation for "no periods", so a reset
        only reaches the legacy endpoints. Clearing a periodic schedule has to
        be done from the AlphaESS app.
        """
        await readable.reset_config(SERIAL)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_awaited_once()
        mock_api.updateDisChargeConfigInfo.assert_awaited_once()

    async def test_update_charge_writes_both_apis(self, readable, mock_api):
        await readable.update_charge("batHighCap", SERIAL, 60)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.updateChargeConfigInfo.assert_awaited_once()

    async def test_update_discharge_writes_both_apis(self, readable, mock_api):
        await readable.update_discharge("batUseCap", SERIAL, 30)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.updateDisChargeConfigInfo.assert_awaited_once()
