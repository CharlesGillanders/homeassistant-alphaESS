"""Shared fixtures for AlphaESS tests.

These unit tests exercise the integration with explicit mocks instead of the
pytest-homeassistant-custom-component plugin (whose runtime fixtures are
Linux-only), so the suite runs identically on Windows, macOS and Linux/CI.
The plugin is disabled via `-p no:homeassistant` in pyproject.toml.
"""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest
from alphaess.alphaess import alphaess as AlphaESSApiClient

from custom_components.alphaess.coordinator import AlphaESSDataUpdateCoordinator


class FakeEntry:
    """Minimal stand-in for a ConfigEntry."""

    def __init__(self, entry_id="test_entry", data=None, options=None, subentries=None):
        self.entry_id = entry_id
        self.domain = "alphaess"
        self.data = data or {"AppID": "app-id", "AppSecret": "app-secret"}
        self.options = options or {}
        self.subentries = subentries or {}
        self.runtime_data = None
        self.version = 2
        self.state = None
        self.add_update_listener = MagicMock(return_value=MagicMock())
        self.async_on_unload = MagicMock()


class FakeAddEntities:
    """Capture entities passed to async_add_entities."""

    def __init__(self):
        self.entities = []
        self.calls = []

    def __call__(self, new_entities, **kwargs):
        new_entities = list(new_entities)
        self.entities.extend(new_entities)
        self.calls.append((new_entities, kwargs))


@pytest.fixture
def mock_hass():
    """Return a MagicMock standing in for HomeAssistant."""
    hass = MagicMock()
    hass.config.currency = "USD"
    hass.services.async_call = AsyncMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()
    return hass


@pytest.fixture
def mock_api():
    """Return a signature-checked mock of the pinned upstream client."""
    api = create_autospec(AlphaESSApiClient, instance=True)
    api.ipaddress = None
    for method in (
        "getESSList",
        "getSumDataForCustomer",
        "getOneDateEnergyBySn",
        "getLastPowerData",
        "getChargeConfigInfo",
        "getDisChargeConfigInfo",
        "getOneDayPowerBySn",
        "getEvChargerConfigList",
        "getEvChargerStatusBySn",
        "getEvChargerCurrentsBySn",
        "getIPData",
        "updateChargeConfigInfo",
        "updateDisChargeConfigInfo",
        "getTimeChargeBySn",
        "setTimeChargeBySn",
        "setEvChargerCurrentsBySn",
        "remoteControlEvCharger",
        "getVerificationCode",
        "bindSn",
        "unBindSn",
        "authenticate",
    ):
        getattr(api, method).return_value = None
        # autospec names every coroutine mock "AsyncMock"; the coordinator
        # keys its raw-response records by the real method name.
        getattr(api, method).__name__ = method
    return api


@pytest.fixture
def make_coordinator(mock_hass, mock_api):
    """Return a factory producing real coordinators with mocked hass/api."""

    def _make(
        models=None,
        ip_map=None,
        entry=None,
        alt=False,
        scan_seconds=60,
        fast_seconds=15,
    ):
        coordinator = AlphaESSDataUpdateCoordinator(
            mock_hass,
            client=mock_api,
            ip_address_map=ip_map,
            inverter_models=models if models is not None else [],
            entry=entry,
            scan_interval=timedelta(seconds=scan_seconds),
            alt_polling_mode=alt,
            fast_scan_interval=timedelta(seconds=fast_seconds),
        )
        # Keep unit tests fast: no inter-call throttling unless a test
        # explicitly sets it.
        coordinator.throttle_multiplier = 0.0
        coordinator.async_request_refresh = AsyncMock()
        coordinator.async_set_updated_data = MagicMock()
        return coordinator

    return _make


@pytest.fixture
def add_entities():
    """Return a callable capturing added entities."""
    return FakeAddEntities()
