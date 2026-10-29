"""Tests for __init__.py setup, services, migration, unload and registry helpers."""
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
import voluptuous as vol
from alphaess.alphaess import AlphaESSApiError
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigSubentry
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

import custom_components.alphaess as init_mod
from custom_components.alphaess import (
    _cleanup_stale_ev_entities,
    _has_inverter_subentries,
    _migrate_devices_to_subentries,
    _migrate_entity_ids,
    _resolve_coordinator_for_serial,
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.alphaess.const import (
    CONF_ASSUME_SCHEDULE_FLAGS,
    CONF_DISABLE_NOTIFICATIONS,
    CONF_INVERTER_MODEL,
    CONF_IP_ADDRESS,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from custom_components.alphaess.enums import AlphaESSNames

from .conftest import FakeEntry

SERIAL = "AL1000021000123"


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


def _charge_service_data(soc):
    return {
        "serial": SERIAL,
        "chargestopsoc": soc,
        "enabled": True,
        "cp1start": "01:00",
        "cp1end": "05:00",
        "cp2start": "00:00",
        "cp2end": "00:00",
    }


def _discharge_service_data(soc):
    return {
        "serial": SERIAL,
        "dischargecutoffsoc": soc,
        "enabled": False,
        "dp1start": "18:00",
        "dp1end": "22:00",
        "dp2start": "00:00",
        "dp2end": "00:00",
    }


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


class TestMigrateDevicesToSubentries:
    async def test_real_registries_preserve_entities_after_the_device_move_event(self, tmp_path):
        """Exercise Core's validation and device-move listener, not a mock API."""
        hass = HomeAssistant(str(tmp_path))
        entry = ConfigEntry(
            data={}, discovery_keys=MappingProxyType({}), domain=DOMAIN,
            minor_version=1, options={}, source="user", title="AlphaESS",
            unique_id="account", version=2,
            subentries_data=[{
                "data": {CONF_SERIAL_NUMBER: SERIAL},
                "subentry_id": "inverter-subentry", "subentry_type": SUBENTRY_TYPE_INVERTER,
                "title": "Inverter", "unique_id": f"inverter_{SERIAL}",
            }],
        )
        hass.config_entries = MagicMock()
        hass.config_entries.async_wait_initialized = AsyncMock(return_value=None)
        hass.config_entries.async_get_entry.return_value = entry
        dr.async_setup(hass)
        await dr.async_load(hass, load_empty=True)
        await er.async_load(hass, load_empty=True)
        await ar.async_load(hass, load_empty=True)
        dev_reg = dr.async_get(hass)
        ent_reg = er.async_get(hass)
        area = ar.async_get(hass).async_create("Garage")
        try:
            device = dev_reg.async_get_or_create(
                config_entry_id=entry.entry_id, identifiers={(DOMAIN, SERIAL)},
                name="Legacy inverter", disabled_by=dr.DeviceEntryDisabler.USER,
            )
            device = dev_reg.async_update_device(
                device.id, area_id=area.id, name_by_user="My solar inverter",
            )
            entity = ent_reg.async_get_or_create(
                "sensor", DOMAIN, "legacy-sensor-uid", config_entry=entry,
                device_id=device.id, disabled_by=er.RegistryEntryDisabler.USER,
            )
            entity = ent_reg.async_update_entity(
                entity.entity_id, name="My solar output", area_id=area.id,
            )
            await hass.async_block_till_done()

            assert _migrate_devices_to_subentries(hass, entry) is True
            # Flush the real registry's device-update listener. Moving the device
            # before its entities would delete the entity here.
            await hass.async_block_till_done()

            migrated_device = dev_reg.async_get(device.id)
            migrated_entity = ent_reg.async_get(entity.entity_id)
            assert migrated_device.config_subentry_id == "inverter-subentry"
            assert migrated_device.name_by_user == "My solar inverter"
            assert migrated_device.area_id == area.id
            assert migrated_device.disabled_by is dr.DeviceEntryDisabler.USER
            assert migrated_entity is not None
            assert migrated_entity.id == entity.id
            assert migrated_entity.unique_id == entity.unique_id
            assert migrated_entity.device_id == device.id
            assert migrated_entity.config_subentry_id == "inverter-subentry"
            assert migrated_entity.name == "My solar output"
            assert migrated_entity.area_id == area.id
            assert migrated_entity.disabled_by is er.RegistryEntryDisabler.USER
            assert not ent_reg.deleted_entities
        finally:
            await hass.async_stop(force=True)

    def _registries(self, monkeypatch, devices, entities):
        dev_reg = MagicMock(spec=["async_update_device", "async_remove_device"])
        ent_reg = MagicMock(spec=["async_update_entity", "async_remove"])

        def update_entity(entity_id, *, config_subentry_id):
            next(entity for entity in entities if entity.entity_id == entity_id).config_subentry_id = (
                config_subentry_id
            )

        def update_device(device_id, *, new_config_subentry_id):
            device = next(device for device in devices if device.id == device_id)
            # Core deletes old-subentry entities when a device moves. Ensuring
            # those records were moved first protects their IDs and overrides.
            assert all(
                entity.config_subentry_id == new_config_subentry_id
                for entity in entities
                if entity.device_id == device_id and entity.config_entry_id == "test_entry"
            )
            device.config_subentry_id = new_config_subentry_id

        dev_reg.async_update_device.side_effect = update_device
        ent_reg.async_update_entity.side_effect = update_entity
        monkeypatch.setattr(init_mod.dr, "async_get", MagicMock(return_value=dev_reg))
        monkeypatch.setattr(init_mod.er, "async_get", MagicMock(return_value=ent_reg))
        monkeypatch.setattr(
            init_mod.dr, "async_entries_for_config_entry", MagicMock(return_value=devices),
        )
        monkeypatch.setattr(
            init_mod.er, "async_entries_for_device",
            MagicMock(side_effect=lambda _reg, device_id, *, include_disabled_entities: [
                entity for entity in entities if entity.device_id == device_id
            ]),
        )
        return dev_reg, ent_reg

    def test_devices_entities_and_user_overrides_survive_idempotent_migration(self, monkeypatch):
        inverter = _inverter_subentry()
        charger = ConfigSubentry(
            data={CONF_SERIAL_NUMBER: "Ev777"},
            subentry_type=SUBENTRY_TYPE_EV_CHARGER, title="Charger", unique_id="charger",
        )
        entry = FakeEntry(subentries={
            sub.subentry_id: sub for sub in (inverter, charger)
        })
        devices = [
            SimpleNamespace(
                id=device_id, identifiers={(DOMAIN, serial.lower())},
                config_subentry_id=None, name_by_user="My device", area_id="garage",
                labels={"energy"}, disabled_by="user",
            )
            for device_id, serial in (("inverter", SERIAL), ("charger", "EV777"))
        ]
        entities = [
            SimpleNamespace(
                entity_id=f"sensor.{device.id}", unique_id=f"custom_{device.id}",
                device_id=device.id, config_entry_id=entry.entry_id,
                config_subentry_id=None, name="My sensor", disabled_by="user",
            )
            for device in devices
        ]
        # An external helper's link must remain on its own config entry.
        external = SimpleNamespace(
            entity_id="sensor.helper", device_id="inverter",
            config_entry_id="another_entry", config_subentry_id=None,
        )
        entities.append(external)
        original_devices = [vars(device).copy() for device in devices]
        original_entities = [vars(entity).copy() for entity in entities]
        dev_reg, ent_reg = self._registries(monkeypatch, devices, entities)

        assert _migrate_devices_to_subentries(MagicMock(), entry) is True
        assert _migrate_devices_to_subentries(MagicMock(), entry) is True

        for device, original, subentry in zip(devices, original_devices, (inverter, charger), strict=True):
            assert vars(device) == {**original, "config_subentry_id": subentry.subentry_id}
        for entity, original, subentry in zip(entities[:2], original_entities[:2], (inverter, charger), strict=True):
            assert vars(entity) == {**original, "config_subentry_id": subentry.subentry_id}
        assert vars(external) == original_entities[-1]
        assert dev_reg.async_update_device.call_count == 2
        assert ent_reg.async_update_entity.call_count == 2
        dev_reg.async_remove_device.assert_not_called()
        ent_reg.async_remove.assert_not_called()
        assert all(
            call.kwargs == {"include_disabled_entities": True}
            for call in init_mod.er.async_entries_for_device.call_args_list
        )

    @pytest.mark.parametrize("problem", ["unmatched", "duplicate-serial", "multiple-identifiers"])
    def test_unmatched_or_ambiguous_devices_are_preserved_for_retry(self, monkeypatch, caplog, problem):
        inverter = _inverter_subentry()
        other = _inverter_subentry(serial=SERIAL if problem == "duplicate-serial" else "OTHER")
        blank = _inverter_subentry(serial="")
        unsupported = ConfigSubentry(
            data={CONF_SERIAL_NUMBER: "UNKNOWN"}, subentry_type="unsupported",
            title="Unsupported", unique_id="unsupported",
        )
        entry = FakeEntry(subentries={
            sub.subentry_id: sub for sub in (inverter, other, blank, unsupported)
        })
        identifiers = {(DOMAIN, SERIAL)}
        if problem == "unmatched":
            identifiers = {(DOMAIN, "UNKNOWN"), ("other_domain", SERIAL)}
        elif problem == "multiple-identifiers":
            identifiers.add((DOMAIN, "OTHER"))
        device = SimpleNamespace(id="unresolved", identifiers=identifiers, config_subentry_id=None)
        dev_reg, ent_reg = self._registries(monkeypatch, [device], [])

        assert _migrate_devices_to_subentries(MagicMock(), entry) is False

        assert device.config_subentry_id is None
        dev_reg.async_update_device.assert_not_called()
        dev_reg.async_remove_device.assert_not_called()
        ent_reg.async_update_entity.assert_not_called()
        assert "Deferring subentry migration for device unresolved" in caplog.text

    def test_no_devices_need_no_migration(self, monkeypatch):
        dev_reg, ent_reg = self._registries(monkeypatch, [], [])

        assert _migrate_devices_to_subentries(MagicMock(), FakeEntry()) is True

        dev_reg.async_update_device.assert_not_called()
        ent_reg.async_update_entity.assert_not_called()


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

    def test_unknown_serial_does_not_fall_back(self, mock_hass, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {"OTHER": {}}
        entry = FakeEntry()
        entry.runtime_data = coordinator
        mock_hass.config_entries.async_entries.return_value = [entry]

        with pytest.raises(HomeAssistantError) as exc_info:
            _resolve_coordinator_for_serial(mock_hass, SERIAL)
        assert str(exc_info.value) == (
            "No loaded AlphaESS config entry currently manages serial " f"{SERIAL}"
        )

    def test_resolves_exact_serial_across_multiple_entries(
        self, mock_hass, make_coordinator
    ):
        wrong_coordinator = make_coordinator()
        wrong_coordinator.data = {"OTHER": {}}
        right_coordinator = make_coordinator()
        right_coordinator.data = {SERIAL: {}}
        wrong_entry = FakeEntry(entry_id="wrong")
        wrong_entry.runtime_data = wrong_coordinator
        right_entry = FakeEntry(entry_id="right")
        right_entry.runtime_data = right_coordinator
        mock_hass.config_entries.async_entries.return_value = [
            wrong_entry,
            right_entry,
        ]

        assert _resolve_coordinator_for_serial(mock_hass, SERIAL) is right_coordinator

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
    @pytest.mark.parametrize("soc", (10, 100))
    @pytest.mark.parametrize(
        ("schema", "data_factory", "field"),
        (
            (
                init_mod.SERVICE_BATTERY_CHARGE_SCHEMA,
                _charge_service_data,
                "chargestopsoc",
            ),
            (
                init_mod.SERVICE_BATTERY_DISCHARGE_SCHEMA,
                _discharge_service_data,
                "dischargecutoffsoc",
            ),
        ),
    )
    def test_battery_soc_schema_accepts_10_to_100(
        self, schema, data_factory, field, soc
    ):
        assert schema(data_factory(soc))[field] == soc

    @pytest.mark.parametrize("soc", (9, 101))
    @pytest.mark.parametrize(
        ("schema", "data_factory"),
        (
            (init_mod.SERVICE_BATTERY_CHARGE_SCHEMA, _charge_service_data),
            (init_mod.SERVICE_BATTERY_DISCHARGE_SCHEMA, _discharge_service_data),
        ),
    )
    def test_battery_soc_schema_rejects_outside_10_to_100(
        self, schema, data_factory, soc
    ):
        with pytest.raises(vol.Invalid):
            schema(data_factory(soc))

    async def test_unknown_serial_is_rejected_before_service_write(
        self, mock_hass, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.data = {"OTHER": {}}
        entry = FakeEntry()
        entry.runtime_data = coordinator
        mock_hass.config_entries.async_entries.return_value = [entry]
        call = SimpleNamespace(hass=mock_hass, data=_charge_service_data(90))

        with pytest.raises(HomeAssistantError) as exc_info:
            await init_mod._async_service_battery_charge(call)

        assert SERIAL in str(exc_info.value)
        mock_api.setTimeChargeBySn.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_battery_charge_service(self, mock_hass, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        coordinator.set_periodic_enable_intent(SERIAL, grid_charge=1, ctr_dis=1)
        periodic = _periodic_schedule()
        mock_api.getTimeChargeBySn.return_value = periodic
        entry = FakeEntry()
        entry.runtime_data = coordinator
        mock_hass.config_entries.async_entries.return_value = [entry]

        call = SimpleNamespace(
            hass=mock_hass,
            data={
                **_charge_service_data(100),
                "cp1power": 3200,
            },
        )
        await init_mod._async_service_battery_charge(call)

        mock_api.getTimeChargeBySn.assert_awaited_once_with(SERIAL)
        periodic_write = mock_api.setTimeChargeBySn.await_args.kwargs
        assert periodic_write["chargeTimeList"] == [
            {
                "beginTime": "01:00",
                "endTime": "05:00",
                "chargeLimit": 100,
                "chargePower": 3200,
            }
        ]
        assert periodic_write["dischargeTimeList"] == periodic["dischargeTimeList"]
        # The retired legacy charge endpoints are never touched.
        mock_api.getChargeConfigInfo.assert_not_awaited()
        mock_api.updateChargeConfigInfo.assert_not_awaited()

    async def test_battery_discharge_service(self, mock_hass, make_coordinator, mock_api):
        coordinator = make_coordinator()
        coordinator.data = {SERIAL: {}}
        coordinator.set_periodic_enable_intent(SERIAL, grid_charge=1, ctr_dis=1)
        periodic = _periodic_schedule()
        mock_api.getTimeChargeBySn.return_value = periodic
        entry = FakeEntry()
        entry.runtime_data = coordinator
        mock_hass.config_entries.async_entries.return_value = [entry]

        call = SimpleNamespace(
            hass=mock_hass,
            data={
                **_discharge_service_data(10),
                "dp1power": 2600,
            },
        )
        await init_mod._async_service_battery_discharge(call)

        mock_api.getTimeChargeBySn.assert_awaited_once_with(SERIAL)
        periodic_write = mock_api.setTimeChargeBySn.await_args.kwargs
        assert periodic_write["chargeTimeList"] == periodic["chargeTimeList"]
        assert periodic_write["dischargeTimeList"] == [
            {
                "beginTime": "18:00",
                "endTime": "22:00",
                "chargeLimit": 10,
                "chargePower": 2600,
            }
        ]
        # The retired legacy discharge endpoints are never touched.
        mock_api.getDisChargeConfigInfo.assert_not_awaited()
        mock_api.updateDisChargeConfigInfo.assert_not_awaited()


class TestUnloadAndListener:
    async def test_unload_removes_services_when_last(self, mock_hass):
        entry = FakeEntry()
        mock_hass.config_entries.async_unload_platforms.return_value = True
        mock_hass.config_entries.async_entries.return_value = [entry]

        assert await async_unload_entry(mock_hass, entry) is True
        assert mock_hass.services.async_remove.call_count == 3
        removed = {call.args[1] for call in mock_hass.services.async_remove.call_args_list}
        assert removed == {"setbatterycharge", "setbatterydischarge", "export_raw_snapshot"}

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
                 fast_scan_interval=None, assume_schedule_flags_enabled=False):
        self.hass = hass
        self.assume_schedule_flags_enabled = assume_schedule_flags_enabled
        self.api = client
        self.ip_address_map = ip_address_map
        self.inverter_models = inverter_models
        self.entry = entry
        self.scan_interval = scan_interval
        self.alt_polling_mode = alt_polling_mode
        self.fast_scan_interval = fast_scan_interval
        self.data = dict(type(self).next_data)
        self.cloud_available = type(self).next_cloud_available
        self._prefetched_ess_list = None

        def set_prefetched_ess_list(units):
            self._prefetched_ess_list = units

        self.set_prefetched_ess_list = MagicMock(side_effect=set_prefetched_ess_list)
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
        discovered = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]
        mock_api.getESSList.return_value = discovered

        assert await async_setup_entry(mock_hass, entry) is True
        coordinator = FakeCoordinator.instances[0]
        assert entry.runtime_data is coordinator
        assert coordinator.ip_address_map == {SERIAL: "192.168.1.9"}
        assert coordinator._prefetched_ess_list is discovered
        coordinator.set_prefetched_ess_list.assert_called_once_with(discovered)
        coordinator.async_config_entry_first_refresh.assert_awaited_once()
        coordinator.async_probe_periodic_readable.assert_not_awaited()
        mock_api.getESSList.assert_awaited_once_with()
        init_mod.alphaess.alphaess.assert_called_once_with(
            "app-id",
            "app-secret",
            session=init_mod.async_get_clientsession.return_value,
            verify_ssl=True,
            raise_on_error=True,
        )
        mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        assert mock_hass.services.async_register.call_count == 3
        export_registration = mock_hass.services.async_register.call_args_list[-1]
        assert export_registration.args[1] == "export_raw_snapshot"
        assert export_registration.kwargs["supports_response"] is SupportsResponse.ONLY
        # Off unless the user turns it on: the default must stay fail-closed.
        assert coordinator.assume_schedule_flags_enabled is False
        # EV cleanup flag stored
        update_kwargs = mock_hass.config_entries.async_update_entry.call_args.kwargs
        assert update_kwargs["options"]["_ev_entity_cleanup_done"] is True

    async def test_the_assume_option_reaches_the_coordinator(self, setup_env):
        mock_hass, mock_api = setup_env
        sub = _inverter_subentry()
        entry = FakeEntry(
            subentries={sub.subentry_id: sub},
            options={"_ev_entity_cleanup_done": True, CONF_ASSUME_SCHEDULE_FLAGS: True},
        )
        mock_api.getESSList.return_value = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]

        await async_setup_entry(mock_hass, entry)

        assert FakeCoordinator.instances[0].assume_schedule_flags_enabled is True

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

    async def test_body_auth_failure_raises(self, setup_env):
        mock_hass, mock_api = setup_env
        mock_api.getESSList.side_effect = _api_error(6007)
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(mock_hass, FakeEntry())

    async def test_body_rejection_raises_not_ready(self, setup_env):
        mock_hass, mock_api = setup_env
        mock_api.getESSList.side_effect = _api_error(6042)
        with pytest.raises(ConfigEntryNotReady):
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

    async def test_timeout_raises_not_ready(self, setup_env):
        mock_hass, mock_api = setup_env
        mock_api.getESSList.side_effect = TimeoutError("timed out")
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
        mock_hass.config_entries.async_add_subentry.side_effect = (
            lambda config, subentry: config.subentries.update({subentry.subentry_id: subentry})
        )

        # device cleanup path
        dev_reg = MagicMock()
        device = MagicMock()
        device.id = "device1"
        device.identifiers = {(DOMAIN, SERIAL)}
        device.config_subentry_id = None
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
            "device1", new_config_subentry_id=created.subentry_id
        )
        assert any(
            "_needs_device_cleanup" not in call.kwargs.get("options", {})
            for call in mock_hass.config_entries.async_update_entry.call_args_list
        )
        # entity-id migration ran
        init_mod._migrate_entity_ids.assert_called_once()

    async def test_unresolved_devices_keep_the_migration_pending(self, setup_env, monkeypatch):
        mock_hass, mock_api = setup_env
        sub = _inverter_subentry()
        entry = FakeEntry(
            subentries={sub.subentry_id: sub},
            options={"_needs_device_cleanup": True, "_ev_entity_cleanup_done": True},
        )
        mock_api.getESSList.return_value = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]
        migration = MagicMock(return_value=False)
        monkeypatch.setattr(init_mod, "_migrate_devices_to_subentries", migration)

        assert await async_setup_entry(mock_hass, entry) is True

        migration.assert_called_once_with(mock_hass, entry)
        assert entry.options["_needs_device_cleanup"] is True
        mock_hass.config_entries.async_update_entry.assert_not_called()

    async def test_charger_migration_precedes_platforms_without_creating_ev_subentries(
        self, setup_env, monkeypatch,
    ):
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
        migrate = MagicMock(return_value={"DEFERRED-EV"})
        monkeypatch.setattr(init_mod, "async_migrate_charger_devices", migrate)

        async def forward(config, platforms):
            migrate.assert_called_once_with(mock_hass, config, config.runtime_data.data)
            assert config.runtime_data.deferred_charger_serials == {"DEFERRED-EV"}

        mock_hass.config_entries.async_forward_entry_setups.side_effect = forward

        await async_setup_entry(mock_hass, entry)
        mock_hass.config_entries.async_add_subentry.assert_not_called()

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

    async def test_fast_scan_interval_is_clamped_to_ten_seconds(self, setup_env):
        mock_hass, mock_api = setup_env
        sub = _inverter_subentry()
        entry = FakeEntry(
            subentries={sub.subentry_id: sub},
            options={
                "scan_interval_seconds": 60,
                "fast_scan_interval_seconds": 1,
                "alt_polling_mode": True,
                "_ev_entity_cleanup_done": True,
            },
        )
        mock_api.getESSList.return_value = [{"sysSn": SERIAL, "minv": "SMILE5-INV"}]

        await async_setup_entry(mock_hass, entry)

        coordinator = FakeCoordinator.instances[0]
        assert coordinator.fast_scan_interval.total_seconds() == 10


class TestMigrateRenamedUniqueIds:
    """0.9.0 renamed four entities; their registry entries migrate in place
    so entity_ids, history and automations survive."""

    def _run(self, monkeypatch, entities, existing_uid_hits=()):
        ent_reg = MagicMock()
        ent_reg.async_get_entity_id.side_effect = (
            lambda _domain, _platform, uid:
            "sensor.taken" if uid in existing_uid_hits else None
        )
        monkeypatch.setattr(init_mod.er, "async_get", MagicMock(return_value=ent_reg))
        monkeypatch.setattr(
            init_mod.er,
            "async_entries_for_config_entry",
            MagicMock(return_value=entities),
        )
        init_mod._migrate_renamed_unique_ids(MagicMock(), FakeEntry())
        return ent_reg

    def test_renamed_entities_migrate_unique_id(self, monkeypatch):
        entities = [
            FakeRegistryEntry(
                f"test_entry_{SERIAL} - Grid Charge Enabled",
                "switch.old_grid",
                domain="switch",
            ),
            FakeRegistryEntry(
                f"test_entry_{SERIAL} - batUseCap",
                "number.old_soc",
                domain="number",
            ),
        ]
        ent_reg = self._run(monkeypatch, entities)

        calls = {
            call.args[0]: call.kwargs["new_unique_id"]
            for call in ent_reg.async_update_entity.call_args_list
        }
        assert calls["switch.old_grid"] == (
            f"test_entry_{SERIAL} - Scheduled Charging"
        )
        assert calls["number.old_soc"] == (
            f"test_entry_{SERIAL} - Discharge To SOC"
        )

    def test_unrelated_and_migrated_entities_are_left_alone(self, monkeypatch):
        entities = [
            FakeRegistryEntry(
                f"test_entry_{SERIAL} - Solar Production", "sensor.solar"
            ),
            FakeRegistryEntry(
                f"test_entry_{SERIAL} - Scheduled Charging",
                "switch.new_grid",
                domain="switch",
            ),
            FakeRegistryEntry(None, "sensor.no_uid"),
        ]
        ent_reg = self._run(monkeypatch, entities)
        ent_reg.async_update_entity.assert_not_called()

    def test_skips_when_target_unique_id_exists(self, monkeypatch):
        target = f"test_entry_{SERIAL} - Scheduled Charging"
        entities = [
            FakeRegistryEntry(
                f"test_entry_{SERIAL} - Grid Charge Enabled",
                "switch.old_grid",
                domain="switch",
            ),
        ]
        ent_reg = self._run(monkeypatch, entities, existing_uid_hits=(target,))
        ent_reg.async_update_entity.assert_not_called()
