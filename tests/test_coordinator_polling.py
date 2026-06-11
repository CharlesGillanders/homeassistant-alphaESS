"""Tests for AlphaESSDataUpdateCoordinator polling, control and fallback logic."""
import asyncio
from unittest.mock import MagicMock

import aiohttp
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.alphaess.const import (
    CONF_SERIAL_NUMBER,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from custom_components.alphaess.enums import AlphaESSNames

SERIAL = "AL1000021000123"
SERIAL2 = "AL2000021000456"


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
    def test_throttle_disabled_for_modern_inverters(self, make_coordinator):
        coordinator = make_coordinator(models=["SMILE5-INV", "SMILE-T10-HV-INV"])
        assert coordinator.has_throttle is False
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

    async def test_control_ev_blocked(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 3}}
        await coordinator.control_ev(SERIAL, "EV123", "1")
        mock_api.remoteControlEvCharger.assert_not_awaited()

    async def test_control_ev_allowed(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {AlphaESSNames.evchargerstatusraw: 2}}
        await coordinator.control_ev(SERIAL, "EV123", "1")
        mock_api.remoteControlEvCharger.assert_awaited_once_with(SERIAL, "EV123", "1")

    async def test_set_ev_charger_current(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        await coordinator.set_ev_charger_current(SERIAL, 16)
        mock_api.setEvChargerCurrentsBySn.assert_awaited_once_with(SERIAL, 16)
        coordinator.async_request_refresh.assert_awaited_once()


class TestChargeDischargeCommands:
    async def test_reset_config(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.set_number_setting(SERIAL, "batUseCap", 20)
        coordinator.set_number_setting(SERIAL, "batHighCap", 80)
        coordinator.data = {SERIAL: {}}

        await coordinator.reset_config(SERIAL)

        mock_api.updateChargeConfigInfo.assert_awaited_once_with(
            SERIAL, 80, 1, "00:00", "00:00", "00:00", "00:00"
        )
        mock_api.updateDisChargeConfigInfo.assert_awaited_once_with(
            SERIAL, 20, 1, "00:00", "00:00", "00:00", "00:00"
        )
        assert coordinator.data[SERIAL]["gridCharge"] == 1
        assert coordinator.data[SERIAL]["ctrDis"] == 1
        coordinator.async_set_updated_data.assert_called_once()

    async def test_reset_config_serial_not_in_data(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {}
        await coordinator.reset_config(SERIAL)
        coordinator.async_set_updated_data.assert_not_called()

    async def test_update_discharge(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.set_number_setting(SERIAL, "batUseCap", 15)
        coordinator.data = {SERIAL: {}}

        await coordinator.update_discharge("batUseCap", SERIAL, 30)

        args = mock_api.updateDisChargeConfigInfo.await_args.args
        assert args[0] == SERIAL
        assert args[1] == 15
        assert args[2] == 1
        assert coordinator.data[SERIAL]["ctrDis"] == 1
        coordinator.async_set_updated_data.assert_called_once()

    async def test_update_discharge_serial_missing(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {}
        await coordinator.update_discharge("batUseCap", SERIAL, 30)
        coordinator.async_set_updated_data.assert_not_called()

    async def test_update_charge(self, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.set_number_setting(SERIAL, "batHighCap", 85)
        coordinator.data = {SERIAL: {}}

        await coordinator.update_charge("batHighCap", SERIAL, 60)

        args = mock_api.updateChargeConfigInfo.await_args.args
        assert args[1] == 85
        assert coordinator.data[SERIAL]["gridCharge"] == 1
        coordinator.async_set_updated_data.assert_called_once()

    async def test_update_charge_serial_missing(self, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {}
        await coordinator.update_charge("batHighCap", SERIAL, 60)
        coordinator.async_set_updated_data.assert_not_called()


def _configure_success_api(mock_api, serial=SERIAL, model="SMILE5-INV", with_ev=False):
    """Configure mock API for one successful inverter fetch."""
    mock_api.getESSList.return_value = [{"sysSn": serial, "minv": model}]
    mock_api.getSumDataForCustomer.return_value = {"eload": 5, "moneyType": "USD"}
    mock_api.getOneDateEnergyBySn.return_value = {"epv": 10.0, "eOutput": 1.0}
    mock_api.getLastPowerData.return_value = {"soc": 50, "pload": 100}
    mock_api.getChargeConfigInfo.return_value = {"gridCharge": 1, "batHighCap": 90}
    mock_api.getDisChargeConfigInfo.return_value = {"ctrDis": 1, "batUseCap": 10}
    mock_api.getOneDayPowerBySn.return_value = [{"cbat": 55}]
    if with_ev:
        mock_api.getEvChargerConfigList.return_value = [
            {"evchargerSn": "EV123", "evchargerModel": "SMILE-EVCT11"}
        ]
        mock_api.getEvChargerStatusBySn.return_value = {"evchargerStatus": 2}
        mock_api.getEvChargerCurrentsBySn.return_value = {"currentsetting": 16}
    else:
        mock_api.getEvChargerConfigList.return_value = None


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
    async def test_throttle_delay_used(self, make_coordinator, mock_api, monkeypatch):
        coordinator = make_coordinator(models=["SMILE5-INV"])
        coordinator.throttle_multiplier = 1.25  # restore real value
        _configure_success_api(mock_api)

        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        await coordinator._async_update_data()
        assert 1.25 in sleeps
