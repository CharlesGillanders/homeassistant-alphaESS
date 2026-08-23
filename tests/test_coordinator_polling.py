"""Tests for AlphaESSDataUpdateCoordinator polling, control and fallback logic."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, call

import aiohttp
import pytest
from alphaess.alphaess import AlphaESSApiError
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.alphaess import coordinator as coordinator_module
from custom_components.alphaess.const import (
    CONF_SERIAL_NUMBER,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from custom_components.alphaess.coordinator import ScheduleWriteError
from custom_components.alphaess.enums import AlphaESSNames

SERIAL = "AL1000021000123"
SERIAL2 = "AL2000021000456"


def _api_error(code):
    return AlphaESSApiError(code=code, description="API rejected request")


def _periodic_schedule():
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
            }
        ],
        "dischargeTimeList": [
            {
                "beginTime": "18:00",
                "endTime": "22:00",
                "chargeLimit": 10,
                "chargePower": 2500,
            }
        ],
    }


def _response_error(status):
    return aiohttp.ClientResponseError(
        request_info=MagicMock(), history=(), status=status, message="boom"
    )


def _make_entry_with_subentries():
    inverter_sub = MagicMock()
    inverter_sub.subentry_type = SUBENTRY_TYPE_INVERTER
    inverter_sub.data = {CONF_SERIAL_NUMBER: SERIAL}
    ev_sub = MagicMock()
    ev_sub.subentry_type = SUBENTRY_TYPE_EV_CHARGER
    ev_sub.data = {CONF_SERIAL_NUMBER: "EV123"}
    other_sub = MagicMock()
    other_sub.subentry_type = "other"
    other_sub.data = {}
    entry = MagicMock()
    entry.subentries = {"sub1": inverter_sub, "sub2": ev_sub, "sub3": other_sub}
    return entry


class TestCoordinatorInit:
    def test_throttle_enabled_for_modern_inverters(self, make_coordinator):
        coordinator = make_coordinator(models=["SMILE5-INV", "SMILE-T10-HV-INV"])
        assert coordinator.has_throttle is True
        assert coordinator.inverter_count == 2
        assert coordinator.LOCAL_INVERTER_COUNT == 2

    def test_throttle_enabled_for_lower_inverters(self, make_coordinator):
        coordinator = make_coordinator(models=["Storion-S5"])
        assert coordinator.has_throttle is True
        assert coordinator.LOCAL_INVERTER_COUNT == 0

    def test_no_models(self, make_coordinator):
        coordinator = make_coordinator(models=[])
        assert coordinator.has_throttle is True
        assert coordinator.inverter_count == 0

    def test_subentry_maps(self, make_coordinator):
        entry = _make_entry_with_subentries()
        coordinator = make_coordinator(entry=entry)
        assert coordinator.get_inverter_subentry_id(SERIAL) == "sub1"
        assert coordinator.get_ev_charger_subentry_id("EV123") == "sub2"
        assert coordinator.get_inverter_subentry_id("missing") is None
        assert coordinator.get_ev_charger_subentry_id("missing") is None

    def test_default_intervals(self, mock_hass, mock_api):
        from custom_components.alphaess.coordinator import AlphaESSDataUpdateCoordinator

        coordinator = AlphaESSDataUpdateCoordinator(mock_hass, client=mock_api)
        assert coordinator.update_interval is not None
        assert coordinator.has_throttle is True
        assert coordinator.throttle_multiplier == 10.0

    def test_number_settings_roundtrip(self, make_coordinator):
        coordinator = make_coordinator()
        assert coordinator.get_number_setting(SERIAL, "batUseCap") is None
        assert coordinator.get_number_setting(SERIAL, "batUseCap", 10) == 10
        coordinator.set_number_setting(SERIAL, "batUseCap", 25)
        assert coordinator.get_number_setting(SERIAL, "batUseCap") == 25


class TestEvControl:
    def test_status_raw_variants(self, make_coordinator):
        coordinator = make_coordinator()
        assert coordinator.get_ev_charger_status_raw(SERIAL) is None

        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: "3"}}
        assert coordinator.get_ev_charger_status_raw(SERIAL) == 3

        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatus: 4}}
        assert coordinator.get_ev_charger_status_raw(SERIAL) == 4

        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: "junk"}}
        assert coordinator.get_ev_charger_status_raw(SERIAL) is None

    @pytest.mark.parametrize(
        ("status", "direction", "expected"),
        [
            (2, 1, True),
            (4, 1, True),
            (5, 1, True),
            (3, 1, False),
            (3, 0, True),
            (4, 0, True),
            (5, 0, True),
            (2, 0, False),
            (3, 9, False),
        ],
    )
    def test_can_control_ev(self, make_coordinator, status, direction, expected):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: status}}
        assert coordinator.can_control_ev(SERIAL, direction) is expected

    def test_can_control_ev_no_status(self, make_coordinator):
        coordinator = make_coordinator()
        assert coordinator.can_control_ev(SERIAL, 1) is False

    async def test_control_ev_sends_even_when_state_disagrees(
        self, make_coordinator, mock_api
    ):
        """The status is only as fresh as the last poll, so don't refuse on it."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 3}}
        await coordinator.control_ev(SERIAL, "EV123", "1")
        mock_api.remoteControlEvCharger.assert_awaited_once_with(
            sysSn=SERIAL, evchargerSn="EV123", controlMode=1
        )

    async def test_control_ev_sends_when_status_is_unknown(
        self, make_coordinator, mock_api
    ):
        """No status used to block every command indefinitely."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        await coordinator.control_ev(SERIAL, "EV123", "1")
        mock_api.remoteControlEvCharger.assert_awaited_once_with(
            sysSn=SERIAL, evchargerSn="EV123", controlMode=1
        )

    async def test_control_ev_allowed(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 2}}
        await coordinator.control_ev(SERIAL, "EV123", "1")
        mock_api.remoteControlEvCharger.assert_awaited_once_with(
            sysSn=SERIAL, evchargerSn="EV123", controlMode=1
        )

    async def test_set_ev_charger_current(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        await coordinator.set_ev_charger_current(SERIAL, 16)
        mock_api.setEvChargerCurrentsBySn.assert_awaited_once_with(
            sysSn=SERIAL, currentsetting=16
        )
        coordinator.async_request_refresh.assert_awaited_once()

    async def test_set_ev_charger_current_api_rejection_propagates(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        mock_api.setEvChargerCurrentsBySn.side_effect = _api_error(6042)

        with pytest.raises(AlphaESSApiError):
            await coordinator.set_ev_charger_current(SERIAL, 16)

        coordinator.async_request_refresh.assert_not_awaited()

    async def test_set_ev_charger_current_transport_error_propagates(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        mock_api.setEvChargerCurrentsBySn.side_effect = aiohttp.ClientError("offline")

        with pytest.raises(aiohttp.ClientError, match="offline"):
            await coordinator.set_ev_charger_current(SERIAL, 16)

        coordinator.async_request_refresh.assert_not_awaited()

    async def test_control_ev_api_rejection_propagates(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        mock_api.remoteControlEvCharger.side_effect = _api_error(6042)

        with pytest.raises(AlphaESSApiError):
            await coordinator.control_ev(SERIAL, "EV123", 1)


class TestChargeDischargeCommands:
    async def test_reset_clears_both_backup_stores(self, make_coordinator, mock_api):
        """Reset exists for backup mode only, where it zeroes both two-slot
        stores; the periodic store cannot be cleared."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        coordinator._periodic_readable[SERIAL] = False
        mock_api.getChargeConfigInfo.return_value = {
            "batHighCap": 90, "gridCharge": 1,
            "timeChaf1": "01:00", "timeChae1": "05:00",
            "timeChaf2": "00:00", "timeChae2": "00:00",
        }
        mock_api.getDisChargeConfigInfo.return_value = {
            "batUseCap": 10, "ctrDis": 1,
            "timeDisf1": "18:00", "timeDise1": "22:00",
            "timeDisf2": "00:00", "timeDise2": "00:00",
        }

        assert coordinator.can_reset_schedule(SERIAL) is True
        await coordinator.reset_config(SERIAL)

        charge = mock_api.updateChargeConfigInfo.await_args.kwargs
        assert charge["timeChaf1"] == charge["timeChae1"] == "00:00"
        discharge = mock_api.updateDisChargeConfigInfo.await_args.kwargs
        assert discharge["timeDisf1"] == discharge["timeDise1"] == "00:00"
        mock_api.setTimeChargeBySn.assert_not_awaited()

    async def test_update_discharge(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.set_number_setting(SERIAL, "batUseCap", 15)
        coordinator.data = {SERIAL: {}}
        coordinator.set_periodic_enable_intent(SERIAL, grid_charge=1, ctr_dis=1)
        periodic = _periodic_schedule()
        mock_api.getTimeChargeBySn.return_value = periodic

        await coordinator.update_discharge("batUseCap", SERIAL, 30)

        mock_api.getTimeChargeBySn.assert_awaited_once_with(SERIAL)
        # Discharge is periodic-only; the retired legacy endpoints stay quiet.
        mock_api.getDisChargeConfigInfo.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()
        periodic_write = mock_api.setTimeChargeBySn.await_args.kwargs
        assert periodic_write["chargeTimeList"] == periodic["chargeTimeList"]
        assert periodic_write["dischargeTimeList"][0]["chargeLimit"] == 10
        assert periodic_write["dischargeTimeList"][0]["chargePower"] == 2500
        assert coordinator.data[SERIAL]["ctrDis"] == 1
        coordinator.async_set_updated_data.assert_called()

    async def test_update_discharge_uses_legacy_backup_on_6017(
        self, make_coordinator, mock_api
    ):
        """A definitive 6017 selects the legacy backup: the press writes the
        two-slot discharge store and never probes the periodic API again."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        coordinator._periodic_readable[SERIAL] = False
        mock_api.getDisChargeConfigInfo.return_value = {
            "batUseCap": 10, "ctrDis": 0,
            "timeDisf1": "00:00", "timeDise1": "00:00",
            "timeDisf2": "23:00", "timeDise2": "23:30",
        }

        await coordinator.update_discharge("batUseCap", SERIAL, 30)

        kwargs = mock_api.updateDisChargeConfigInfo.await_args.kwargs
        assert kwargs["ctrDis"] == 1
        # Slot 2 is preserved from the fresh read, not zeroed.
        assert kwargs["timeDisf2"] == "23:00"
        mock_api.getTimeChargeBySn.assert_not_awaited()
        mock_api.setTimeChargeBySn.assert_not_awaited()
        assert coordinator.data[SERIAL]["ctrDis"] == 1

    async def test_update_charge(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.set_number_setting(SERIAL, "batHighCap", 85)
        coordinator.data = {SERIAL: {}}
        coordinator.set_periodic_enable_intent(SERIAL, grid_charge=1, ctr_dis=1)
        periodic = _periodic_schedule()
        mock_api.getTimeChargeBySn.return_value = periodic

        await coordinator.update_charge("batHighCap", SERIAL, 60)

        mock_api.getTimeChargeBySn.assert_awaited_once_with(SERIAL)
        # Charge is periodic-only; the retired legacy endpoints stay quiet.
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        periodic_write = mock_api.setTimeChargeBySn.await_args.kwargs
        assert periodic_write["dischargeTimeList"] == periodic["dischargeTimeList"]
        assert periodic_write["chargeTimeList"][0]["chargeLimit"] == 90
        assert periodic_write["chargeTimeList"][0]["chargePower"] == 3000
        assert coordinator.data[SERIAL]["gridCharge"] == 1
        coordinator.async_set_updated_data.assert_called()

    async def test_update_charge_uses_legacy_backup_on_6017(
        self, make_coordinator, mock_api
    ):
        """A definitive 6017 selects the legacy backup for charge too."""
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        coordinator._periodic_readable[SERIAL] = False
        mock_api.getChargeConfigInfo.return_value = {
            "batHighCap": 90, "gridCharge": 0,
            "timeChaf1": "00:00", "timeChae1": "00:00",
            "timeChaf2": "06:00", "timeChae2": "07:00",
        }

        await coordinator.update_charge("batHighCap", SERIAL, 60)

        kwargs = mock_api.updateChargeConfigInfo.await_args.kwargs
        assert kwargs["gridCharge"] == 1
        assert kwargs["timeChaf2"] == "06:00"
        mock_api.getTimeChargeBySn.assert_not_awaited()
        mock_api.setTimeChargeBySn.assert_not_awaited()
        assert coordinator.data[SERIAL]["gridCharge"] == 1

    async def test_update_charge_serial_missing(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {}
        coordinator.set_periodic_enable_intent(SERIAL, grid_charge=1, ctr_dis=1)
        mock_api.getTimeChargeBySn.return_value = _periodic_schedule()

        await coordinator.update_charge("batHighCap", SERIAL, 60)

        mock_api.setTimeChargeBySn.assert_awaited_once()
        coordinator.async_set_updated_data.assert_not_called()

    async def test_update_charge_fails_closed_when_periodic_read_is_inconclusive(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        mock_api.getTimeChargeBySn.return_value = None

        with pytest.raises(ScheduleWriteError, match="could not be read safely"):
            await coordinator.update_charge("batHighCap", SERIAL, 60)

        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()
        coordinator.async_set_updated_data.assert_not_called()


def _configure_success_api(mock_api, serial=SERIAL, model="SMILE5-INV", with_ev=False):
    """Configure mock API for one successful inverter fetch."""
    mock_api.getESSList.return_value = [{"sysSn": serial, "minv": model}]
    mock_api.getSumDataForCustomer.return_value = {"eload": 5, "moneyType": "USD"}
    mock_api.getOneDateEnergyBySn.return_value = {"epv": 10.0, "eOutput": 1.0}
    mock_api.getLastPowerData.return_value = {"soc": 50, "pload": 100}
    mock_api.getOneDayPowerBySn.return_value = [{"cbat": 55}]
    if with_ev:
        mock_api.getEvChargerConfigList.return_value = [
            {"evchargerSn": "EV123", "evchargerModel": "SMILE-EVCT11"}
        ]
        mock_api.getEvChargerStatusBySn.return_value = {"evchargerStatus": 2}
        mock_api.getEvChargerCurrentsBySn.return_value = {"currentsetting": 16}
    else:
        mock_api.getEvChargerConfigList.return_value = None


class TestESSListHandling:
    @pytest.mark.parametrize("alt", (False, True))
    async def test_empty_success_prunes_previous_state(
        self, make_coordinator, mock_api, alt
    ):
        coordinator = make_coordinator(alt=alt)
        coordinator.data = {SERIAL: {"sentinel": True}}
        coordinator.number_settings[SERIAL] = {"batHighCap": 80}
        coordinator._inverter_error_count[SERIAL] = 2
        coordinator._multi_ev_warned.add(SERIAL)
        mock_api.getESSList.return_value = []

        data = await coordinator._async_update_data()

        assert data == {}
        assert coordinator.cloud_available is True
        assert SERIAL not in coordinator.number_settings
        assert SERIAL not in coordinator._inverter_error_count
        assert SERIAL not in coordinator._multi_ev_warned

    @pytest.mark.parametrize("alt", (False, True))
    async def test_partial_success_prunes_absent_system(
        self, make_coordinator, mock_api, alt
    ):
        coordinator = make_coordinator(alt=alt)
        coordinator.data = {
            SERIAL: {"old": True},
            SERIAL2: {"sentinel": True},
        }
        coordinator.number_settings[SERIAL2] = {"batHighCap": 80}
        coordinator._multi_ev_warned.add(SERIAL2)
        _configure_success_api(mock_api)

        data = await coordinator._async_update_data()

        assert SERIAL in data
        assert SERIAL2 not in data
        assert SERIAL2 not in coordinator.number_settings
        assert SERIAL2 not in coordinator._multi_ev_warned

    @pytest.mark.parametrize("alt", (False, True))
    async def test_nonempty_malformed_success_fails_closed_without_pruning(
        self, make_coordinator, mock_api, alt
    ):
        coordinator = make_coordinator(alt=alt)
        coordinator.data = {SERIAL: {"sentinel": True}}
        coordinator.number_settings[SERIAL] = {"batHighCap": 80}
        mock_api.getESSList.return_value = [{"minv": "missing-serial"}]

        data = await coordinator._async_update_data()

        assert data[SERIAL]["sentinel"] is True
        assert coordinator.number_settings[SERIAL] == {"batHighCap": 80}
        assert coordinator.cloud_available is False
        mock_api.getSumDataForCustomer.assert_not_awaited()

    async def test_prefetched_discovery_is_consumed_without_duplicate_read(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        prefetched = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]
        coordinator.set_prefetched_ess_list(prefetched)

        data = await coordinator._async_update_data()

        assert SERIAL in data
        assert coordinator._prefetched_ess_list is None
        assert coordinator._last_api_request_completed is not None
        mock_api.getESSList.assert_not_awaited()


class TestRuntimeAuthentication:
    async def test_body_auth_error_during_discovery_propagates(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        mock_api.getESSList.side_effect = _api_error(6007)

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.parametrize("alt", (False, True))
    async def test_body_auth_error_during_inverter_fetch_propagates(
        self, make_coordinator, mock_api, alt
    ):
        coordinator = make_coordinator(alt=alt)
        _configure_success_api(mock_api)
        mock_api.getSumDataForCustomer.side_effect = _api_error(6007)

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_body_auth_error_during_ev_fetch_propagates(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getEvChargerConfigList.side_effect = _api_error(6007)

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_body_auth_error_during_periodic_probe_propagates(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getTimeChargeBySn.side_effect = _api_error(6007)

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()


class TestNormalPolling:
    async def test_successful_poll(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api, with_ev=True)

        data = await coordinator._async_update_data()

        assert SERIAL in data
        assert data[SERIAL]["Model"] == "SMILE5-INV"
        assert data[SERIAL][AlphaESSNames.TotalLoad] == 5
        assert data[SERIAL][AlphaESSNames.evchargersn] == "EV123"
        assert data[SERIAL][AlphaESSNames.PollMode] == "normal"
        assert data[SERIAL][AlphaESSNames.LastPollType] == "normal"
        assert coordinator.cloud_available is True
        # Fresh copy each cycle
        assert data[SERIAL] is not coordinator.data[SERIAL]
        # The retired legacy config endpoints are never polled.
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.getDisChargeConfigInfo.assert_not_awaited()

    async def test_poll_with_none_data_initializes(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = None
        _configure_success_api(mock_api)
        data = await coordinator._async_update_data()
        assert SERIAL in data

    async def test_empty_units(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        mock_api.getESSList.return_value = []
        assert await coordinator._async_update_data() == {}

    async def test_unit_without_serial_skipped(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        mock_api.getESSList.return_value = [{"minv": "SMILE5-INV"}]
        data = await coordinator._async_update_data()
        assert data == {}
        assert coordinator.cloud_available is False

    async def test_backoff_skips_inverter(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        coordinator._inverter_error_count[SERIAL] = 3
        coordinator._poll_tick_count = 0  # next tick = 1, 1 % 5 != 0 -> skip

        data = await coordinator._async_update_data()
        assert SERIAL not in data
        mock_api.getSumDataForCustomer.assert_not_awaited()

    async def test_backoff_retry_cycle(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        coordinator._inverter_error_count[SERIAL] = 3
        coordinator._poll_tick_count = 4  # next tick = 5, 5 % 5 == 0 -> retry

        data = await coordinator._async_update_data()
        assert SERIAL in data
        assert coordinator._inverter_error_count[SERIAL] == 0

    async def test_per_inverter_error_counted(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getSumDataForCustomer.side_effect = ValueError("bad data")

        data = await coordinator._async_update_data()
        assert coordinator._inverter_error_count[SERIAL] == 1
        assert coordinator.cloud_available is False
        assert data == {}

    async def test_per_inverter_non_401_response_error(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getSumDataForCustomer.side_effect = _response_error(500)

        await coordinator._async_update_data()
        assert coordinator._inverter_error_count[SERIAL] == 1

    async def test_401_raises_auth_failed(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getSumDataForCustomer.side_effect = _response_error(401)

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_cancelled_error_propagates(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getSumDataForCustomer.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_update_data()

    async def test_cloud_error_no_local_ip_raises_update_failed(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        mock_api.getESSList.side_effect = aiohttp.ClientConnectorError(
            MagicMock(), OSError("no route")
        )
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        assert coordinator.cloud_available is False

    async def test_unexpected_error_no_local_ip_raises_update_failed(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        mock_api.getESSList.side_effect = RuntimeError("boom")
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_cloud_error_falls_back_to_local(self, make_coordinator, mock_api):
        coordinator = make_coordinator(ip_map={SERIAL: "192.168.1.5"})
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        mock_api.getESSList.side_effect = TypeError("NoneType")
        mock_api.getIPData.return_value = {
            "status": {"devstatus": 1},
            "device_info": {"sn": "AL1234"},
        }

        data = await coordinator._async_update_data()
        assert data[SERIAL][AlphaESSNames.localIP] == "192.168.1.5"
        assert data[SERIAL]["Model"] == "SMILE5-INV"

    async def test_local_ip_fetch_during_normal_poll(self, make_coordinator, mock_api):
        coordinator = make_coordinator(ip_map={SERIAL: "192.168.1.5"})
        _configure_success_api(mock_api)
        mock_api.getIPData.return_value = {"status": {"devstatus": 1}}

        data = await coordinator._async_update_data()
        assert data[SERIAL][AlphaESSNames.localIP] == "192.168.1.5"
        assert mock_api.ipaddress is None


class TestFetchInverterData:
    async def test_storion_skips_last_power(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api, model="Storion-S5")

        await coordinator._async_update_data()
        mock_api.getLastPowerData.assert_not_awaited()

    async def test_ev_fetch_dict_payload(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getEvChargerConfigList.return_value = {"evchargerSn": "EV9"}
        mock_api.getEvChargerStatusBySn.return_value = {"evchargerStatus": 1}
        mock_api.getEvChargerCurrentsBySn.return_value = {"currentsetting": 8}

        data = await coordinator._async_update_data()
        assert data[SERIAL][AlphaESSNames.evchargersn] == "EV9"

    async def test_multiple_ev_chargers_use_first_and_report_count_once(
        self, make_coordinator, mock_api, caplog
    ):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getEvChargerConfigList.return_value = [
            {"evchargerSn": "EV1", "evchargerModel": "FIRST"},
            {"evchargerSn": "EV2", "evchargerModel": "SECOND"},
        ]
        mock_api.getEvChargerStatusBySn.return_value = {"evchargerStatus": 1}
        mock_api.getEvChargerCurrentsBySn.return_value = {"currentsetting": 8}
        caplog.set_level(logging.WARNING, logger=coordinator_module.__name__)

        first = await coordinator._async_update_data()
        second = await coordinator._async_update_data()

        for data in (first, second):
            assert data[SERIAL][AlphaESSNames.evchargersn] == "EV1"
            assert data[SERIAL][AlphaESSNames.evchargermodel] == "FIRST"
            assert data[SERIAL][AlphaESSNames.evchargercount] == 2
        assert mock_api.getEvChargerStatusBySn.await_count == 2
        assert all(
            call.args == (SERIAL, "EV1")
            for call in mock_api.getEvChargerStatusBySn.await_args_list
        )
        warnings = [
            record
            for record in caplog.records
            if "returned 2 EV chargers" in record.getMessage()
        ]
        assert len(warnings) == 1

    async def test_ev_fetch_without_serial(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getEvChargerConfigList.return_value = [{"evchargerModel": "X"}]

        await coordinator._async_update_data()
        mock_api.getEvChargerStatusBySn.assert_not_awaited()

    async def test_ev_fetch_error_is_swallowed(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getEvChargerConfigList.side_effect = ValueError("ev api down")

        data = await coordinator._async_update_data()
        assert SERIAL in data  # main fetch still succeeded

    async def test_ev_fetch_cancelled_propagates(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getEvChargerConfigList.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_update_data()

    async def test_include_local_ip_from_client(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.ipaddress = "10.0.0.2"
        mock_api.getIPData.return_value = {"status": {"devstatus": 1}}

        data = await coordinator._async_update_data()
        assert data[SERIAL][AlphaESSNames.localIP] == "10.0.0.2"

    async def test_include_local_ip_empty_payload(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.ipaddress = "10.0.0.2"
        mock_api.getIPData.return_value = None

        data = await coordinator._async_update_data()
        assert AlphaESSNames.localIP not in data[SERIAL]

    async def test_include_local_ip_error_swallowed(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.ipaddress = "10.0.0.2"
        mock_api.getIPData.side_effect = OSError("unreachable")

        data = await coordinator._async_update_data()
        assert SERIAL in data

    async def test_include_local_ip_cancelled_propagates(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.ipaddress = "10.0.0.2"
        mock_api.getIPData.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_update_data()


class TestAltPolling:
    async def test_full_poll(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        _configure_success_api(mock_api)

        data = await coordinator._async_update_data()
        assert data[SERIAL][AlphaESSNames.PollMode] == "alt"
        assert data[SERIAL][AlphaESSNames.LastPollType] == "full"
        assert coordinator._last_full_poll is not None

    async def test_full_poll_empty_units(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        mock_api.getESSList.return_value = []
        assert await coordinator._async_update_data() == {}

    async def test_full_poll_unit_without_serial(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        mock_api.getESSList.return_value = [{"minv": "SMILE5-INV"}]
        data = await coordinator._async_update_data()
        assert data == {}
        assert coordinator.cloud_available is False

    async def test_full_poll_error_counted(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        _configure_success_api(mock_api)
        mock_api.getSumDataForCustomer.side_effect = ValueError("bad")

        await coordinator._async_update_data()
        assert coordinator._inverter_error_count[SERIAL] == 1
        assert coordinator.cloud_available is False

    async def test_full_poll_non_401_response_error(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        _configure_success_api(mock_api)
        mock_api.getSumDataForCustomer.side_effect = _response_error(503)

        await coordinator._async_update_data()
        assert coordinator._inverter_error_count[SERIAL] == 1

    async def test_full_poll_401_raises(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        _configure_success_api(mock_api)
        mock_api.getSumDataForCustomer.side_effect = _response_error(401)

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_full_poll_cancelled_propagates(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        _configure_success_api(mock_api)
        mock_api.getSumDataForCustomer.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_update_data()

    def _prime_fast_mode(self, coordinator, model="SMILE5-INV", ev_sn=None):
        """Set state so the next tick is a fast poll."""
        import time as time_mod

        coordinator._last_full_poll = time_mod.monotonic()
        serial_data = {"Model": model}
        if ev_sn:
            serial_data[AlphaESSNames.evchargersn] = ev_sn
        coordinator.data = {SERIAL: serial_data}

    async def test_fast_poll_power_energy_and_ev(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        self._prime_fast_mode(coordinator, ev_sn="EV123")
        mock_api.getLastPowerData.return_value = {"soc": 60}
        mock_api.getOneDateEnergyBySn.return_value = {"epv": 12.0}
        mock_api.getEvChargerStatusBySn.return_value = {"evchargerStatus": 3}

        data = await coordinator._async_update_data()
        assert data[SERIAL][AlphaESSNames.LastPollType] == "fast"
        assert data[SERIAL][AlphaESSNames.BatterySOC] == 60
        assert data[SERIAL][AlphaESSNames.SolarProduction] == 12.0
        assert data[SERIAL][AlphaESSNames.evchargerstatus] == 3

    async def test_fast_poll_skips_power_for_storion(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        self._prime_fast_mode(coordinator, model="Storion-S5")
        mock_api.getOneDateEnergyBySn.return_value = {"epv": 1.0}

        await coordinator._async_update_data()
        mock_api.getLastPowerData.assert_not_awaited()

    async def test_fast_poll_empty_payloads(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        self._prime_fast_mode(coordinator)
        mock_api.getLastPowerData.return_value = None
        mock_api.getOneDateEnergyBySn.return_value = None

        data = await coordinator._async_update_data()
        assert AlphaESSNames.BatterySOC not in data[SERIAL]

    async def test_fast_poll_ev_status_empty(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        self._prime_fast_mode(coordinator, ev_sn="EV123")
        mock_api.getLastPowerData.return_value = None
        mock_api.getOneDateEnergyBySn.return_value = None
        mock_api.getEvChargerStatusBySn.return_value = None

        data = await coordinator._async_update_data()
        assert AlphaESSNames.evchargerstatus not in data[SERIAL]

    async def test_fast_poll_no_serials(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        import time as time_mod

        coordinator._last_full_poll = time_mod.monotonic()
        coordinator.data = {}

        assert await coordinator._async_update_data() == {}

    async def test_fast_poll_backoff(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        self._prime_fast_mode(coordinator)
        coordinator._inverter_error_count[SERIAL] = 3
        coordinator._poll_tick_count = 0  # tick 1 -> skip

        data = await coordinator._async_update_data()
        assert SERIAL in data
        mock_api.getOneDateEnergyBySn.assert_not_awaited()

    async def test_fast_poll_backoff_retry(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        self._prime_fast_mode(coordinator)
        coordinator._inverter_error_count[SERIAL] = 3
        coordinator._poll_tick_count = 4  # tick 5 -> retry
        mock_api.getLastPowerData.return_value = {"soc": 1}
        mock_api.getOneDateEnergyBySn.return_value = {"epv": 2.0}

        await coordinator._async_update_data()
        assert coordinator._inverter_error_count[SERIAL] == 0

    async def test_fast_poll_error_counted(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        self._prime_fast_mode(coordinator)
        mock_api.getLastPowerData.side_effect = ValueError("bad")

        await coordinator._async_update_data()
        assert coordinator._inverter_error_count[SERIAL] == 1

    async def test_fast_poll_non_401_response_error(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        self._prime_fast_mode(coordinator)
        mock_api.getLastPowerData.side_effect = _response_error(429)

        await coordinator._async_update_data()
        assert coordinator._inverter_error_count[SERIAL] == 1

    async def test_fast_poll_401_raises(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        self._prime_fast_mode(coordinator)
        mock_api.getLastPowerData.side_effect = _response_error(401)

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_fast_poll_cancelled_propagates(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        self._prime_fast_mode(coordinator)
        mock_api.getLastPowerData.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_update_data()

    async def test_round_robin_staggers_inverters(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True)
        import time as time_mod

        coordinator._last_full_poll = time_mod.monotonic()
        coordinator.data = {
            SERIAL: {"Model": "SMILE5-INV"},
            SERIAL2: {"Model": "SMILE5-INV"},
        }
        mock_api.getLastPowerData.return_value = {"soc": 1}
        mock_api.getOneDateEnergyBySn.return_value = {"epv": 1.0}

        await coordinator._async_update_data()
        first = mock_api.getLastPowerData.await_args.args[0]
        await coordinator._async_update_data()
        second = mock_api.getLastPowerData.await_args.args[0]
        assert {first, second} == {SERIAL, SERIAL2}

    async def test_alt_cloud_error_falls_back(self, make_coordinator, mock_api):
        coordinator = make_coordinator(alt=True, ip_map={SERIAL: "192.168.1.5"})
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        mock_api.getESSList.side_effect = aiohttp.ClientConnectorError(
            MagicMock(), OSError("no route")
        )
        mock_api.getIPData.return_value = {"status": {"devstatus": 1}}

        data = await coordinator._async_update_data()
        assert data[SERIAL][AlphaESSNames.localIP] == "192.168.1.5"

    async def test_alt_unexpected_error_raises_when_no_local(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator(alt=True)
        mock_api.getESSList.side_effect = RuntimeError("boom")
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


class TestLocalDataFetch:
    async def test_skip_serial_not_in_data(self, make_coordinator, mock_api):
        coordinator = make_coordinator(ip_map={SERIAL: "192.168.1.5"})
        coordinator.data = {}
        await coordinator._fetch_per_inverter_local_data()
        mock_api.getIPData.assert_not_awaited()

    async def test_skip_no_ip(self, make_coordinator, mock_api):
        coordinator = make_coordinator(ip_map={SERIAL: None})
        coordinator.data = {SERIAL: {}}
        await coordinator._fetch_per_inverter_local_data()
        mock_api.getIPData.assert_not_awaited()

    async def test_skip_if_cloud_provided_local_ip(self, make_coordinator, mock_api):
        coordinator = make_coordinator(ip_map={SERIAL: "192.168.1.5"})
        coordinator.data = {SERIAL: {AlphaESSNames.localIP: "10.0.0.9"}}
        await coordinator._fetch_per_inverter_local_data()
        mock_api.getIPData.assert_not_awaited()

    async def test_fetch_success(self, make_coordinator, mock_api):
        coordinator = make_coordinator(ip_map={SERIAL: "192.168.1.5"})
        coordinator.data = {SERIAL: {}}
        mock_api.getIPData.return_value = {"status": {"devstatus": 1}}

        await coordinator._fetch_per_inverter_local_data()
        assert coordinator.data[SERIAL][AlphaESSNames.localIP] == "192.168.1.5"
        assert mock_api.ipaddress is None

    async def test_fetch_empty_payload(self, make_coordinator, mock_api):
        coordinator = make_coordinator(ip_map={SERIAL: "192.168.1.5"})
        coordinator.data = {SERIAL: {}}
        mock_api.getIPData.return_value = None

        await coordinator._fetch_per_inverter_local_data()
        assert AlphaESSNames.localIP not in coordinator.data[SERIAL]

    async def test_fetch_error_swallowed(self, make_coordinator, mock_api):
        coordinator = make_coordinator(ip_map={SERIAL: "192.168.1.5"})
        coordinator.data = {SERIAL: {}}
        mock_api.getIPData.side_effect = OSError("down")

        await coordinator._fetch_per_inverter_local_data()
        assert mock_api.ipaddress is None


class TestFallbackToLocal:
    async def test_no_ips_raises(self, make_coordinator):
        coordinator = make_coordinator(ip_map={SERIAL: None})
        with pytest.raises(UpdateFailed):
            await coordinator._fallback_to_local_data(RuntimeError("cloud down"))

    async def test_inverter_without_ip_keeps_model_only(self, make_coordinator, mock_api):
        coordinator = make_coordinator(
            ip_map={SERIAL: None, SERIAL2: "192.168.1.6"}
        )
        coordinator.data = {
            SERIAL: {"Model": "SMILE5-INV", AlphaESSNames.TotalLoad: 5},
            SERIAL2: {"Model": "SMILE5-INV"},
        }
        mock_api.getIPData.return_value = {"status": {"devstatus": 1}}

        data = await coordinator._fallback_to_local_data()
        assert data[SERIAL]["Model"] == "SMILE5-INV"
        assert AlphaESSNames.TotalLoad not in data[SERIAL]
        assert data[SERIAL2][AlphaESSNames.localIP] == "192.168.1.6"

    async def test_inverter_without_ip_not_in_data(self, make_coordinator, mock_api):
        coordinator = make_coordinator(
            ip_map={SERIAL: None, SERIAL2: "192.168.1.6"}
        )
        coordinator.data = {SERIAL2: {"Model": "X"}}
        mock_api.getIPData.return_value = {"status": {"devstatus": 1}}

        data = await coordinator._fallback_to_local_data()
        assert SERIAL not in data

    async def test_empty_local_payload_keeps_model(self, make_coordinator, mock_api):
        coordinator = make_coordinator(ip_map={SERIAL: "192.168.1.5"})
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        mock_api.getIPData.return_value = None

        with pytest.raises(UpdateFailed):
            await coordinator._fallback_to_local_data()
        assert coordinator.data[SERIAL] == {"Model": "SMILE5-INV"}

    async def test_local_error_keeps_model_and_raises(self, make_coordinator, mock_api):
        coordinator = make_coordinator(ip_map={SERIAL: "192.168.1.5"})
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        mock_api.getIPData.side_effect = OSError("down")

        with pytest.raises(UpdateFailed):
            await coordinator._fallback_to_local_data(RuntimeError("original"))
        assert coordinator.data[SERIAL] == {"Model": "SMILE5-INV"}
        assert mock_api.ipaddress is None


class TestThrottle:
    async def test_binding_trio_routes_through_shared_gate(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator._call_cloud_api = AsyncMock(
            side_effect=["requested", "bound", "unbound"]
        )
        coordinator._async_wait_for_api_interval = AsyncMock()

        assert (
            await coordinator.async_request_verification_code(SERIAL, "CHECK")
            == "requested"
        )
        assert await coordinator.async_bind_system(SERIAL, "1234") == "bound"
        assert await coordinator.async_unbind_system(SERIAL) == "unbound"

        assert coordinator._call_cloud_api.await_args_list == [
            call(mock_api.getVerificationCode, SERIAL, "CHECK"),
            call(mock_api.bindSn, SERIAL, "1234"),
            call(mock_api.unBindSn, SERIAL),
        ]
        assert coordinator._async_wait_for_api_interval.await_count == 2

    @pytest.mark.parametrize(
        ("method_name", "api_method_name", "args"),
        (
            ("async_bind_system", "bindSn", (SERIAL, "1234")),
            ("async_unbind_system", "unBindSn", (SERIAL,)),
        ),
    )
    async def test_bind_and_unbind_wait_one_interval_after_call(
        self,
        make_coordinator,
        mock_api,
        monkeypatch,
        method_name,
        api_method_name,
        args,
    ):
        coordinator = make_coordinator()
        coordinator.throttle_multiplier = 10.0
        monotonic = MagicMock(side_effect=[100.0, 101.0])
        sleep = AsyncMock()
        fake_time = MagicMock()
        fake_time.monotonic = monotonic
        fake_asyncio = MagicMock()
        fake_asyncio.sleep = sleep
        monkeypatch.setattr(coordinator_module, "time_mod", fake_time)
        monkeypatch.setattr(coordinator_module, "asyncio", fake_asyncio)

        await getattr(coordinator, method_name)(*args)

        sleep.assert_awaited_once_with(9.0)
        getattr(mock_api, api_method_name).assert_awaited_once_with(*args)

    @pytest.mark.parametrize(
        ("method_name", "api_method_name", "args", "error_code"),
        (
            ("async_bind_system", "bindSn", (SERIAL, "1234"), 6003),
            ("async_unbind_system", "unBindSn", (SERIAL,), 6005),
        ),
    )
    async def test_bind_and_unbind_wait_after_idempotent_rejection(
        self,
        make_coordinator,
        mock_api,
        method_name,
        api_method_name,
        args,
        error_code,
    ):
        coordinator = make_coordinator()
        coordinator._async_wait_for_api_interval = AsyncMock()
        getattr(mock_api, api_method_name).side_effect = _api_error(error_code)

        with pytest.raises(AlphaESSApiError):
            await getattr(coordinator, method_name)(*args)

        coordinator._async_wait_for_api_interval.assert_awaited_once_with()

    async def test_read_and_write_share_ten_second_spacing(
        self, make_coordinator, mock_api, monkeypatch
    ):
        coordinator = make_coordinator(models=["SMILE5-INV"])
        coordinator.throttle_multiplier = 10.0
        # Documented-pace lane: the fast lane has its own tests.
        coordinator._fast_api_lane = False
        monotonic = MagicMock(side_effect=[100.0, 101.0, 110.0])
        sleep = AsyncMock()
        fake_time = MagicMock()
        fake_time.monotonic = monotonic
        fake_asyncio = MagicMock()
        fake_asyncio.sleep = sleep
        monkeypatch.setattr(coordinator_module, "time_mod", fake_time)
        monkeypatch.setattr(coordinator_module, "asyncio", fake_asyncio)
        mock_api.getESSList.return_value = []

        await coordinator._read(mock_api.getESSList)
        await coordinator.set_ev_charger_current(SERIAL, 16)

        sleep.assert_awaited_once_with(9.0)
        mock_api.setEvChargerCurrentsBySn.assert_awaited_once_with(
            sysSn=SERIAL, currentsetting=16
        )

    async def test_read_and_write_calls_are_serialized(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        read_entered = asyncio.Event()
        release_read = asyncio.Event()

        async def blocking_read():
            read_entered.set()
            await release_read.wait()
            return []

        mock_api.getESSList.side_effect = blocking_read
        read_task = asyncio.create_task(coordinator._read(mock_api.getESSList))
        await read_entered.wait()
        write_task = asyncio.create_task(
            coordinator.set_ev_charger_current(SERIAL, 16)
        )
        await asyncio.sleep(0)

        mock_api.setEvChargerCurrentsBySn.assert_not_awaited()
        release_read.set()
        await asyncio.gather(read_task, write_task)
        mock_api.setEvChargerCurrentsBySn.assert_awaited_once()


class TestPollWithOpenDraft:
    """A poll must never reset a staged edit (#269: "resets everytime")."""

    @pytest.mark.parametrize("alt", (False, True))
    async def test_full_poll_preserves_draft_view_and_draft(
        self, make_coordinator, mock_api, alt
    ):
        coordinator = make_coordinator(alt=alt)
        _configure_success_api(mock_api)
        mock_api.getTimeChargeBySn.return_value = _periodic_schedule()

        await coordinator._async_update_data()
        assert coordinator.data[SERIAL]["charge_timeChaf1"] == "01:00"

        coordinator.stage_schedule_change(SERIAL, charge={"timeChaf1": "03:00"})
        assert coordinator.data[SERIAL]["charge_timeChaf1"] == "03:00"

        # The next full poll re-reads the OLD periodic times (via the in-poll
        # probe refresh); the published view must keep the draft value and the
        # draft itself must survive.
        coordinator._last_full_poll = None
        result = await coordinator._async_update_data()

        assert result[SERIAL]["charge_timeChaf1"] == "03:00"
        assert coordinator.data[SERIAL]["charge_timeChaf1"] == "03:00"
        assert coordinator.has_schedule_draft(SERIAL)

    async def test_full_poll_updates_committed_view_without_a_draft(
        self, make_coordinator, mock_api
    ):
        """Sanity check: without a draft the poll does refresh the times."""
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getTimeChargeBySn.return_value = _periodic_schedule()

        await coordinator._async_update_data()
        assert coordinator.data[SERIAL]["charge_timeChaf1"] == "01:00"

        changed = _periodic_schedule()
        changed["chargeTimeList"][0]["beginTime"] = "04:00"
        mock_api.getTimeChargeBySn.return_value = changed
        result = await coordinator._async_update_data()

        assert result[SERIAL]["charge_timeChaf1"] == "04:00"
        assert not coordinator.has_schedule_draft(SERIAL)


class TestFirstPollResolvesMode:
    """The schedule mode is resolved inside the first poll, not one poll late."""

    async def test_first_poll_activates_backup_and_serves_schedule_entities(
        self, make_coordinator, mock_api
    ):
        """A 6017 system (the maintainer's probed state) must come up in
        backup mode with its schedule entities available after the very
        first refresh, not one scan interval later."""
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getTimeChargeBySn.side_effect = _api_error(6017)
        mock_api.getChargeConfigInfo.return_value = {
            "batHighCap": 100, "gridCharge": 1,
            "timeChaf1": "11:00", "timeChae1": "14:00",
            "timeChaf2": "00:00", "timeChae2": "00:00",
        }
        mock_api.getDisChargeConfigInfo.return_value = {
            "batUseCap": 10, "ctrDis": 0,
            "timeDisf1": "00:00", "timeDise1": "00:00",
            "timeDisf2": "00:00", "timeDise2": "00:00",
        }

        result = await coordinator._async_update_data()

        assert coordinator.is_legacy_backup_active(SERIAL) is True
        assert result[SERIAL]["charge_timeChaf1"] == "11:00"
        assert result[SERIAL][AlphaESSNames.batUseCap] == 10
        assert coordinator.can_stage_schedule(SERIAL) is True

    async def test_first_poll_resolves_periodic_mode_in_the_same_cycle(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        _configure_success_api(mock_api)
        mock_api.getTimeChargeBySn.return_value = {
            "executeCycleType": 0,
            "gridChargeCycle": 1,
            "ctrDisCycle": 1,
            "chargeTimeList": [
                {"beginTime": "01:00", "endTime": "05:00",
                 "chargeLimit": 90, "chargePower": 3000}
            ],
            "dischargeTimeList": [
                {"beginTime": "18:00", "endTime": "22:00",
                 "chargeLimit": 10, "chargePower": 2500}
            ],
        }

        result = await coordinator._async_update_data()

        assert coordinator.is_periodic_schedule_readable(SERIAL) is True
        assert result[SERIAL]["charge_timeChaf1"] == "01:00"
        assert coordinator.can_stage_schedule(SERIAL) is True
        # The mode probe stays out of the legacy endpoints in periodic mode.
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.getDisChargeConfigInfo.assert_not_awaited()


class TestFastApiLane:
    """All calls run at the live-probed fast spacing; a 6053 drops the
    session back to the documented pace permanently."""

    def test_interval_stays_fast_in_steady_state(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.throttle_multiplier = 10.0

        assert coordinator._call_interval() == 1.0

        # Landing data does not slow the lane down; only a 6053 does.
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        coordinator._finalize_data()
        assert coordinator._fast_api_lane is True
        assert coordinator._call_interval() == 1.0

    async def test_6053_backs_off_and_retries_once(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.throttle_multiplier = 0.0  # keep the retry sleep instant
        mock_api.getSumDataForCustomer.side_effect = [
            _api_error(6053),
            {"eload": 5},
        ]

        result = await coordinator._call_cloud_api(
            mock_api.getSumDataForCustomer, SERIAL
        )

        assert result == {"eload": 5}
        assert coordinator._fast_api_lane is False
        assert mock_api.getSumDataForCustomer.await_count == 2

    async def test_6053_after_the_lane_dropped_propagates_without_retry(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator._fast_api_lane = False
        mock_api.getSumDataForCustomer.side_effect = _api_error(6053)

        with pytest.raises(AlphaESSApiError):
            await coordinator._call_cloud_api(
                mock_api.getSumDataForCustomer, SERIAL
            )

        assert mock_api.getSumDataForCustomer.await_count == 1

    def test_dropped_lane_uses_the_documented_interval(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.throttle_multiplier = 10.0
        coordinator._fast_api_lane = False
        assert coordinator._call_interval() == 10.0


class TestAltPollKeepsTheBackupSnapshot:
    """Alt mode polls on a different path; backup-mode systems still need a
    complete legacy snapshot cached from it, or nothing can be staged."""

    async def test_full_alt_poll_caches_the_legacy_stores(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator(alt=True)
        _configure_success_api(mock_api)
        # A definitive 6017 on the periodic read: this system runs on backup.
        coordinator._periodic_readable[SERIAL] = False
        mock_api.getChargeConfigInfo.return_value = {
            "batHighCap": 90, "gridCharge": 1,
            "timeChaf1": "01:00", "timeChae1": "05:00",
            "timeChaf2": "00:00", "timeChae2": "00:00",
        }
        mock_api.getDisChargeConfigInfo.return_value = {
            "batUseCap": 10, "ctrDis": 1,
            "timeDisf1": "18:00", "timeDise1": "22:00",
            "timeDisf2": "00:00", "timeDise2": "00:00",
        }

        await coordinator._async_update_data()

        cached = coordinator._legacy_schedules.get(SERIAL, {})
        assert cached["charge"]["timeChaf1"] == "01:00"
        assert cached["discharge"]["timeDise1"] == "22:00"
        assert coordinator.can_stage_schedule(SERIAL) is True
        mock_api.getTimeChargeBySn.assert_not_awaited()
