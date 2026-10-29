"""Charger child-device migration preserves registry identity and user settings."""

from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.alphaess import binary_sensor, button, number, sensor
from custom_components.alphaess.const import (
    CONF_EV_CHARGER_MODEL,
    CONF_PARENT_INVERTER,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from custom_components.alphaess.device import (
    async_migrate_charger_devices,
    build_inverter_device_info,
)
from custom_components.alphaess.enums import AlphaESSNames

SERIAL = "AL1000021000123"
EV_SERIAL = "EV123"
OTHER_SERIAL = "AL2000021000456"


def _inverter(serial=SERIAL, subentry_id="inverter"):
    return {
        "data": {CONF_SERIAL_NUMBER: serial},
        "subentry_id": subentry_id,
        "subentry_type": SUBENTRY_TYPE_INVERTER,
        "title": f"Inverter {serial}",
        "unique_id": f"inverter_{serial}_{subentry_id}",
    }


def _charger(serial=EV_SERIAL, parent=SERIAL, subentry_id="charger"):
    return {
        "data": {CONF_SERIAL_NUMBER: serial, CONF_PARENT_INVERTER: parent},
        "subentry_id": subentry_id,
        "subentry_type": SUBENTRY_TYPE_EV_CHARGER,
        "title": f"Charger {serial}",
        "unique_id": f"charger_{serial}_{subentry_id}",
    }


def _entry(account, subentries):
    return ConfigEntry(
        data={}, discovery_keys=MappingProxyType({}), domain=DOMAIN,
        minor_version=1, options={}, source="user", title=account,
        unique_id=account, version=2, subentries_data=subentries,
    )


def _data(ev_serial=EV_SERIAL):
    return {
        "Model": "SMILE5-INV",
        AlphaESSNames.evchargersn: ev_serial,
        AlphaESSNames.evchargermodel: "SMILE-EVCT11",
        AlphaESSNames.evchargerstatus: 3,
    }


@pytest.fixture
async def native_registry(tmp_path):
    """Use native registries, including destructive subentry-removal cleanup."""
    hass = HomeAssistant(str(tmp_path))
    entries = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_wait_initialized = AsyncMock(return_value=None)
    hass.config_entries.async_get_entry.side_effect = entries.get

    def remove_subentry(entry, subentry_id):
        dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
        # Core removes every record left on the old subentry. The integration
        # must move the device and its entities before requesting that cleanup.
        assert not any(
            device.config_entry_id == entry.entry_id and device.config_subentry_id == subentry_id
            for device in [
                *dr.async_entries_for_config_entry(dev_reg, entry.entry_id),
                *dev_reg.child_devices,
            ]
        )
        assert not any(
            entity.config_subentry_id == subentry_id
            for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        )
        subentries = dict(entry.subentries)
        subentries.pop(subentry_id)
        object.__setattr__(entry, "subentries", MappingProxyType(subentries))
        dev_reg.async_clear_config_subentry(entry.entry_id, subentry_id, entry.domain)
        ent_reg.async_clear_config_subentry(entry.entry_id, subentry_id)
        return True

    hass.config_entries.async_remove_subentry.side_effect = remove_subentry
    try:
        dr.async_setup(hass)
        await dr.async_load(hass, load_empty=True)
        await er.async_load(hass, load_empty=True)
        await ar.async_load(hass, load_empty=True)
        yield hass, entries
    finally:
        await hass.async_stop(force=True)


def _device_fields(info):
    """Serialize native dataclasses without their temporary mapping methods."""
    return info if isinstance(info, dict) else info.as_dict()


def _register(hass, entry, subentry_id, info):
    fields = _device_fields(info)
    registry = dr.async_get(hass)
    register = registry.async_get_or_create_child if "parent_device_id" in fields else registry.async_get_or_create
    return register(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry_id,
        **fields,
    )


def _parent_id(hass, entry, serial=SERIAL):
    return dr.async_get(hass).async_get_device_by_identifier((DOMAIN, serial.upper()), entry.entry_id).id


def _legacy_charger(hass, entry, *, subentry_id="charger", serial=EV_SERIAL):
    """Create the full charger device written by the previous integration."""
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, config_subentry_id=subentry_id,
        identifiers={(DOMAIN, serial)}, manufacturer="AlphaESS",
        model="SMILE-EVCT11", serial_number=serial,
        name=f"Alpha ESS Charger : {serial}",
    )


async def test_inverter_metadata_clears_legacy_service_classification(native_registry):
    hass, entries = native_registry
    entry = _entry("account", [_inverter()])
    entries[entry.entry_id] = entry
    parent = _register(hass, entry, "inverter", build_inverter_device_info(SERIAL, _data()))
    registry = dr.async_get(hass)
    area = ar.async_get(hass).async_create("Garage")
    registry.async_update_device(
        parent.id, entry_type=dr.DeviceEntryType.SERVICE,
        name_by_user="My ESS", area_id=area.id,
    )

    updated = _register(hass, entry, "inverter", build_inverter_device_info(SERIAL, _data()))

    assert updated.id == parent.id
    assert updated.entry_type is None
    assert updated.name_by_user == "My ESS"
    assert updated.area_id == area.id
    assert len(registry.devices) == 1


async def test_conversion_preserves_devices_entities_and_overrides_and_is_idempotent(native_registry):
    hass, entries = native_registry
    entry = _entry("account", [_inverter(), _charger()])
    entries[entry.entry_id] = entry
    inverter = _register(hass, entry, "inverter", build_inverter_device_info(SERIAL, _data()))
    charger = _legacy_charger(hass, entry)
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    area = ar.async_get(hass).async_create("Garage")
    charger = dev_reg.async_update_device(
        charger.id, name_by_user="My charger", area_id=area.id,
        labels={"charger-label"}, disabled_by=dr.DeviceEntryDisabler.USER,
    )
    entity = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "existing-charger-energy", config_entry=entry,
        config_subentry_id="charger", device_id=charger.id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    entity = ent_reg.async_update_entity(
        entity.entity_id, name="Car energy", area_id=area.id, labels={"energy-label"},
    )
    entity = ent_reg.async_update_entity_options(entity.entity_id, "sensor", {"display_precision": 2})
    unattached = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "charger-before-device-registration", config_entry=entry,
        config_subentry_id="charger", disabled_by=er.RegistryEntryDisabler.USER,
    )
    await hass.async_block_till_done()
    # A real restart clears live registration tracking before setup migration.
    dev_reg.async_config_entry_unloaded(entry.entry_id)

    async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    await hass.async_block_till_done()
    async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    await hass.async_block_till_done()

    child = dev_reg.async_get(charger.id)
    assert isinstance(child, dr.ChildDeviceEntry)
    assert child.id == charger.id
    assert child.parent_device_id == inverter.id
    assert child.config_subentry_id == "inverter"
    assert child.identifiers == {(DOMAIN, EV_SERIAL)}
    assert child.name == "EV Charger (SMILE-EVCT11)"
    assert child.name_by_user == "My charger"
    assert child.area_id == area.id
    assert child.labels == {"charger-label"}
    assert child.disabled_by is dr.DeviceEntryDisabler.USER
    assert not {"manufacturer", "model", "serial_number", "via_device_id"}.intersection(child.dict_repr)
    retained = ent_reg.async_get(entity.entity_id)
    assert retained is not None
    assert retained.id == entity.id
    assert retained.unique_id == entity.unique_id
    assert retained.device_id == charger.id
    assert retained.config_subentry_id == "inverter"
    assert retained.name == "Car energy"
    assert retained.area_id == area.id
    assert retained.labels == {"energy-label"}
    assert retained.options == {"sensor": {"display_precision": 2}}
    assert retained.disabled_by is er.RegistryEntryDisabler.USER
    retained_unattached = ent_reg.async_get(unattached.entity_id)
    assert retained_unattached.id == unattached.id
    assert retained_unattached.config_subentry_id == "inverter"
    assert retained_unattached.device_id is None
    assert retained_unattached.disabled_by is er.RegistryEntryDisabler.USER
    assert "charger" not in entry.subentries
    hass.config_entries.async_remove_subentry.assert_called_once_with(entry, "charger")
    assert len(dev_reg.devices) == len(dev_reg.child_devices) == 1
    assert not ent_reg.deleted_entities


async def test_entry_level_inverter_defers_charger_until_parent_subentry_is_ready(native_registry):
    """Early version-2 installs may still have a parent without a subentry."""
    hass, entries = native_registry
    entry = _entry("account", [_inverter(), _charger()])
    entries[entry.entry_id] = entry
    registry, entities = dr.async_get(hass), er.async_get(hass)
    parent = _register(hass, entry, None, build_inverter_device_info(SERIAL, _data()))
    charger = _legacy_charger(hass, entry)
    entity = entities.async_get_or_create(
        "sensor", DOMAIN, "charger-energy", config_entry=entry,
        config_subentry_id="charger", device_id=charger.id,
    )
    await hass.async_block_till_done()
    registry.async_config_entry_unloaded(entry.entry_id)

    nested = async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    await hass.async_block_till_done()

    assert nested == {}
    assert registry.async_get(parent.id) == parent
    assert registry.async_get(charger.id) == charger
    assert entities.async_get(entity.entity_id) == entity
    assert "charger" in entry.subentries
    hass.config_entries.async_remove_subentry.assert_not_called()
    assert not registry.child_devices
    assert not entities.deleted_entities

    # Inverter platform registration repairs the parent's association. The
    # next setup can then adopt the charger without replacing either identity.
    with patch("homeassistant.helpers.device_registry.report_usage"):
        repaired = _register(hass, entry, "inverter", build_inverter_device_info(SERIAL, _data()))
    assert repaired.id == parent.id
    assert repaired.config_subentry_id == "inverter"
    registry.async_config_entry_unloaded(entry.entry_id)
    assert SERIAL in async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    await hass.async_block_till_done()
    child = registry.async_get(charger.id)
    assert isinstance(child, dr.ChildDeviceEntry)
    assert child.parent_device_id == parent.id
    assert entities.async_get(entity.entity_id).id == entity.id
    assert entities.async_get(entity.entity_id).config_subentry_id == "inverter"


async def test_fresh_discovery_creates_one_parent_and_child_without_an_ev_subentry(native_registry):
    hass, entries = native_registry
    entry = _entry("account", [_inverter()])
    entries[entry.entry_id] = entry

    async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    await hass.async_block_till_done()
    dev_reg = dr.async_get(hass)
    children = list(dev_reg.child_devices)
    assert len(children) == 1
    child = children[0]
    assert child.identifiers == {(DOMAIN, EV_SERIAL)}
    assert child.name == "EV Charger (SMILE-EVCT11)"
    assert child.config_subentry_id == "inverter"
    assert child.parent_device_id == _parent_id(hass, entry)

    async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    assert [device.id for device in dev_reg.child_devices] == [child.id]
    assert len(dev_reg.devices) == 1
    assert list(entry.subentries) == ["inverter"]
    hass.config_entries.async_remove_subentry.assert_not_called()


async def test_missing_discovery_uses_saved_parent_and_later_enriches_parent_metadata(native_registry):
    hass, entries = native_registry
    entry = _entry("account", [_charger(), _inverter(serial=SERIAL.lower())])
    entries[entry.entry_id] = entry
    charger = _legacy_charger(hass, entry)
    dev_reg = dr.async_get(hass)
    dev_reg.async_config_entry_unloaded(entry.entry_id)

    async_migrate_charger_devices(hass, entry, {})
    await hass.async_block_till_done()

    child = dev_reg.async_get(charger.id)
    assert isinstance(child, dr.ChildDeviceEntry)
    assert child.name == "EV Charger (SMILE-EVCT11)"
    parent_id = child.parent_device_id
    parent = dev_reg.async_get(parent_id)
    assert parent.identifiers == {(DOMAIN, SERIAL)}
    assert parent.config_subentry_id == "inverter"
    inverter_data = {
        **_data(), AlphaESSNames.localIP: "192.168.1.5",
        AlphaESSNames.deviceStatus: 1, AlphaESSNames.softwareVersion: "1.2.3",
        AlphaESSNames.hardwareVersion: "A1",
    }
    enriched = _register(
        hass, entry, "inverter", build_inverter_device_info(SERIAL.lower(), inverter_data),
    )
    assert enriched.id == parent_id
    assert enriched.model == "SMILE5-INV"
    assert enriched.serial_number == SERIAL.lower()
    assert enriched.configuration_url == "http://192.168.1.5"
    assert enriched.sw_version == "1.2.3"
    assert enriched.hw_version == "A1"
    assert _parent_id(hass, entry) == parent_id
    assert "charger" not in entry.subentries
    assert len(dev_reg.devices) == len(dev_reg.child_devices) == 1


@pytest.mark.parametrize(
    ("fresh_model", "saved_model", "legacy_model", "discovered", "expected_model"),
    [
        ("SMILE-G3-EVCT11", "SAVED-EVCT11", "LEGACY-EVCT11", True, "SMILE-G3-EVCT11"),
        (None, "SAVED-EVCT11", "LEGACY-EVCT11", False, "SAVED-EVCT11"),
        (None, "Unknown", "LEGACY-EVCT11", False, "LEGACY-EVCT11"),
        (None, "SAVED-EVCT11", "LEGACY-EVCT11", True, "SAVED-EVCT11"),
        ("Unknown", "", None, True, None),
    ],
    ids=["fresh", "saved", "legacy-device", "incomplete-discovery", "unknown"],
)
async def test_charger_name_uses_best_available_model_and_preserves_custom_name(
    native_registry, fresh_model, saved_model, legacy_model, discovered, expected_model,
):
    hass, entries = native_registry
    legacy_subentry = _charger()
    legacy_subentry["data"][CONF_EV_CHARGER_MODEL] = saved_model
    entry = _entry("account", [_inverter(), legacy_subentry])
    entries[entry.entry_id] = entry
    registry = dr.async_get(hass)
    parent = _register(hass, entry, "inverter", build_inverter_device_info(SERIAL, _data()))
    charger = _legacy_charger(hass, entry)
    registry.async_update_device(charger.id, model=legacy_model, name_by_user="My charger")
    registry.async_config_entry_unloaded(entry.entry_id)
    data = {SERIAL: {**_data(), AlphaESSNames.evchargermodel: fresh_model}} if discovered else {}

    nested = async_migrate_charger_devices(hass, entry, data)
    assert set(nested) == ({SERIAL} if discovered else set())
    await hass.async_block_till_done()

    child = registry.async_get(charger.id)
    expected_name = f"EV Charger ({expected_model})" if expected_model else "EV Charger"
    assert isinstance(child, dr.ChildDeviceEntry)
    assert child.name == expected_name
    assert child.name_by_user == "My charger"
    assert child.parent_device_id == parent.id
    if discovered:
        registered = _register(
            hass, entry, "inverter", nested[SERIAL],
        )
        assert registered.id == child.id
        assert registered.name == expected_name
    assert set(async_migrate_charger_devices(hass, entry, data)) == set(nested)
    assert registry.async_get(child.id).name == expected_name
    assert registry.async_get(child.id).name_by_user == "My charger"


async def test_existing_child_keeps_model_name_during_missing_metadata_and_accepts_fresh_model(native_registry):
    hass, entries = native_registry
    entry = _entry("account", [_inverter()])
    entries[entry.entry_id] = entry
    registry = dr.async_get(hass)
    assert SERIAL in async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    child = next(iter(registry.child_devices))
    registry.async_update_child_device(child.id, name_by_user="Garage charger")
    missing_model = {SERIAL: {**_data(), AlphaESSNames.evchargermodel: None}}

    nested = async_migrate_charger_devices(hass, entry, missing_model)
    registered = _register(hass, entry, "inverter", nested[SERIAL])
    assert registered.id == child.id
    assert registered.name == "EV Charger (SMILE-EVCT11)"
    assert registered.name_by_user == "Garage charger"

    updated = {SERIAL: {**_data(), AlphaESSNames.evchargermodel: "SMILE-G3-EVCT11"}}
    assert SERIAL in async_migrate_charger_devices(hass, entry, updated)
    refreshed = registry.async_get(child.id)
    assert refreshed.name == "EV Charger (SMILE-G3-EVCT11)"
    assert refreshed.name_by_user == "Garage charger"
    assert refreshed.parent_device_id == child.parent_device_id


async def test_conversion_is_scoped_to_the_account_with_matching_device_identifiers(native_registry):
    hass, entries = native_registry
    foreign = _entry("other account", [_inverter(), _charger()])
    own = _entry("charger account", [_inverter(), _charger()])
    entries.update({entry.entry_id: entry for entry in (foreign, own)})
    foreign_parent = _register(hass, foreign, "inverter", build_inverter_device_info(SERIAL, _data()))
    foreign_charger = _legacy_charger(hass, foreign)
    own_charger = _legacy_charger(hass, own)
    dev_reg = dr.async_get(hass)
    dev_reg.async_config_entry_unloaded(own.entry_id)

    async_migrate_charger_devices(hass, own, {SERIAL: _data()})
    await hass.async_block_till_done()

    own_child = dev_reg.async_get(own_charger.id)
    assert isinstance(own_child, dr.ChildDeviceEntry)
    own_parent = dev_reg.async_get(own_child.parent_device_id)
    assert own_parent.id != foreign_parent.id
    assert own_parent.config_entry_id == own.entry_id
    assert own_child.config_subentry_id == "inverter"
    assert dev_reg.async_get(foreign_charger.id) == foreign_charger
    assert dev_reg.async_get(foreign_parent.id) == foreign_parent
    assert "charger" in foreign.subentries
    assert "charger" not in own.subentries
    assert len(dev_reg.devices) == 3
    assert len(dev_reg.child_devices) == 1


@pytest.mark.parametrize("problem", ["missing", "ambiguous"])
async def test_unresolved_parent_preserves_legacy_device_and_pending_subentry(native_registry, problem):
    hass, entries = native_registry
    subentries = [_charger()]
    if problem == "ambiguous":
        subentries.extend([_inverter(), _inverter(SERIAL.lower(), "duplicate-inverter")])
    entry = _entry("account", subentries)
    entries[entry.entry_id] = entry
    charger = _legacy_charger(hass, entry)
    dev_reg = dr.async_get(hass)
    dev_reg.async_config_entry_unloaded(entry.entry_id)

    nested = async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    await hass.async_block_till_done()

    assert nested == {}
    assert dev_reg.async_get(charger.id) == charger
    assert "charger" in entry.subentries
    assert len(dev_reg.devices) == 1
    assert not dev_reg.child_devices
    hass.config_entries.async_remove_subentry.assert_not_called()


async def test_ambiguous_case_variants_preserve_both_legacy_devices(native_registry):
    hass, entries = native_registry
    entry = _entry("account", [_inverter(), _charger()])
    entries[entry.entry_id] = entry
    original = _legacy_charger(hass, entry)
    case_variant = _legacy_charger(hass, entry, serial=EV_SERIAL.lower())
    dev_reg = dr.async_get(hass)
    dev_reg.async_config_entry_unloaded(entry.entry_id)

    nested = async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    await hass.async_block_till_done()

    assert nested == {}
    assert dev_reg.async_get(original.id) == original
    assert dev_reg.async_get(case_variant.id) == case_variant
    assert not dev_reg.child_devices
    assert "charger" in entry.subentries
    hass.config_entries.async_remove_subentry.assert_not_called()


async def test_inverter_with_a_charger_alias_defers_before_mutating_any_entity(native_registry):
    hass, entries = native_registry
    entry = _entry("account", [_inverter(), _charger()])
    entries[entry.entry_id] = entry
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    parent_info = _device_fields(build_inverter_device_info(SERIAL, _data()))
    parent_info["identifiers"] = {(DOMAIN, SERIAL), (DOMAIN, EV_SERIAL)}
    parent = _register(hass, entry, "inverter", parent_info)
    inverter_entity = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "inverter-output", config_entry=entry,
        config_subentry_id="inverter", device_id=parent.id,
    )
    legacy_charger_entity = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "legacy-charger-without-device", config_entry=entry,
        config_subentry_id="charger", disabled_by=er.RegistryEntryDisabler.USER,
    )
    await hass.async_block_till_done()
    dev_reg.async_config_entry_unloaded(entry.entry_id)

    nested = async_migrate_charger_devices(hass, entry, {SERIAL: _data(EV_SERIAL.lower())})
    await hass.async_block_till_done()

    assert nested == {}
    assert dev_reg.async_get(parent.id) == parent
    assert ent_reg.async_get(inverter_entity.entity_id) == inverter_entity
    assert ent_reg.async_get(legacy_charger_entity.entity_id) == legacy_charger_entity
    assert "charger" in entry.subentries
    assert len(dev_reg.devices) == 1
    assert not dev_reg.child_devices
    assert not ent_reg.deleted_entities
    hass.config_entries.async_remove_subentry.assert_not_called()


async def test_legacy_charger_with_children_is_preserved_instead_of_becoming_a_grandchild(native_registry):
    hass, entries = native_registry
    entry = _entry("account", [_inverter(), _charger()])
    entries[entry.entry_id] = entry
    charger = _legacy_charger(hass, entry)
    dev_reg = dr.async_get(hass)
    connector = dev_reg.async_get_or_create_child(
        config_entry_id=entry.entry_id, config_subentry_id="charger",
        identifiers={(DOMAIN, "EV-CONNECTOR")}, name="Connector", parent_device_id=charger.id,
    )
    dev_reg.async_config_entry_unloaded(entry.entry_id)

    nested = async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    await hass.async_block_till_done()

    assert nested == {}
    assert dev_reg.async_get(charger.id) == charger
    assert dev_reg.async_get(connector.id) == connector
    assert "charger" in entry.subentries
    hass.config_entries.async_remove_subentry.assert_not_called()


@pytest.mark.parametrize("problem", ["changed_parent", "ambiguous_discovery"])
async def test_existing_child_is_not_reparented_by_changed_or_ambiguous_discovery(native_registry, problem):
    hass, entries = native_registry
    entry = _entry("account", [_inverter(), _inverter(OTHER_SERIAL, "other-inverter")])
    entries[entry.entry_id] = entry
    parent = _register(hass, entry, "inverter", build_inverter_device_info(SERIAL, _data()))
    child = _register(
        hass, entry, "inverter", {"identifiers": {(DOMAIN, EV_SERIAL)}, "parent_device_id": parent.id},
    )
    dr.async_get(hass).async_config_entry_unloaded(entry.entry_id)
    data = {OTHER_SERIAL: _data()}
    if problem == "ambiguous_discovery":
        data[SERIAL] = _data()

    nested = async_migrate_charger_devices(hass, entry, data)
    await hass.async_block_till_done()

    assert nested == {}
    retained = dr.async_get(hass).async_get(child.id)
    assert retained.id == child.id
    assert retained.parent_device_id == parent.id
    assert retained.config_subentry_id == "inverter"
    assert len(dr.async_get(hass).child_devices) == 1
    hass.config_entries.async_remove_subentry.assert_not_called()


async def test_registry_refusal_defers_the_charger_instead_of_failing_setup(native_registry, caplog):
    hass, entries = native_registry
    entry = _entry("account", [_inverter()])
    entries[entry.entry_id] = entry
    _register(hass, entry, "inverter", build_inverter_device_info(SERIAL, _data()))
    # Still registered as a main device in this run, so Core refuses to convert it.
    charger = _legacy_charger(hass, entry, subentry_id="inverter")

    nested = async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    await hass.async_block_till_done()

    assert nested == {}
    retained = dr.async_get(hass).async_get(charger.id)
    assert isinstance(retained, dr.DeviceEntry)
    assert not dr.async_get(hass).child_devices
    assert f"Skipping EV charger {EV_SERIAL} until it can be nested" in caplog.text
    assert "the device registry refused it" in caplog.text


async def test_subentry_with_residual_records_is_not_removed(native_registry):
    hass, entries = native_registry
    entry = _entry("account", [_inverter(), _charger()])
    entries[entry.entry_id] = entry
    charger = _legacy_charger(hass, entry)
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    unresolved_device = _legacy_charger(hass, entry, serial="UNRESOLVED-DEVICE")
    residual = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "unresolved-record", config_entry=entry, config_subentry_id="charger",
        device_id=unresolved_device.id,
    )
    dev_reg.async_config_entry_unloaded(entry.entry_id)

    async_migrate_charger_devices(hass, entry, {SERIAL: _data()})
    await hass.async_block_till_done()

    assert isinstance(dev_reg.async_get(charger.id), dr.ChildDeviceEntry)
    assert dev_reg.async_get(unresolved_device.id) == unresolved_device
    assert ent_reg.async_get(residual.entity_id) == residual
    assert "charger" in entry.subentries
    hass.config_entries.async_remove_subentry.assert_not_called()
    assert not ent_reg.deleted_entities


@pytest.mark.parametrize("legacy", [True, False], ids=["legacy_ev_branches", "discovery_fallback"])
async def test_all_ev_platforms_use_children_and_parent_subentries(native_registry, make_coordinator, legacy):
    hass, entries = native_registry
    other_ev = "EV456"
    subentries = []
    if legacy:
        subentries.extend([_charger(), _charger(other_ev, OTHER_SERIAL, "other-charger")])
    subentries.extend([_inverter(), _inverter(OTHER_SERIAL, "other-inverter")])
    entry = _entry("account", subentries)
    entries[entry.entry_id] = entry
    coordinator = make_coordinator(entry=entry)
    coordinator.data = {SERIAL: _data(), OTHER_SERIAL: _data(other_ev)}
    entry.runtime_data = coordinator
    coordinator.charger_device_info = async_migrate_charger_devices(hass, entry, coordinator.data)
    expected = {EV_SERIAL: (SERIAL, "inverter"), other_ev: (OTHER_SERIAL, "other-inverter")}
    ev_entities = []

    for platform in (sensor, binary_sensor, button, number):
        platform_name = platform.__name__.rsplit(".", 1)[-1]

        def add_entities(entities, *, config_subentry_id):
            for entity in entities:
                info = entity.device_info
                device = _register(hass, entry, config_subentry_id, info)
                registered = er.async_get(hass).async_get_or_create(
                    platform_name, DOMAIN, entity.unique_id, config_entry=entry,
                    config_subentry_id=config_subentry_id, device_id=device.id,
                )
                if isinstance(device, dr.ChildDeviceEntry):
                    ev_entities.append((platform_name, device, registered, config_subentry_id, entity))

        await platform.async_setup_entry(hass, entry, add_entities)

    await hass.async_block_till_done()
    for ev_serial, (inverter_serial, inverter_subentry_id) in expected.items():
        charger_entities = [item for item in ev_entities if (DOMAIN, ev_serial) in item[1].identifiers]
        assert {item[0] for item in charger_entities} == {"sensor", "binary_sensor", "button", "number"}
        parent_id = _parent_id(hass, entry, inverter_serial)
        for _, device, registered, subentry_id, _ in charger_entities:
            assert device.parent_device_id == parent_id
            assert device.identifiers == {(DOMAIN, ev_serial)}
            assert device.config_subentry_id == inverter_subentry_id
            assert registered.config_subentry_id == subentry_id == inverter_subentry_id
            assert registered.device_id == device.id
        sensors = {item[4]._key: item[4] for item in charger_entities if item[0] == "sensor"}
        assert sensors[AlphaESSNames.evchargermodel].native_value == "SMILE-EVCT11"
        assert sensors[AlphaESSNames.evchargersn].native_value == ev_serial
        assert sensors[AlphaESSNames.evchargerstatus].native_value == "charging"
    assert len(dr.async_get(hass).devices) == len(dr.async_get(hass).child_devices) == 2
    assert not er.async_get(hass).deleted_entities
