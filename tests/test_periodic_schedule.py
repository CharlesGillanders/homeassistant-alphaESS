"""Tests for the periodic (setTimeChargeBySn) charge/discharge schedule.

Covers the dual-write behaviour added for issues #267 and #269: every schedule
change is pushed to the periodic API first (the only one migrated AlphaESS
backends act on) and then to the legacy two-slot endpoints.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.alphaess.coordinator import (
    PERIODIC_DAILY,
    SCHEDULING_API_LEGACY,
    SCHEDULING_API_PERIODIC,
    SCHEDULING_API_UNKNOWN,
    find_overlapping_periods,
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
def supported(make_coordinator, mock_api):
    """A coordinator whose system is entitled to the periodic API."""
    coordinator = make_coordinator()
    coordinator.data = {SERIAL: _schedule_data()}
    coordinator._periodic_support[SERIAL] = True
    return coordinator


@pytest.fixture
def unsupported(make_coordinator, mock_api):
    """A coordinator whose system returned 6017 (feature not entitled)."""
    coordinator = make_coordinator()
    coordinator.data = {SERIAL: _schedule_data()}
    coordinator._periodic_support[SERIAL] = False
    return coordinator


class TestOverlapDetection:
    def test_no_overlap(self):
        assert find_overlapping_periods([_period("01:00", "05:00")],
                                        [_period("17:00", "21:00")]) is None

    def test_touching_edges_do_not_overlap(self):
        assert find_overlapping_periods([_period("01:00", "05:00")],
                                        [_period("05:00", "07:00")]) is None

    def test_plain_overlap(self):
        assert find_overlapping_periods([_period("01:00", "06:00")],
                                        [_period("05:00", "07:00")]) is not None

    def test_overnight_charge_overlaps_early_discharge(self):
        # 23:00-02:00 wraps midnight and must still be caught.
        assert find_overlapping_periods([_period("23:00", "02:00")],
                                        [_period("01:00", "03:00")]) is not None

    def test_overnight_without_overlap(self):
        assert find_overlapping_periods([_period("23:00", "02:00")],
                                        [_period("10:00", "12:00")]) is None

    def test_empty_lists(self):
        assert find_overlapping_periods([], []) is None


class TestBuildPeriodList:
    def test_drops_disabled_slots(self, supported):
        periods = supported._build_period_list(
            [("01:00", "05:00"), ("00:00", "00:00")], 90,
        )
        assert periods == [{"beginTime": "01:00", "endTime": "05:00", "chargeLimit": 90}]

    def test_all_disabled_gives_empty_list(self, supported):
        periods = supported._build_period_list(
            [("00:00", "00:00"), ("12:00", "12:00")], 90,
        )
        assert periods == []

    def test_missing_times_are_skipped(self, supported):
        assert supported._build_period_list([(None, "05:00"), ("01:00", None)], 90) == []

    def test_charge_limit_clamped_to_minimum(self, supported):
        # The number entity allows 0-100 but the periodic API rejects <10 (6001).
        periods = supported._build_period_list([("17:00", "21:00")], 5)
        assert periods[0]["chargeLimit"] == 10

    def test_charge_limit_clamped_to_maximum(self, supported):
        periods = supported._build_period_list([("01:00", "05:00")], 150)
        assert periods[0]["chargeLimit"] == 100

    def test_invalid_charge_limit_falls_back_to_minimum(self, supported):
        periods = supported._build_period_list([("01:00", "05:00")], None)
        assert periods[0]["chargeLimit"] == 10

    def test_charge_power_included_when_known(self, supported):
        periods = supported._build_period_list([("01:00", "05:00")], 90, 5000)
        assert periods[0]["chargePower"] == 5000

    def test_charge_power_omitted_when_unknown(self, supported):
        periods = supported._build_period_list([("01:00", "05:00")], 90)
        assert "chargePower" not in periods[0]


class TestProbe:
    async def test_payload_marks_supported(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        mock_api.getTimeChargeBySn.return_value = {
            "chargeTimeList": [{"beginTime": "01:00", "chargePower": 5000}],
            "dischargeTimeList": [],
        }

        assert await coordinator.async_probe_periodic_support(SERIAL) is True
        assert coordinator._periodic_support[SERIAL] is True
        # chargePower is remembered so our writes don't wipe the app's setting.
        assert coordinator._periodic_power[SERIAL]["charge"] == 5000

    async def test_none_marks_unsupported(self, make_coordinator, mock_api):
        # The library returns None for 6017 "no operation permissions".
        mock_api.getTimeChargeBySn.return_value = None
        coordinator = make_coordinator()

        assert await coordinator.async_probe_periodic_support(SERIAL) is False
        assert coordinator._periodic_support[SERIAL] is False

    async def test_transport_error_leaves_state_unknown(self, make_coordinator, mock_api):
        mock_api.getTimeChargeBySn.side_effect = OSError("connection reset")
        coordinator = make_coordinator()

        assert await coordinator.async_probe_periodic_support(SERIAL) is None
        # Nothing cached, so the next write retries rather than giving up.
        assert SERIAL not in coordinator._periodic_support


class TestDualWrite:
    async def test_periodic_written_before_legacy(self, supported, mock_api):
        order = []
        mock_api.setTimeChargeBySn = AsyncMock(side_effect=lambda *a, **k: order.append("periodic"))
        mock_api.updateChargeConfigInfo = AsyncMock(side_effect=lambda *a, **k: order.append("legacy"))

        await supported.async_write_charge_config(SERIAL, times={"timeChaf1": "02:00"})

        assert order == ["periodic", "legacy"]

    async def test_periodic_payload_is_daily_with_no_weeks(self, supported, mock_api):
        await supported.async_write_charge_config(SERIAL, times={"timeChaf1": "02:00"})

        args, kwargs = mock_api.setTimeChargeBySn.await_args
        serial, cycle_type, charge_list, discharge_list = args
        assert serial == SERIAL
        assert cycle_type == PERIODIC_DAILY == 0
        assert charge_list == [{"beginTime": "02:00", "endTime": "05:00", "chargeLimit": 90}]
        assert discharge_list == [{"beginTime": "17:00", "endTime": "21:00", "chargeLimit": 20}]
        assert all("weeks" not in period for period in charge_list + discharge_list)
        assert kwargs == {"gridChargeCycle": 1, "ctrDisCycle": 1}

    async def test_both_lists_always_sent(self, supported, mock_api):
        """A charge-only edit must still carry the current discharge periods."""
        await supported.async_write_charge_config(SERIAL, bat_high_cap=80)

        _, _, charge_list, discharge_list = mock_api.setTimeChargeBySn.await_args.args
        assert charge_list and discharge_list

    async def test_empty_lists_sent_not_null(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data(
            charge_timeChaf1="00:00", charge_timeChae1="00:00",
            discharge_timeDisf1="00:00", discharge_timeDise1="00:00",
        )}
        coordinator._periodic_support[SERIAL] = True

        await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        _, _, charge_list, discharge_list = mock_api.setTimeChargeBySn.await_args.args
        assert charge_list == []
        assert discharge_list == []

    async def test_legacy_argument_order_unchanged(self, supported, mock_api):
        """The legacy call must keep passing end times before start times."""
        await supported.async_write_charge_config(SERIAL, times={"timeChaf1": "02:00"})

        args = mock_api.updateChargeConfigInfo.await_args.args
        # (serial, batHighCap, gridCharge, timeChae1, timeChae2, timeChaf1, timeChaf2)
        assert args == (SERIAL, 90, 1, "05:00", "00:00", "02:00", "00:00")

    async def test_discharge_legacy_argument_order_unchanged(self, supported, mock_api):
        await supported.async_write_discharge_config(SERIAL, ctr_dis=0)

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
        coordinator._periodic_support[SERIAL] = True

        await coordinator.async_write_charge_config(SERIAL, times={"timeChaf1": "02:00"})

        kwargs = mock_api.setTimeChargeBySn.await_args.kwargs
        assert kwargs == {"gridChargeCycle": 1, "ctrDisCycle": 1}
        args = mock_api.updateChargeConfigInfo.await_args.args
        assert args[1:3] == (90, 1)

    async def test_disabled_switch_value_of_zero_is_preserved(self, supported, mock_api):
        """0 is a valid gridCharge/ctrDis value and must not become the default 1."""
        await supported.async_write_charge_config(SERIAL, grid_charge=0)

        assert mock_api.setTimeChargeBySn.await_args.kwargs["gridChargeCycle"] == 0
        assert mock_api.updateChargeConfigInfo.await_args.args[2] == 0

    async def test_only_the_changed_side_writes_legacy(self, supported, mock_api):
        await supported.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.updateChargeConfigInfo.assert_awaited_once()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()


class TestUnsupportedSystems:
    async def test_periodic_skipped_and_legacy_still_written(self, unsupported, mock_api):
        await unsupported.async_write_charge_config(SERIAL, times={"timeChaf1": "02:00"})

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_awaited_once()

    async def test_legacy_failure_propagates_when_periodic_unavailable(
        self, unsupported, mock_api
    ):
        """With no periodic write to fall back on, the entity must see the error."""
        mock_api.updateChargeConfigInfo.side_effect = OSError("api down")

        with pytest.raises(OSError):
            await unsupported.async_write_charge_config(SERIAL, grid_charge=1)

    async def test_probe_runs_lazily_on_first_write(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        mock_api.getTimeChargeBySn.return_value = None

        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)

        mock_api.getTimeChargeBySn.assert_awaited_once_with(SERIAL)
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_awaited_once()


class TestFailureHandling:
    async def test_periodic_failure_propagates(self, supported, mock_api):
        """Periodic is the primary write, so its failure must reach the entity."""
        mock_api.setTimeChargeBySn.side_effect = OSError("api down")

        with pytest.raises(OSError):
            await supported.async_write_charge_config(SERIAL, grid_charge=1)

        # The legacy call is never reached, so nothing is half-applied.
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_legacy_failure_swallowed_after_periodic_success(
        self, supported, mock_api, caplog
    ):
        """The inverter already has the schedule it acts on — don't fail the write."""
        mock_api.updateChargeConfigInfo.side_effect = OSError("api down")

        await supported.async_write_charge_config(SERIAL, grid_charge=1)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        assert "periodic schedule was accepted" in caplog.text

    async def test_overlap_skips_periodic_and_falls_back_to_legacy(
        self, make_coordinator, mock_api, caplog
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data(
            charge_timeChaf1="01:00", charge_timeChae1="18:00",  # overlaps discharge
        )}
        coordinator._periodic_support[SERIAL] = True

        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_awaited_once()
        assert "overlaps discharge period" in caplog.text

    async def test_cancellation_during_legacy_write_is_not_swallowed(
        self, supported, mock_api
    ):
        """Shutdown must cancel the write, not be logged as a legacy failure."""
        mock_api.updateChargeConfigInfo.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await supported.async_write_charge_config(SERIAL, grid_charge=1)

    async def test_cancellation_during_probe_is_not_swallowed(
        self, make_coordinator, mock_api
    ):
        mock_api.getTimeChargeBySn.side_effect = asyncio.CancelledError()
        coordinator = make_coordinator()

        with pytest.raises(asyncio.CancelledError):
            await coordinator.async_probe_periodic_support(SERIAL)

    async def test_overlap_still_surfaces_legacy_errors(self, make_coordinator, mock_api):
        """Skipping periodic makes legacy primary again, errors included."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data(
            charge_timeChaf1="01:00", charge_timeChae1="18:00",
        )}
        coordinator._periodic_support[SERIAL] = True
        mock_api.updateChargeConfigInfo.side_effect = OSError("api down")

        with pytest.raises(OSError):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=1)


class TestSchedulingApiDiagnostic:
    def test_reports_periodic_when_supported(self, supported):
        assert supported.get_scheduling_api(SERIAL) == SCHEDULING_API_PERIODIC

    def test_reports_legacy_when_not_entitled(self, unsupported):
        assert unsupported.get_scheduling_api(SERIAL) == SCHEDULING_API_LEGACY

    def test_reports_unknown_before_the_probe_answers(self, make_coordinator):
        assert make_coordinator().get_scheduling_api(SERIAL) == SCHEDULING_API_UNKNOWN

    def test_published_into_coordinator_data(self, supported):
        supported._update_diagnostics()
        assert supported.data[SERIAL][AlphaESSNames.SchedulingApi] == SCHEDULING_API_PERIODIC

    async def test_unknown_support_is_retried(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: _schedule_data()}
        mock_api.getTimeChargeBySn.return_value = {"chargeTimeList": [], "dischargeTimeList": []}

        await coordinator._async_resolve_unknown_scheduling_api()

        mock_api.getTimeChargeBySn.assert_awaited_once_with(SERIAL)
        assert coordinator.get_scheduling_api(SERIAL) == SCHEDULING_API_PERIODIC

    async def test_known_support_is_not_reprobed(self, supported, mock_api):
        await supported._async_resolve_unknown_scheduling_api()

        mock_api.getTimeChargeBySn.assert_not_awaited()


class TestButtonAndResetPaths:
    async def test_reset_clears_periodic_schedule(self, supported, mock_api):
        await supported.reset_config(SERIAL)

        _, cycle_type, charge_list, discharge_list = mock_api.setTimeChargeBySn.await_args.args
        assert cycle_type == PERIODIC_DAILY
        # Everything reset to 00:00 means no periods at all.
        assert charge_list == []
        assert discharge_list == []
        mock_api.updateChargeConfigInfo.assert_awaited_once()
        mock_api.updateDisChargeConfigInfo.assert_awaited_once()

    async def test_update_charge_writes_both_apis(self, supported, mock_api):
        await supported.update_charge("batHighCap", SERIAL, 60)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.updateChargeConfigInfo.assert_awaited_once()

    async def test_update_discharge_writes_both_apis(self, supported, mock_api):
        await supported.update_discharge("batUseCap", SERIAL, 30)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.updateDisChargeConfigInfo.assert_awaited_once()
