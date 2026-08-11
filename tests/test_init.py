"""Tests for __init__.py setup, services, migration, unload and registry helpers."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)

import custom_components.alphaess as init_mod
from custom_components.alphaess import (
    _cleanup_stale_ev_entities,
    _has_inverter_subentries,
    _migrate_entity_ids,
    _resolve_coordinator_for_serial,
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.alphaess.const import (
    CONF_DISABLE_NOTIFICATIONS,
    CONF_INVERTER_MODEL,
    CONF_IP_ADDRESS,
    CONF_SERIAL_NUMBER,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from custom_components.alphaess.enums import AlphaESSNames

from .conftest import FakeEntry

SERIAL = "AL1000021000123"


def _inverter_subentry(serial=SERIAL, model="SMILE5-INV", ip=""):
    return ConfigSubentry(
        data={
            CONF_SERIAL_NUMBER: serial,
            CONF_INVERTER_MODEL: model,
            CONF_IP_ADDRESS: ip,
            CONF_DISABLE_NOTIFICATIONS: True,
        },
        subentry_type=SUBENTRY_TYPE_INVERTER,
        title=f"{model} ({serial})",
        unique_id=f"{SUBENTRY_TYPE_INVERTER}_{serial}",
    )


def _response_error(status):
    return aiohttp.ClientResponseError(
        request_info=MagicMock(), history=(), status=status, message="x"
    )


class TestHasInverterSubentries:
    def test_true(self):
        sub = _inverter_subentry()
        entry = FakeEntry(subentries={sub.subentry_id: sub})
        assert _has_inverter_subentries(entry) is True

    def test_false(self):
        assert _has_inverter_subentries(FakeEntry()) is False


class FakeRegistryEntry:
    def __init__(self, unique_id, entity_id, domain="sensor", name=None):
        self.unique_id = unique_id
        self.entity_id = entity_id
        self.domain = domain
        self.name = name


class TestMigrateEntityIds:
    def _run(self, monkeypatch, entities, existing_ids=()):
        ent_reg = MagicMock()
        ent_reg.async_get.side_effect = (
            lambda eid: MagicMock() if eid in existing_ids else None
        )
        monkeypatch.setattr(init_mod.er, "async_get", MagicMock(return_value=ent_reg))
        monkeypatch.setattr(
            init_mod.er,
            "async_entries_for_config_entry",
            MagicMock(return_value=entities),
        )
        entry = FakeEntry()
        _migrate_entity_ids(MagicMock(), entry)
        return ent_reg

    def test_renames_matching_entity(self, monkeypatch):
        entity = FakeRegistryEntry(
            f"test_entry_{SERIAL} - Solar Production", "sensor.old_name"
        )
        ent_reg = self._run(monkeypatch, [entity])
        ent_reg.async_update_entity.assert_called_once_with(
            "sensor.old_name",
            new_entity_id=f"sensor.{SERIAL.lower()}_solar_production",
        )

    def test_skips_no_uid_or_wrong_prefix(self, monkeypatch):
        entities = [
            FakeRegistryEntry(None, "sensor.a"),
            FakeRegistryEntry("other_prefix - Name", "sensor.b"),
        ]
        ent_reg = self._run(monkeypatch, entities)
        ent_reg.async_update_entity.assert_not_called()

    def test_skips_without_separator(self, monkeypatch):
        entity = FakeRegistryEntry(f"test_entry_{SERIAL}NoSeparator", "sensor.a")
        ent_reg = self._run(monkeypatch, [entity])
        ent_reg.async_update_entity.assert_not_called()

    def test_skips_already_correct(self, monkeypatch):
        entity = FakeRegistryEntry(
            f"test_entry_{SERIAL} - Solar Production",
            f"sensor.{SERIAL.lower()}_solar_production",
        )
        ent_reg = self._run(monkeypatch, [entity])
        ent_reg.async_update_entity.assert_not_called()

    def test_skips_when_target_taken(self, monkeypatch):
        entity = FakeRegistryEntry(
            f"test_entry_{SERIAL} - Solar Production", "sensor.old"
        )
        ent_reg = self._run(
            monkeypatch,
            [entity],
            existing_ids=(f"sensor.{SERIAL.lower()}_solar_production",),
        )
        ent_reg.async_update_entity.assert_not_called()


class TestCleanupStaleEvEntities:
    def _run(self, monkeypatch, coordinator, entities):
        ent_reg = MagicMock()
        monkeypatch.setattr(init_mod.er, "async_get", MagicMock(return_value=ent_reg))
        monkeypatch.setattr(
            init_mod.er,
            "async_entries_for_config_entry",
            MagicMock(return_value=entities),
        )
        _cleanup_stale_ev_entities(MagicMock(), FakeEntry(), coordinator)
        return ent_reg

    def test_removes_pev_without_charger(self, monkeypatch, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        entity = FakeRegistryEntry(
            f"test_entry_{SERIAL} - {AlphaESSNames.pev.value}", "sensor.pev"
        )
        ent_reg = self._run(monkeypatch, coordinator, [entity])
        ent_reg.async_remove.assert_called_once_with("sensor.pev")

    def test_keeps_pev_with_connector(self, monkeypatch, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.ElectricVehiclePowerOne: 5,
            }
        }
        entity = FakeRegistryEntry(
            f"test_entry_{SERIAL} - {AlphaESSNames.pev.value}", "sensor.pev"
        )
        ent_reg = self._run(monkeypatch, coordinator, [entity])
        ent_reg.async_remove.assert_not_called()

    def test_removes_stale_connector(self, monkeypatch, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {
            SERIAL: {
                AlphaESSNames.evchargersn: "EV123",
                AlphaESSNames.ElectricVehiclePowerOne: 5,
                AlphaESSNames.ElectricVehiclePowerTwo: None,
            }
        }
        entity = FakeRegistryEntry(
            f"test_entry_{SERIAL} - {AlphaESSNames.ElectricVehiclePowerTwo.value}",
            "sensor.ev2",
        )
        ent_reg = self._run(monkeypatch, coordinator, [entity])
        ent_reg.async_remove.assert_called_once_with("sensor.ev2")

    def test_skips_unknown_serial_and_bad_uids(self, monkeypatch, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {}
        entities = [
            FakeRegistryEntry(None, "sensor.a"),
            FakeRegistryEntry("wrong - format", "sensor.b"),
            FakeRegistryEntry(f"test_entry_UNKNOWN - {AlphaESSNames.pev.value}", "sensor.c"),
            FakeRegistryEntry(f"test_entry_{SERIAL} - Unrelated", "sensor.d"),
        ]
        ent_reg = self._run(monkeypatch, coordinator, entities)
        ent_reg.async_remove.assert_not_called()


class TestResolveCoordinator:
    def test_resolves_by_serial(self, mock_hass, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entry = FakeEntry()
        entry.runtime_data = coordinator
        mock_hass.config_entries.async_entries.return_value = [entry]

        assert _resolve_coordinator_for_serial(mock_hass, SERIAL) is coordinator

    def test_falls_back_to_first_coordinator(self, mock_hass, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {"OTHER": {}}
        entry = FakeEntry()
        entry.runtime_data = coordinator
        mock_hass.config_entries.async_entries.return_value = [entry]

        assert _resolve_coordinator_for_serial(mock_hass, SERIAL) is coordinator

    def test_ignores_non_coordinator_runtime_data(self, mock_hass):
        entry = FakeEntry()
        entry.runtime_data = "not-a-coordinator"
        mock_hass.config_entries.async_entries.return_value = [entry]

        with pytest.raises(HomeAssistantError):
            _resolve_coordinator_for_serial(mock_hass, SERIAL)

    def test_raises_without_entries(self, mock_hass):
        mock_hass.config_entries.async_entries.return_value = []
        with pytest.raises(HomeAssistantError):
            _resolve_coordinator_for_serial(mock_hass, SERIAL)


class TestServices:
    async def test_battery_charge_service(self, mock_hass, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entry = FakeEntry()
        entry.runtime_data = coordinator
        mock_hass.config_entries.async_entries.return_value = [entry]

        call = SimpleNamespace(
            hass=mock_hass,
            data={
                "serial": SERIAL,
                "chargestopsoc": 90,
                "enabled": True,
                "cp1start": "01:00",
                "cp1end": "05:00",
                "cp2start": "00:00",
                "cp2end": "00:00",
            },
        )
        await init_mod._async_service_battery_charge(call)
        mock_api.updateChargeConfigInfo.assert_awaited_once_with(
            SERIAL, 90, 1, "05:00", "00:00", "01:00", "00:00"
        )

    async def test_battery_discharge_service(self, mock_hass, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        entry = FakeEntry()
        entry.runtime_data = coordinator
        mock_hass.config_entries.async_entries.return_value = [entry]

        call = SimpleNamespace(
            hass=mock_hass,
            data={
                "serial": SERIAL,
                "dischargecutoffsoc": 10,
                "enabled": False,
                "dp1start": "18:00",
                "dp1end": "22:00",
                "dp2start": "00:00",
                "dp2end": "00:00",
            },
        )
        await init_mod._async_service_battery_discharge(call)
        mock_api.updateDisChargeConfigInfo.assert_awaited_once_with(
            SERIAL, 10, 0, "22:00", "00:00", "18:00", "00:00"
        )


class TestUnloadAndListener:
    async def test_unload_removes_services_when_last(self, mock_hass):
        entry = FakeEntry()
        mock_hass.config_entries.async_unload_platforms.return_value = True
        mock_hass.config_entries.async_entries.return_value = [entry]

        assert await async_unload_entry(mock_hass, entry) is True
        assert mock_hass.services.async_remove.call_count == 2

    async def test_unload_keeps_services_when_others_loaded(self, mock_hass):
        entry = FakeEntry(entry_id="one")
        other = FakeEntry(entry_id="two")
        other.state = ConfigEntryState.LOADED
        mock_hass.config_entries.async_unload_platforms.return_value = True
        mock_hass.config_entries.async_entries.return_value = [entry, other]

        assert await async_unload_entry(mock_hass, entry) is True
        mock_hass.services.async_remove.assert_not_called()

    async def test_unload_failure(self, mock_hass):
        entry = FakeEntry()
        mock_hass.config_entries.async_unload_platforms.return_value = False

        assert await async_unload_entry(mock_hass, entry) is False
        mock_hass.services.async_remove.assert_not_called()


class TestMigrateEntry:
    async def test_version_above_two_unsupported(self, mock_hass):
        entry = FakeEntry()
        entry.version = 3
        assert await async_migrate_entry(mock_hass, entry) is False

    async def test_version_two_noop(self, mock_hass):
        entry = FakeEntry()
        entry.version = 2
        assert await async_migrate_entry(mock_hass, entry) is True
        mock_hass.config_entries.async_update_entry.assert_not_called()

    async def test_version_one_with_ip(self, mock_hass):
        entry = FakeEntry(
            data={"AppID": "id", "AppSecret": "secret", "IPAddress": "192.168.1.4"},
            options={},
        )
        entry.version = 1

        assert await async_migrate_entry(mock_hass, entry) is True
        kwargs = mock_hass.config_entries.async_update_entry.call_args.kwargs
        assert kwargs["version"] == 2
        assert kwargs["options"]["_migrated_ip"] == "192.168.1.4"
        assert kwargs["options"]["_needs_device_cleanup"] is True
        assert "IPAddress" not in kwargs["data"]

    async def test_version_one_without_ip(self, mock_hass):
        entry = FakeEntry(data={"AppID": "id", "AppSecret": "secret"})
        entry.version = 1

        assert await async_migrate_entry(mock_hass, entry) is True
        kwargs = mock_hass.config_entries.async_update_entry.call_args.kwargs
        assert "_migrated_ip" not in kwargs["options"]


class FakeCoordinator:
    """Stand-in for AlphaESSDataUpdateCoordinator inside async_setup_entry."""

    instances = []

    def __init__(self, hass, client=None, ip_address_map=None, inverter_models=None,
                 entry=None, scan_interval=None, alt_polling_mode=False,
                 fast_scan_interval=None):
        self.hass = hass
        self.api = client
        self.ip_address_map = ip_address_map
        self.inverter_models = inverter_models
        self.entry = entry
        self.scan_interval = scan_interval
        self.alt_polling_mode = alt_polling_mode
        self.fast_scan_interval = fast_scan_interval
        self.data = dict(type(self).next_data)
        self.cloud_available = type(self).next_cloud_available
        self.async_config_entry_first_refresh = AsyncMock()
        self.async_probe_periodic_readable = AsyncMock(return_value=False)
        type(self).instances.append(self)

    next_data = {}
    next_cloud_available = True


@pytest.fixture
def setup_env(monkeypatch, mock_hass, mock_api):
    """Patch __init__ collaborators for async_setup_entry tests."""
    FakeCoordinator.instances = []
    FakeCoordinator.next_data = {SERIAL: {"Model": "SMILE5-INV"}}
    FakeCoordinator.next_cloud_available = True

    monkeypatch.setattr(
        init_mod.alphaess, "alphaess", MagicMock(return_value=mock_api)
    )
    monkeypatch.setattr(
        init_mod, "async_get_clientsession", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr(init_mod, "AlphaESSDataUpdateCoordinator", FakeCoordinator)
    monkeypatch.setattr(init_mod, "_cleanup_stale_ev_entities", MagicMock())
    monkeypatch.setattr(init_mod, "_migrate_entity_ids", MagicMock())

    mock_hass.services.has_service.return_value = False
    mock_hass.config_entries.async_entries.return_value = []
    return mock_hass, mock_api


class TestAsyncSetupEntry:
    async def test_basic_setup(self, setup_env):
        mock_hass, mock_api = setup_env
        sub = _inverter_subentry(ip="192.168.1.9")
        entry = FakeEntry(subentries={sub.subentry_id: sub})
        mock_api.getESSList.return_value = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]

        assert await async_setup_entry(mock_hass, entry) is True
        coordinator = FakeCoordinator.instances[0]
        assert entry.runtime_data is coordinator
        assert coordinator.ip_address_map == {SERIAL: "192.168.1.9"}
        coordinator.async_config_entry_first_refresh.assert_awaited_once()
        mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        assert mock_hass.services.async_register.call_count == 2
        # EV cleanup flag stored
        update_kwargs = mock_hass.config_entries.async_update_entry.call_args.kwargs
        assert update_kwargs["options"]["_ev_entity_cleanup_done"] is True

    async def test_services_not_reregistered(self, setup_env):
        mock_hass, mock_api = setup_env
        mock_hass.services.has_service.return_value = True
        sub = _inverter_subentry()
        entry = FakeEntry(
            subentries={sub.subentry_id: sub},
            options={"_ev_entity_cleanup_done": True},
        )
        mock_api.getESSList.return_value = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]

        await async_setup_entry(mock_hass, entry)
        mock_hass.services.async_register.assert_not_called()

    async def test_auth_failure_raises(self, setup_env):
        mock_hass, mock_api = setup_env
        mock_api.getESSList.side_effect = _response_error(401)
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(mock_hass, FakeEntry())

    async def test_response_error_raises_not_ready(self, setup_env):
        mock_hass, mock_api = setup_env
        mock_api.getESSList.side_effect = _response_error(503)
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(mock_hass, FakeEntry())

    async def test_client_error_raises_not_ready(self, setup_env):
        mock_hass, mock_api = setup_env
        mock_api.getESSList.side_effect = aiohttp.ClientError("offline")
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(mock_hass, FakeEntry())

    async def test_auto_creates_subentries_on_migration(self, setup_env):
        mock_hass, mock_api = setup_env
        entry = FakeEntry(
            options={
                "_migrated_ip": "192.168.1.4",
                "_needs_device_cleanup": True,
                "_needs_entity_id_migration": True,
            }
        )
        mock_api.getESSList.return_value = [
            {"sysSn": SERIAL, "minv": "SMILE5-INV"},
            {"minv": "ignored-no-serial"},
        ]

        # device cleanup path
        dev_reg = MagicMock()
        device = MagicMock()
        device.id = "device1"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(init_mod.dr, "async_get", MagicMock(return_value=dev_reg))
            mp.setattr(
                init_mod.dr,
                "async_entries_for_config_entry",
                MagicMock(return_value=[device]),
            )
            assert await async_setup_entry(mock_hass, entry) is True

        # first inverter got the migrated IP via subentry creation
        added = mock_hass.config_entries.async_add_subentry.call_args_list
        assert added, "expected auto-created subentries"
        created = added[0].args[1]
        assert created.data[CONF_SERIAL_NUMBER] == SERIAL
        assert created.data[CONF_IP_ADDRESS] == "192.168.1.4"
        # device cleanup ran
        dev_reg.async_update_device.assert_called_once_with(
            "device1", remove_config_entry_id=entry.entry_id
        )
        # entity-id migration ran
        init_mod._migrate_entity_ids.assert_called_once()

    async def test_ev_subentry_auto_created(self, setup_env):
        mock_hass, mock_api = setup_env
        FakeCoordinator.next_data = {
            SERIAL: {
                "Model": "SMILE5-INV",
                AlphaESSNames.evchargersn: "EV777",
                AlphaESSNames.evchargermodel: "SMILE-EVCT11",
            }
        }
        sub = _inverter_subentry()
        entry = FakeEntry(
            subentries={sub.subentry_id: sub},
            options={"_ev_entity_cleanup_done": True},
        )
        mock_api.getESSList.return_value = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]

        await async_setup_entry(mock_hass, entry)
        added = mock_hass.config_entries.async_add_subentry.call_args_list
        ev_subs = [
            call.args[1] for call in added
            if call.args[1].subentry_type == SUBENTRY_TYPE_EV_CHARGER
        ]
        assert len(ev_subs) == 1
        assert ev_subs[0].data[CONF_SERIAL_NUMBER] == "EV777"

    async def test_cleanup_skipped_when_cloud_unavailable(self, setup_env):
        mock_hass, mock_api = setup_env
        FakeCoordinator.next_cloud_available = False
        sub = _inverter_subentry()
        entry = FakeEntry(subentries={sub.subentry_id: sub})
        mock_api.getESSList.return_value = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]

        await async_setup_entry(mock_hass, entry)
        init_mod._cleanup_stale_ev_entities.assert_not_called()

    async def test_invalid_scan_interval_options(self, setup_env):
        mock_hass, mock_api = setup_env
        sub = _inverter_subentry()
        entry = FakeEntry(
            subentries={sub.subentry_id: sub},
            options={
                "scan_interval_seconds": "not-a-number",
                "fast_scan_interval_seconds": "junk",
                "alt_polling_mode": True,
                "_ev_entity_cleanup_done": True,
            },
        )
        mock_api.getESSList.return_value = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]

        await async_setup_entry(mock_hass, entry)
        coordinator = FakeCoordinator.instances[0]
        assert coordinator.scan_interval.total_seconds() == 60
        assert coordinator.fast_scan_interval.total_seconds() == 15
        assert coordinator.alt_polling_mode is True
