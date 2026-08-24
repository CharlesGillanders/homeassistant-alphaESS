"""Safety regressions for charge/discharge schedule transactions.

The periodic schedule (``getTimeChargeBySn``/``setTimeChargeBySn``) is the only
schedule store the integration uses; the legacy charge/discharge endpoints are
retired and must never be called. These tests exercise the boundaries that
caused issue #269: entity edits are staged until one explicit Apply, every
write is a fresh-read/patch/single-POST transaction, and a known-invalid
transaction must fail before any POST is made.
"""

from copy import deepcopy

import pytest
from alphaess.alphaess import AlphaESSApiError
from homeassistant.util import dt as dt_util

from custom_components.alphaess.coordinator import (
    PERIODIC_NOT_ENTITLED,
    PERIODIC_OVERLAP,
    ScheduleConflictError,
    ScheduleWriteError,
)
from custom_components.alphaess.enums import AlphaESSNames

SERIAL = "ALP123456"
GHOST_BEGIN = "13:30"
GHOST_END = "02:45"


def _api_error(code: int, description: str | None = None) -> AlphaESSApiError:
    """Build the API exception shape raised by alphaessopenapi."""
    return AlphaESSApiError(code=code, description=description)


def _period(
    begin: str,
    end: str,
    *,
    limit: int = 90,
    power: int = 3000,
    weeks: list[int] | None = None,
) -> dict:
    period = {
        "beginTime": begin,
        "endTime": end,
        "chargeLimit": limit,
        "chargePower": power,
    }
    if weeks is not None:
        period["weeks"] = list(weeks)
    return period


def _daily_schedule(
    *,
    charge: list[dict] | None = None,
    discharge: list[dict] | None = None,
    grid_charge: int = 1,
    discharge_enabled: int = 1,
) -> dict:
    return {
        "executeCycleType": 0,
        "gridChargeCycle": grid_charge,
        "ctrDisCycle": discharge_enabled,
        "chargeTimeList": deepcopy(
            charge
            if charge is not None
            else [_period("01:00", "05:00", limit=90, power=3000)]
        ),
        "dischargeTimeList": deepcopy(
            discharge
            if discharge is not None
            else [_period("17:00", "21:00", limit=20, power=2500)]
        ),
    }


def _weekly_schedule() -> dict:
    weekdays = [1, 2, 3, 4, 5]
    weekend = [6, 7]
    return {
        "executeCycleType": 1,
        "gridChargeCycle": 1,
        "ctrDisCycle": 1,
        "chargeTimeList": [
            _period("00:30", "02:00", limit=80, power=1100, weeks=weekdays),
            _period("02:15", "03:30", limit=85, power=2200, weeks=weekdays),
            _period("04:00", "05:00", limit=95, power=3300, weeks=weekend),
        ],
        "dischargeTimeList": [
            _period("16:00", "17:00", limit=15, power=1400, weeks=weekdays),
            _period("17:15", "18:15", limit=20, power=2400, weeks=weekdays),
            _period("19:00", "20:00", limit=25, power=3400, weeks=weekend),
        ],
    }


def _legacy_charge(
    *,
    begin1: str = "01:00",
    end1: str = "05:00",
    begin2: str = "00:00",
    end2: str = "00:00",
    limit: int = 90,
    enabled: int = 1,
) -> dict:
    """Build a charge-side entity snapshot (legacy key names, data-only)."""
    return {
        "batHighCap": limit,
        "gridCharge": enabled,
        "timeChaf1": begin1,
        "timeChae1": end1,
        "timeChaf2": begin2,
        "timeChae2": end2,
    }


def _legacy_discharge(
    *,
    begin1: str = "17:00",
    end1: str = "21:00",
    begin2: str = "00:00",
    end2: str = "00:00",
    limit: int = 20,
    enabled: int = 1,
) -> dict:
    """Build a discharge-side entity snapshot (legacy key names, data-only)."""
    return {
        "batUseCap": limit,
        "ctrDis": enabled,
        "timeDisf1": begin1,
        "timeDise1": end1,
        "timeDisf2": begin2,
        "timeDise2": end2,
    }


def _entity_data(charge: dict, discharge: dict, *, poinv: float | None = 5.0) -> dict:
    data = {
        AlphaESSNames.batHighCap: charge["batHighCap"],
        "gridCharge": charge["gridCharge"],
        "charge_timeChaf1": charge["timeChaf1"],
        "charge_timeChae1": charge["timeChae1"],
        "charge_timeChaf2": charge["timeChaf2"],
        "charge_timeChae2": charge["timeChae2"],
        AlphaESSNames.batUseCap: discharge["batUseCap"],
        "ctrDis": discharge["ctrDis"],
        "discharge_timeDisf1": discharge["timeDisf1"],
        "discharge_timeDise1": discharge["timeDise1"],
        "discharge_timeDisf2": discharge["timeDisf2"],
        "discharge_timeDise2": discharge["timeDise2"],
    }
    if poinv is not None:
        data[AlphaESSNames.poinv] = poinv
    return data


def _schedule_data() -> dict:
    """Return a complete entity data snapshot for shared tests."""
    return _entity_data(_legacy_charge(), _legacy_discharge())


def _cached(schedule: dict) -> dict:
    """Return the form a read leaves in the cache.

    gridChargeCycle/ctrDisCycle are write-only (the endpoint answers 0 for both
    however the inverter is set), so the snapshot never carries them.
    """
    return {
        key: deepcopy(value)
        for key, value in schedule.items()
        if key not in ("gridChargeCycle", "ctrDisCycle")
    }


def _seed(
    coordinator,
    *,
    periodic: dict | None = None,
    charge: dict | None = None,
    discharge: dict | None = None,
    readable: bool | None = True,
    poinv: float | None = 5.0,
):
    """Seed coordinator entity data and the periodic snapshot.

    ``charge``/``discharge`` seed only ``coordinator.data`` (the entity view);
    the periodic store is the only schedule store the coordinator keeps.
    """
    charge = deepcopy(charge or _legacy_charge())
    discharge = deepcopy(discharge or _legacy_discharge())
    coordinator.data = {SERIAL: _entity_data(charge, discharge, poinv=poinv)}
    if periodic is not None:
        # The cache always holds the normalised resource, exactly as a real read
        # leaves it: gridChargeCycle/ctrDisCycle are write-only and stripped.
        coordinator._periodic_schedules[SERIAL] = (
            coordinator._normalise_periodic_schedule(deepcopy(periodic))
        )
        # The flags a test puts in the payload express what the user last asked
        # for, which is the only place that answer can come from now.
        coordinator.set_periodic_enable_intent(
            SERIAL,
            grid_charge=periodic.get("gridChargeCycle", 1),
            ctr_dis=periodic.get("ctrDisCycle", 1),
        )
        # ...and has already sent it once, so a write that changes nothing
        # still has nothing to say.
        coordinator._periodic_enable_sent[SERIAL] = dict(
            coordinator._periodic_enable_intent[SERIAL]
        )
    if readable is not None:
        coordinator._periodic_readable[SERIAL] = readable
    return coordinator


def _periodic_payload(mock_api) -> dict:
    return mock_api.setTimeChargeBySn.await_args.kwargs


class TestPeriodicOnlyStore:
    async def test_hidden_legacy_ghost_is_never_posted_periodically(
        self, make_coordinator, mock_api
    ):
        """The exact #269 ghost cannot reach a POST: the retired legacy stores
        are never read at all, so a stale window in entity data stays inert."""
        periodic = _daily_schedule(
            charge=[_period("03:00", "04:00", limit=88, power=1800)]
        )
        ghost = _legacy_charge(begin1=GHOST_BEGIN, end1=GHOST_END)
        coordinator = _seed(
            make_coordinator(), periodic=periodic, charge=ghost, readable=True
        )
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.async_write_discharge_config(
            SERIAL, times={"timeDise1": "22:00"}
        )

        payload = _periodic_payload(mock_api)
        assert payload["chargeTimeList"] == periodic["chargeTimeList"]
        assert all(
            (period["beginTime"], period["endTime"])
            != (GHOST_BEGIN, GHOST_END)
            for key in ("chargeTimeList", "dischargeTimeList")
            for period in payload[key]
        )
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.getDisChargeConfigInfo.assert_not_awaited()

    async def test_weekly_three_period_resource_round_trips_untouched_fields(
        self, make_coordinator, mock_api
    ):
        periodic = _weekly_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.async_write_charge_config(
            SERIAL, times={"timeChae1": "02:30"}
        )

        payload = _periodic_payload(mock_api)
        assert payload["executeCycleType"] == 1
        assert len(payload["chargeTimeList"]) == 3
        assert len(payload["dischargeTimeList"]) == 3
        assert payload["chargeTimeList"][0] == {
            **periodic["chargeTimeList"][0],
            "endTime": "02:30",
        }
        assert payload["chargeTimeList"][1:] == periodic["chargeTimeList"][1:]
        assert payload["dischargeTimeList"] == periodic["dischargeTimeList"]
        assert [p["chargePower"] for p in payload["chargeTimeList"]] == [
            1100,
            2200,
            3300,
        ]
        assert all(period["weeks"] for period in payload["chargeTimeList"])
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()


class TestQuickButtonSafety:
    """The duration buttons patch only slot 1 and never guess at overlap."""

    async def test_discharge_button_preserves_second_periodic_period(
        self, make_coordinator, mock_api
    ):
        """#269 class: a one-shot button must not delete an app period."""
        periodic = _daily_schedule(
            discharge=[
                _period("02:00", "05:00", limit=20, power=2500),
                _period("13:00", "16:00", limit=25, power=1500),
            ]
        )
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.update_discharge("batUseCap", SERIAL, 15)

        payload = _periodic_payload(mock_api)
        assert len(payload["dischargeTimeList"]) == 2
        assert payload["dischargeTimeList"][1] == periodic["dischargeTimeList"][1]
        # Period 1 is retargeted; its power and cutoff carry over unchanged.
        assert payload["dischargeTimeList"][0]["chargePower"] == 2500
        assert payload["dischargeTimeList"][0]["chargeLimit"] == 20
        assert payload["chargeTimeList"] == periodic["chargeTimeList"]

    async def test_discharge_button_uses_the_legacy_backup_on_6017(
        self, make_coordinator, mock_api
    ):
        """A definitive 6017 activates the legacy backup: the button writes
        the two-slot discharge store, touching only slot 1."""
        coordinator = _seed(make_coordinator(), periodic=None, readable=False)
        mock_api.getDisChargeConfigInfo.return_value = _legacy_discharge(
            begin2="23:00", end2="23:45"
        )

        await coordinator.update_discharge("batUseCap", SERIAL, 15)

        kwargs = mock_api.updateDisChargeConfigInfo.await_args.kwargs
        assert kwargs["ctrDis"] == 1
        assert kwargs["timeDisf2"] == "23:00"
        assert kwargs["timeDise2"] == "23:45"
        mock_api.getTimeChargeBySn.assert_not_awaited()
        mock_api.setTimeChargeBySn.assert_not_awaited()

    async def test_charge_button_preserves_second_periodic_period(
        self, make_coordinator, mock_api
    ):
        """The charge button too patches only slot 1 of the periodic store."""
        periodic = _daily_schedule(
            charge=[
                _period("01:00", "05:00", limit=90, power=3000),
                _period("23:00", "23:45", limit=95, power=1000),
            ]
        )
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.update_charge("batHighCap", SERIAL, 15)

        payload = _periodic_payload(mock_api)
        assert len(payload["chargeTimeList"]) == 2
        assert payload["chargeTimeList"][1] == periodic["chargeTimeList"][1]
        assert payload["chargeTimeList"][0]["chargePower"] == 3000
        assert payload["chargeTimeList"][0]["chargeLimit"] == 90
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_button_refuses_weekly_period_that_does_not_run_today(
        self, make_coordinator, mock_api
    ):
        """Retargeting a period bound to other weekdays would be accepted by
        the API, do nothing now, and reschedule those weekdays instead."""
        today = dt_util.now().isoweekday()
        periodic = _weekly_schedule()
        periodic["dischargeTimeList"][0]["weeks"] = [
            day for day in range(1, 8) if day != today
        ][:2]
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        with pytest.raises(ScheduleWriteError, match="does not run today"):
            await coordinator.update_discharge("batUseCap", SERIAL, 15)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()

    async def test_button_retargets_weekly_period_that_runs_today(
        self, make_coordinator, mock_api
    ):
        today = dt_util.now().isoweekday()
        periodic = _weekly_schedule()
        periodic["dischargeTimeList"][0]["weeks"] = [today]
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.update_discharge("batUseCap", SERIAL, 15)

        payload = _periodic_payload(mock_api)
        assert payload["dischargeTimeList"][0]["weeks"] == [today]
        assert payload["dischargeTimeList"][0]["chargePower"] == 1400

    async def test_button_error_on_missing_first_period_names_a_remedy(
        self, make_coordinator, mock_api
    ):
        """The empty-side failure must not talk about drafts to a button user."""
        periodic = _daily_schedule(discharge=[])
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        with pytest.raises(
            ScheduleWriteError, match="quick discharge button"
        ) as excinfo:
            await coordinator.update_discharge("batUseCap", SERIAL, 15)

        assert "AlphaESS app" in str(excinfo.value)
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()

    async def test_overlapping_windows_are_posted_for_the_api_to_judge(
        self, make_coordinator, mock_api
    ):
        """#269 (benbrown249): no local overlap guess may gate the POST."""
        periodic = _daily_schedule()  # existing discharge 17:00-21:00
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.async_write_charge_config(
            SERIAL, times={"timeChaf1": "17:30", "timeChae1": "18:30"}
        )

        payload = _periodic_payload(mock_api)
        assert payload["chargeTimeList"][0]["beginTime"] == "17:30"
        assert payload["chargeTimeList"][0]["endTime"] == "18:30"
        assert payload["dischargeTimeList"][0]["beginTime"] == "17:00"

    async def test_api_overlap_rejection_is_surfaced_not_predicted(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
        mock_api.setTimeChargeBySn.side_effect = _api_error(
            PERIODIC_OVERLAP, "Set failed"
        )

        with pytest.raises(ScheduleWriteError, match="was not updated"):
            await coordinator.async_write_charge_config(
                SERIAL, times={"timeChaf1": "17:30", "timeChae1": "18:30"}
            )

        mock_api.setTimeChargeBySn.assert_awaited_once()
        mock_api.updateChargeConfigInfo.assert_not_awaited()


class TestDraftTransaction:
    async def test_sequential_entity_edits_make_one_post_on_apply(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)

        coordinator.stage_schedule_change(
            SERIAL, discharge={"timeDisf2": "22:00"}
        )
        coordinator.stage_schedule_change(
            SERIAL, discharge={"timeDise2": "22:15"}
        )
        coordinator.stage_schedule_change(
            SERIAL, discharge={"chargePower2": 1750.0}
        )

        mock_api.getTimeChargeBySn.assert_not_awaited()
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()
        assert coordinator.has_schedule_draft(SERIAL)

        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
        await coordinator.async_apply_schedule_draft(SERIAL)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        # Discharge has no legacy store any more; nothing else is called.
        mock_api.getDisChargeConfigInfo.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()
        periods = _periodic_payload(mock_api)["dischargeTimeList"]
        assert periods[0] == periodic["dischargeTimeList"][0]
        assert periods[1] == {
            "beginTime": "22:00",
            "endTime": "22:15",
            "chargeLimit": 20,
            "chargePower": 1750,
        }
        assert type(periods[1]["chargePower"]) is int
        assert not coordinator.has_schedule_draft(SERIAL)

    async def test_periodic_conflict_blocks_every_post_and_keeps_draft(
        self, make_coordinator, mock_api
    ):
        original = _daily_schedule()
        changed_remotely = _daily_schedule(
            discharge=[_period("16:00", "20:00", limit=20, power=2500)]
        )
        coordinator = _seed(make_coordinator(), periodic=original, readable=True)
        coordinator.stage_schedule_change(
            SERIAL, charge={"timeChae1": "04:30"}
        )
        mock_api.getTimeChargeBySn.return_value = changed_remotely

        with pytest.raises(ScheduleConflictError, match="periodic schedule changed"):
            await coordinator.async_apply_schedule_draft(SERIAL)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        assert coordinator.has_schedule_draft(SERIAL)

    async def test_stage_uses_the_legacy_backup_on_6017(
        self, make_coordinator, mock_api
    ):
        """A definitive 6017 activates the legacy backup: staging works from
        the complete polled two-slot view, without any API call."""
        coordinator = _seed(make_coordinator(), periodic=None, readable=False)

        assert coordinator.can_stage_schedule(SERIAL) is True
        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})

        assert coordinator.has_schedule_draft(SERIAL)
        assert coordinator._schedule_drafts[SERIAL]["charge"]["timeChaf1"] == "02:00"
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.getTimeChargeBySn.assert_not_awaited()

    async def test_stage_refused_when_periodic_writes_are_denied(
        self, make_coordinator, mock_api
    ):
        """Write-denied is not backup mode: the periodic store still governs
        the inverter, so editing a store it ignores would only mislead."""
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator._periodic_write_denied.add(SERIAL)

        assert coordinator.can_stage_schedule(SERIAL) is False
        with pytest.raises(ScheduleWriteError, match="Schedule control requires"):
            coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})

        assert not coordinator.has_schedule_draft(SERIAL)
        mock_api.getChargeConfigInfo.assert_not_awaited()

    async def test_stage_fails_closed_while_readability_is_unknown(
        self, make_coordinator, mock_api
    ):
        """An undecided periodic probe must not admit edits it cannot apply."""
        coordinator = _seed(make_coordinator(), periodic=None, readable=None)

        assert coordinator.can_stage_schedule(SERIAL) is False
        with pytest.raises(
            ScheduleWriteError, match="periodic schedule can be read"
        ):
            coordinator.stage_schedule_change(
                SERIAL, discharge={"timeDisf1": "01:00"}
            )

        assert not coordinator.has_schedule_draft(SERIAL)


class TestValidationBeforeWrite:
    async def test_unknown_transient_periodic_read_blocks_the_post(
        self, make_coordinator, mock_api
    ):
        coordinator = _seed(make_coordinator(), periodic=None, readable=None)
        mock_api.getTimeChargeBySn.side_effect = OSError("connection reset")

        with pytest.raises(ScheduleWriteError, match="could not be read safely"):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_power_write_on_backup_system_fails_closed(
        self, make_coordinator, mock_api
    ):
        """The legacy backup has no power field; a power write on a 6017
        system must fail before any request rather than be silently dropped."""
        coordinator = _seed(make_coordinator(), periodic=None, readable=False)

        with pytest.raises(ScheduleWriteError, match="power"):
            await coordinator.async_write_charge_config(
                SERIAL, powers={"chargePower1": 1800}
            )

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_cached_periodic_write_denial_blocks_without_a_read(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator._periodic_write_denied.add(SERIAL)

        with pytest.raises(ScheduleWriteError, match="Schedule control requires"):
            await coordinator.async_write_charge_config(
                SERIAL, powers={"chargePower1": 1800}
            )

        assert coordinator.is_periodic_schedule_readable(SERIAL) is False
        mock_api.getTimeChargeBySn.assert_not_awaited()
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {
                "executeCycleType": 0,
                "chargeTimeList": [],
                # Missing dischargeTimeList is a malformed full resource.
            },
        ],
        ids=["empty", "malformed"],
    )
    async def test_known_readable_bad_refresh_blocks_the_post(
        self, make_coordinator, mock_api, payload
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = payload

        with pytest.raises(ScheduleWriteError, match="periodic schedule"):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_missing_power_blocks_all_posts(self, make_coordinator, mock_api):
        periodic = _daily_schedule()
        del periodic["chargeTimeList"][0]["chargePower"]
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        with pytest.raises(ScheduleWriteError, match="chargePower"):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    @pytest.mark.parametrize("power", [1200.5, float("nan"), float("inf"), True])
    async def test_invalid_power_type_blocks_all_posts(
        self, make_coordinator, mock_api, power
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        with pytest.raises(ScheduleWriteError, match="chargePower"):
            await coordinator.async_write_charge_config(
                SERIAL, powers={"chargePower1": power}
            )

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_empty_opposite_list_blocks_all_posts(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule(charge=[])
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        with pytest.raises(ScheduleWriteError, match="chargeTimeList would be empty"):
            await coordinator.async_write_discharge_config(SERIAL, ctr_dis=1)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()

    async def test_noop_write_makes_no_post(self, make_coordinator, mock_api):
        """Proposed == current is a success without consuming a write."""
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.async_write_charge_config(
            SERIAL, times={"timeChae1": "05:00"}
        )

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()


class TestEndpointOutcomes:
    async def test_read_6017_activates_the_legacy_backup_and_is_cached(
        self, make_coordinator, mock_api
    ):
        """6017 is cached as permanent and selects the legacy backup: the
        same write transaction falls through to the two-slot store, and no
        later write probes the periodic API again."""
        coordinator = _seed(make_coordinator(), periodic=None, readable=None)
        mock_api.getTimeChargeBySn.side_effect = _api_error(
            PERIODIC_NOT_ENTITLED, "No operation permissions"
        )
        mock_api.getChargeConfigInfo.return_value = _legacy_charge()

        await coordinator.async_write_charge_config(SERIAL, grid_charge=0)
        mock_api.getChargeConfigInfo.return_value = _legacy_charge(enabled=0)
        await coordinator.async_write_charge_config(SERIAL, grid_charge=1)

        # The permanent refusal is cached: only the first write probed.
        mock_api.getTimeChargeBySn.assert_awaited_once_with(SERIAL)
        mock_api.setTimeChargeBySn.assert_not_awaited()
        assert mock_api.updateChargeConfigInfo.await_count == 2
        assert coordinator._periodic_readable[SERIAL] is False

    async def test_generic_6008_is_surfaced_with_its_meaning_logged(
        self, make_coordinator, mock_api, caplog
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
        mock_api.setTimeChargeBySn.side_effect = _api_error(
            PERIODIC_OVERLAP, "Set failed"
        )

        with pytest.raises(ScheduleWriteError, match="was not updated"):
            await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        mock_api.updateChargeConfigInfo.assert_not_awaited()
        assert coordinator._periodic_schedules[SERIAL] == _cached(periodic)
        assert "generic set-failed" in caplog.text
        assert "one possible cause" in caplog.text

    async def test_discharge_write_is_periodic_only_and_cannot_10001(
        self, make_coordinator, mock_api
    ):
        """dragon2611's 10001 came from the retired updateDisChargeConfigInfo.
        A discharge write now touches only the periodic store, so that failure
        mode no longer exists — and a stale poll cannot reset the view."""
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)
        mock_api.updateDisChargeConfigInfo.side_effect = AssertionError(
            "retired endpoint must never be called"
        )
        mock_api.getDisChargeConfigInfo.side_effect = AssertionError(
            "retired endpoint must never be called"
        )

        await coordinator.async_write_discharge_config(
            SERIAL, times={"timeDise1": "22:00"}
        )

        mock_api.setTimeChargeBySn.assert_awaited_once()
        assert coordinator._periodic_schedules[SERIAL]["dischargeTimeList"][0][
            "endTime"
        ] == "22:00"

        # A stale legacy-shaped poll snapshot must not reset the app-facing
        # periodic view used by the entities.
        stale_data = _entity_data(_legacy_charge(), _legacy_discharge())
        coordinator._overlay_schedule_view(SERIAL, stale_data)
        assert stale_data["discharge_timeDise1"] == "22:00"


class TestRefresh:
    async def test_known_readable_periodic_schedule_is_refreshed(
        self, make_coordinator, mock_api
    ):
        original = _daily_schedule()
        changed = _daily_schedule(
            charge=[_period("02:00", "06:00", limit=92, power=1750)]
        )
        coordinator = _seed(make_coordinator(), periodic=original, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(changed)

        await coordinator._async_resolve_unknown_periodic_read()

        mock_api.getTimeChargeBySn.assert_awaited_once_with(SERIAL)
        assert coordinator._periodic_schedules[SERIAL] == _cached(changed)
        assert coordinator.data[SERIAL]["charge_timeChaf1"] == "02:00"
        assert coordinator.data[SERIAL]["charge_timeChae1"] == "06:00"

    async def test_reset_clears_both_backup_stores_on_6017(
        self, make_coordinator, mock_api
    ):
        """The reset exists only for backup mode, where it zeroes both
        two-slot stores; the periodic store cannot be cleared."""
        coordinator = _seed(make_coordinator(), periodic=None, readable=False)
        mock_api.getChargeConfigInfo.return_value = _legacy_charge()
        mock_api.getDisChargeConfigInfo.return_value = _legacy_discharge()

        assert coordinator.can_reset_schedule(SERIAL) is True
        await coordinator.reset_config(SERIAL)

        charge = mock_api.updateChargeConfigInfo.await_args.kwargs
        discharge = mock_api.updateDisChargeConfigInfo.await_args.kwargs
        assert charge["timeChaf1"] == charge["timeChae1"] == "00:00"
        assert charge["timeChaf2"] == charge["timeChae2"] == "00:00"
        assert discharge["timeDisf1"] == discharge["timeDise1"] == "00:00"
        assert discharge["timeDisf2"] == discharge["timeDise2"] == "00:00"
        mock_api.setTimeChargeBySn.assert_not_awaited()

    async def test_reset_refused_on_periodic_systems(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        assert coordinator.can_reset_schedule(SERIAL) is False
        with pytest.raises(ScheduleWriteError, match="clear"):
            await coordinator.reset_config(SERIAL)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()


class TestWriteOnlyEnableFlags:
    """gridChargeCycle/ctrDisCycle are write-only. getTimeChargeBySn answers 0
    for both whatever the inverter is doing, and setTimeChargeBySn acts on
    them — 0/0 switches the inverter to self-consumption. Echoing the read back
    therefore turns timed control off on every write (#267)."""

    def test_the_read_never_becomes_part_of_the_snapshot(self, make_coordinator):
        coordinator = make_coordinator()
        payload = _daily_schedule()
        payload["gridChargeCycle"] = 0
        payload["ctrDisCycle"] = 0

        normalised = coordinator._normalise_periodic_schedule(payload)

        assert "gridChargeCycle" not in normalised
        assert "ctrDisCycle" not in normalised

    async def test_a_write_sends_what_was_asked_for_not_what_was_read(
        self, make_coordinator, mock_api
    ):
        """The regression: a system reporting 0/0 while running a schedule must
        not have those zeros posted back at it."""
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator.set_periodic_enable_intent(SERIAL, grid_charge=1, ctr_dis=0)
        answered = deepcopy(periodic)
        answered["gridChargeCycle"] = 0
        answered["ctrDisCycle"] = 0
        mock_api.getTimeChargeBySn.return_value = answered

        await coordinator.async_write_charge_config(
            SERIAL, times={"timeChaf1": "02:00"}
        )

        payload = _periodic_payload(mock_api)
        assert payload["gridChargeCycle"] == 1
        assert payload["ctrDisCycle"] == 0

    async def test_a_switch_change_wins_over_the_stored_answer(
        self, make_coordinator, mock_api
    ):
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator.set_periodic_enable_intent(SERIAL, grid_charge=0, ctr_dis=0)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.async_write_discharge_config(SERIAL, ctr_dis=1)

        payload = _periodic_payload(mock_api)
        assert payload["gridChargeCycle"] == 0
        assert payload["ctrDisCycle"] == 1
        # And it becomes the answer the next write starts from.
        assert coordinator._periodic_enable_intent[SERIAL] == {
            "gridChargeCycle": 0,
            "ctrDisCycle": 1,
        }

    async def test_a_write_is_refused_while_the_answer_is_unknown(
        self, make_coordinator, mock_api
    ):
        """Nothing in the API can supply it, so guessing would mean choosing
        the inverter's working mode on the user's behalf."""
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator._periodic_enable_intent.pop(SERIAL, None)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        with pytest.raises(ScheduleWriteError, match="does not know whether"):
            await coordinator.async_write_charge_config(
                SERIAL, times={"timeChaf1": "02:00"}
            )

        mock_api.setTimeChargeBySn.assert_not_awaited()

    async def test_one_sided_knowledge_is_still_not_an_answer(
        self, make_coordinator, mock_api
    ):
        """Both parameters go in every request, so half a record cannot fill
        one in."""
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        coordinator._periodic_enable_intent[SERIAL] = {"gridChargeCycle": 1}
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        with pytest.raises(ScheduleWriteError, match="does not know whether"):
            await coordinator.async_write_discharge_config(
                SERIAL, times={"timeDisf1": "18:00"}
            )

        mock_api.setTimeChargeBySn.assert_not_awaited()

    def test_half_a_restored_answer_does_not_break_the_switches(
        self, make_coordinator
    ):
        """Reported on #267: one switch restores its state and the other has
        none, so the entity view holds a None the last-timer guard tried to
        cast. Nothing here is knowable enough to guard on, and guessing at the
        missing side would invent the answer this store cannot give."""
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=deepcopy(periodic), readable=True)
        coordinator._periodic_enable_intent[SERIAL] = {"ctrDisCycle": 1}
        coordinator._overlay_schedule_view(SERIAL, coordinator.data[SERIAL])

        assert coordinator._committed_enable_flags(SERIAL) is None

        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})
        coordinator.stage_schedule_change(SERIAL, charge={"gridCharge": 1})

        assert coordinator._schedule_drafts[SERIAL]["charge"]["gridCharge"] == 1

    async def test_an_incomplete_answer_still_refuses_the_write(
        self, make_coordinator, mock_api
    ):
        """Skipping the guard does not let a half-known pair reach the API."""
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=deepcopy(periodic), readable=True)
        coordinator._periodic_enable_intent[SERIAL] = {"ctrDisCycle": 1}
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "02:00"})
        with pytest.raises(ScheduleWriteError, match="does not know whether"):
            await coordinator.async_apply_schedule_draft(SERIAL)

        mock_api.setTimeChargeBySn.assert_not_awaited()

    async def test_a_flag_only_change_still_reaches_the_api(
        self, make_coordinator, mock_api
    ):
        """The periods are identical, but the flags are the whole point of the
        request and the endpoint cannot be asked about them separately."""
        periodic = _daily_schedule()
        coordinator = _seed(make_coordinator(), periodic=periodic, readable=True)
        mock_api.getTimeChargeBySn.return_value = deepcopy(periodic)

        await coordinator.async_write_charge_config(SERIAL, grid_charge=0)

        payload = _periodic_payload(mock_api)
        assert payload["gridChargeCycle"] == 0
        assert payload["chargeTimeList"] == _cached(periodic)["chargeTimeList"]
