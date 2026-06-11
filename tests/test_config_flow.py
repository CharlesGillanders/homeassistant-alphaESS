"""Tests for the AlphaESS config, options, reauth and subentry flows."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.alphaess import config_flow
from custom_components.alphaess.config_flow import (
    AlphaESSConfigFlow,
    AlphaESSInverterSubentryFlowHandler,
    AlphaESSOptionsFlowHandler,
    CannotConnect,
    InvalidAuth,
    validate_input,
)
from custom_components.alphaess.const import (
    CONF_DISABLE_NOTIFICATIONS,
    CONF_IP_ADDRESS,
    CONF_SERIAL_NUMBER,
    SUBENTRY_TYPE_INVERTER,
)

from .conftest import FakeEntry

USER_INPUT = {
    "AppID": "app-id",
    "AppSecret": "app-secret",
    "Verify SSL Certificate": True,
}


def _response_error(status):
    return aiohttp.ClientResponseError(
        request_info=MagicMock(), history=(), status=status, message="x"
    )


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    monkeypatch.setattr(config_flow.asyncio, "sleep", AsyncMock())


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    client.authenticate = AsyncMock(return_value=True)
    client.getESSList = AsyncMock(
        return_value=[{"sysSn": "AL123", "minv": "SMILE5-INV"}]
    )
    monkeypatch.setattr(
        config_flow.alphaess, "alphaess", MagicMock(return_value=client)
    )
    return client


class TestValidateInput:
    async def test_success(self, mock_hass, mock_client):
        result = await validate_input(mock_hass, USER_INPUT)
        assert result["title"] == "app-id"
        assert result["ess_list"][0]["sysSn"] == "AL123"

    async def test_empty_ess_list(self, mock_hass, mock_client):
        mock_client.getESSList.return_value = None
        result = await validate_input(mock_hass, USER_INPUT)
        assert result["ess_list"] == []

    async def test_invalid_auth(self, mock_hass, mock_client):
        mock_client.authenticate.side_effect = _response_error(401)
        with pytest.raises(InvalidAuth):
            await validate_input(mock_hass, USER_INPUT)

    async def test_other_response_error(self, mock_hass, mock_client):
        mock_client.authenticate.side_effect = _response_error(500)
        with pytest.raises(aiohttp.ClientResponseError):
            await validate_input(mock_hass, USER_INPUT)

    async def test_cannot_connect(self, mock_hass, mock_client):
        mock_client.authenticate.side_effect = aiohttp.ClientConnectorError(
            MagicMock(), OSError("no route")
        )
        with pytest.raises(CannotConnect):
            await validate_input(mock_hass, USER_INPUT)


def _make_flow(mock_hass):
    flow = AlphaESSConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    return flow


class TestUserFlow:
    async def test_form_shown_without_input(self, mock_hass):
        flow = _make_flow(mock_hass)
        result = await flow.async_step_user(None)
        assert result["type"] == "form"
        assert result["step_id"] == "user"

    async def test_create_entry(self, mock_hass, mock_client):
        flow = _make_flow(mock_hass)
        result = await flow.async_step_user(dict(USER_INPUT))
        assert result["type"] == "create_entry"
        assert result["title"] == "app-id"
        assert result["data"]["AppID"] == "app-id"
        subentries = result["subentries"]
        assert len(subentries) == 1
        assert subentries[0]["data"][CONF_SERIAL_NUMBER] == "AL123"

    async def test_unit_without_serial_skipped(self, mock_hass, mock_client):
        mock_client.getESSList.return_value = [{"minv": "X"}]
        flow = _make_flow(mock_hass)
        result = await flow.async_step_user(dict(USER_INPUT))
        assert list(result["subentries"]) == []

    async def test_cannot_connect_error(self, mock_hass, mock_client):
        mock_client.authenticate.side_effect = aiohttp.ClientConnectorError(
            MagicMock(), OSError("no route")
        )
        flow = _make_flow(mock_hass)
        result = await flow.async_step_user(dict(USER_INPUT))
        assert result["type"] == "form"
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_invalid_auth_error(self, mock_hass, mock_client):
        mock_client.authenticate.side_effect = _response_error(401)
        flow = _make_flow(mock_hass)
        result = await flow.async_step_user(dict(USER_INPUT))
        assert result["errors"] == {"base": "invalid_auth"}

    def test_options_flow_factory(self):
        entry = FakeEntry()
        handler = AlphaESSConfigFlow.async_get_options_flow(entry)
        assert isinstance(handler, AlphaESSOptionsFlowHandler)

    def test_subentry_types(self):
        types = AlphaESSConfigFlow.async_get_supported_subentry_types(FakeEntry())
        assert types == {SUBENTRY_TYPE_INVERTER: AlphaESSInverterSubentryFlowHandler}


class TestReauthFlow:
    def _make_reauth_flow(self, mock_hass, entry):
        flow = _make_flow(mock_hass)
        flow._get_reauth_entry = MagicMock(return_value=entry)
        flow.async_update_reload_and_abort = MagicMock(
            return_value={"type": "abort", "reason": "reauth_successful"}
        )
        return flow

    async def test_reauth_shows_form(self, mock_hass):
        entry = FakeEntry()
        flow = self._make_reauth_flow(mock_hass, entry)
        result = await flow.async_step_reauth({})
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"

    async def test_reauth_success(self, mock_hass, mock_client):
        entry = FakeEntry()
        flow = self._make_reauth_flow(mock_hass, entry)
        result = await flow.async_step_reauth_confirm({"AppSecret": "new-secret"})
        assert result["reason"] == "reauth_successful"
        flow.async_update_reload_and_abort.assert_called_once_with(
            entry, data_updates={"AppSecret": "new-secret"}
        )

    async def test_reauth_invalid_auth(self, mock_hass, mock_client):
        mock_client.authenticate.side_effect = _response_error(401)
        entry = FakeEntry()
        flow = self._make_reauth_flow(mock_hass, entry)
        result = await flow.async_step_reauth_confirm({"AppSecret": "bad"})
        assert result["errors"] == {"base": "invalid_auth"}

    async def test_reauth_cannot_connect(self, mock_hass, mock_client):
        mock_client.authenticate.side_effect = aiohttp.ClientConnectorError(
            MagicMock(), OSError("x")
        )
        entry = FakeEntry()
        flow = self._make_reauth_flow(mock_hass, entry)
        result = await flow.async_step_reauth_confirm({"AppSecret": "bad"})
        assert result["errors"] == {"base": "cannot_connect"}


class TestOptionsFlow:
    def _make(self, options=None):
        entry = FakeEntry(options=options or {})
        handler = AlphaESSOptionsFlowHandler(entry)
        return handler

    async def test_form_shown(self):
        handler = self._make()
        result = await handler.async_step_init(None)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

    async def test_save_preserves_internal_flags(self):
        handler = self._make(options={"_ev_entity_cleanup_done": True})
        result = await handler.async_step_init({"scan_interval_seconds": 120})
        assert result["type"] == "create_entry"
        assert result["data"]["_ev_entity_cleanup_done"] is True
        assert result["data"]["scan_interval_seconds"] == 120


def _make_subentry_flow(mock_hass, api):
    flow = AlphaESSInverterSubentryFlowHandler()
    flow.hass = mock_hass
    flow.context = {"source": "user"}
    entry = FakeEntry()
    entry.runtime_data = SimpleNamespace(api=api)
    flow._get_entry = MagicMock(return_value=entry)
    return flow, entry


class TestSubentryUserStep:
    async def test_form_shown(self, mock_hass, mock_api):
        flow, _ = _make_subentry_flow(mock_hass, mock_api)
        result = await flow.async_step_user(None)
        assert result["type"] == "form"
        assert result["step_id"] == "user"

    async def test_verification_requested(self, mock_hass, mock_api):
        mock_api.getVerificationCode.return_value = {"ok": True}
        flow, _ = _make_subentry_flow(mock_hass, mock_api)
        result = await flow.async_step_user(
            {"serial_number": " AL999 ", "check_code": " CODE "}
        )
        mock_api.getVerificationCode.assert_awaited_once_with("AL999", "CODE")
        assert result["step_id"] == "verify"
        assert flow._sysSn == "AL999"

    async def test_verification_request_returns_none(self, mock_hass, mock_api):
        mock_api.getVerificationCode.return_value = None
        flow, _ = _make_subentry_flow(mock_hass, mock_api)
        result = await flow.async_step_user(
            {"serial_number": "AL999", "check_code": "CODE"}
        )
        assert result["errors"] == {"base": "verification_request_failed"}

    async def test_verification_request_raises(self, mock_hass, mock_api):
        mock_api.getVerificationCode.side_effect = OSError("api down")
        flow, _ = _make_subentry_flow(mock_hass, mock_api)
        result = await flow.async_step_user(
            {"serial_number": "AL999", "check_code": "CODE"}
        )
        assert result["errors"] == {"base": "verification_request_failed"}


class TestSubentryVerifyStep:
    async def test_form_shown(self, mock_hass, mock_api):
        flow, _ = _make_subentry_flow(mock_hass, mock_api)
        flow._sysSn = "AL999"
        result = await flow.async_step_verify(None)
        assert result["type"] == "form"
        assert result["step_id"] == "verify"

    async def test_bind_success(self, mock_hass, mock_api):
        mock_api.bindSn.return_value = {"ok": True}
        flow, entry = _make_subentry_flow(mock_hass, mock_api)
        flow._sysSn = "AL999"
        result = await flow.async_step_verify({"verification_code": " 1234 "})
        mock_api.bindSn.assert_awaited_once_with("AL999", "1234")
        assert result["type"] == "create_entry"
        assert result["data"][CONF_SERIAL_NUMBER] == "AL999"
        mock_hass.async_create_task.assert_called_once()

    async def test_bind_returns_none(self, mock_hass, mock_api):
        mock_api.bindSn.return_value = None
        flow, _ = _make_subentry_flow(mock_hass, mock_api)
        flow._sysSn = "AL999"
        result = await flow.async_step_verify({"verification_code": "1234"})
        assert result["errors"] == {"base": "bind_failed"}

    async def test_bind_raises(self, mock_hass, mock_api):
        mock_api.bindSn.side_effect = OSError("api down")
        flow, _ = _make_subentry_flow(mock_hass, mock_api)
        flow._sysSn = "AL999"
        result = await flow.async_step_verify({"verification_code": "1234"})
        assert result["errors"] == {"base": "bind_failed"}


def _make_reconfigure_flow(mock_hass, api, subentry_data=None):
    flow, entry = _make_subentry_flow(mock_hass, api)
    subentry = SimpleNamespace(
        data=subentry_data
        or {
            CONF_SERIAL_NUMBER: "AL999",
            CONF_IP_ADDRESS: "",
            CONF_DISABLE_NOTIFICATIONS: True,
        },
        subentry_id="sub1",
    )
    flow._get_reconfigure_subentry = MagicMock(return_value=subentry)
    flow.async_update_and_abort = MagicMock(
        return_value={"type": "abort", "reason": "reconfigure_successful"}
    )
    flow.async_remove_and_abort = MagicMock(
        return_value={"type": "abort", "reason": "reconfigure_successful"}
    )
    return flow, entry, subentry


class TestSubentryReconfigureStep:
    async def test_form_shown(self, mock_hass, mock_api):
        flow, _, _ = _make_reconfigure_flow(mock_hass, mock_api)
        result = await flow.async_step_reconfigure(None)
        assert result["type"] == "form"
        assert result["step_id"] == "reconfigure"

    async def test_save_ip(self, mock_hass, mock_api):
        flow, entry, subentry = _make_reconfigure_flow(mock_hass, mock_api)
        result = await flow.async_step_reconfigure(
            {CONF_IP_ADDRESS: " 192.168.1.7 ", CONF_DISABLE_NOTIFICATIONS: False}
        )
        assert result["reason"] == "reconfigure_successful"
        saved = flow.async_update_and_abort.call_args.kwargs["data"]
        assert saved[CONF_IP_ADDRESS] == "192.168.1.7"
        assert saved[CONF_DISABLE_NOTIFICATIONS] is False

    async def test_save_empty_ip(self, mock_hass, mock_api):
        flow, _, _ = _make_reconfigure_flow(mock_hass, mock_api)
        result = await flow.async_step_reconfigure({CONF_IP_ADDRESS: ""})
        assert result["reason"] == "reconfigure_successful"

    async def test_invalid_ip(self, mock_hass, mock_api):
        flow, _, _ = _make_reconfigure_flow(mock_hass, mock_api)
        result = await flow.async_step_reconfigure({CONF_IP_ADDRESS: "not-an-ip"})
        assert result["errors"] == {"base": "invalid_ip"}

    async def test_unbind_success(self, mock_hass, mock_api):
        mock_api.unBindSn.return_value = {"ok": True}
        flow, _, _ = _make_reconfigure_flow(mock_hass, mock_api)
        result = await flow.async_step_reconfigure({"confirm_unbind": True})
        assert result["reason"] == "reconfigure_successful"
        flow.async_remove_and_abort.assert_called_once()
        mock_hass.async_create_task.assert_called_once()

    async def test_unbind_returns_none(self, mock_hass, mock_api):
        mock_api.unBindSn.return_value = None
        flow, _, _ = _make_reconfigure_flow(mock_hass, mock_api)
        result = await flow.async_step_reconfigure({"confirm_unbind": True})
        assert result["errors"] == {"base": "unbind_failed"}

    async def test_unbind_raises(self, mock_hass, mock_api):
        mock_api.unBindSn.side_effect = OSError("api down")
        flow, _, _ = _make_reconfigure_flow(mock_hass, mock_api)
        result = await flow.async_step_reconfigure({"confirm_unbind": True})
        assert result["errors"] == {"base": "unbind_failed"}
