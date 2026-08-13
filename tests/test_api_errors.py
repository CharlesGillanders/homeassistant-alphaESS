"""Tests for AlphaESSApiError handling.

The client runs with raise_on_error=True so schedule writes can tell an accepted
schedule from a rejected one. That means API-level rejections now arrive as
exceptions everywhere, including on reads that used to just return None — these
tests pin down which paths absorb them and which don't.
"""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from alphaess.alphaess import AlphaESSApiError
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

import custom_components.alphaess as init_mod
from custom_components.alphaess import async_setup_entry
from custom_components.alphaess.coordinator import PERIODIC_NOT_ENTITLED

from .conftest import FakeEntry
from .test_periodic_schedule import SERIAL, _schedule_data


def _api_error(code, expMsg=None):
    return AlphaESSApiError(code=code, msg="rejected", expMsg=expMsg,
                            path="https://openapi.alphaess.com/api/x",
                            description="desc")


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
