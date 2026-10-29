"""Charger connections retain separate devices, subentries and user settings."""

from types import MappingProxyType
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.alphaess import binary_sensor, button, number, sensor
from custom_components.alphaess.const import (
    CONF_PARENT_INVERTER,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from custom_components.alphaess.device import (
    async_get_inverter_device_id,
    build_ev_charger_device_info,
    build_inverter_device_info,
)
from custom_components.alphaess.enums import AlphaESSNames

SERIAL = "AL1000021000123"
EV_SERIAL = "EV123"


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
        "unique_id": f"charger_{serial}",
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
    }


@pytest.fixture
async def native_registry(tmp_path):
    """Use Core's registry validation and listeners with isolated storage."""
    hass = HomeAssistant(str(tmp_path))
    entries = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_get_entry.side_effect = entries.get
    try:
        dr.async_setup(hass)
        await dr.async_load(hass, load_empty=True)
        await er.async_load(hass, load_empty=True)
        await ar.async_load(hass, load_empty=True)
        yield hass, entries
    finally:
        await hass.async_stop(force=True)


def _device_fields(info):
    """Use Core 2026.11's serialization instead of its dict compatibility layer."""
    return info if isinstance(info, dict) else info.as_dict()


def _register(hass, entry, subentry_id, info):
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry_id,
        **_device_fields(info),
    )


async def test_connecting_existing_charger_preserves_registry_identity_and_overrides(native_registry):
    hass, entries = native_registry
    entry = _entry("account", [_inverter(), _charger()])
    entries[entry.entry_id] = entry
    inverter = _register(hass, entry, "inverter", build_inverter_device_info(SERIAL, _data()))
    charger = _register(hass, entry, "charger", build_ev_charger_device_info(_data()))
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    area = ar.async_get(hass).async_create("Garage")
    charger = dev_reg.async_update_device(
        charger.id, name_by_user="My charger", area_id=area.id,
        disabled_by=dr.DeviceEntryDisabler.USER,
    )
    entity = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "existing-charger-energy", config_entry=entry,
        config_subentry_id="charger", device_id=charger.id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    entity = ent_reg.async_update_entity(entity.entity_id, name="Car energy", area_id=area.id)
    await hass.async_block_till_done()

    linked = _register(
        hass, entry, "charger",
        build_ev_charger_device_info(
            _data(), via_device_id=async_get_inverter_device_id(hass, entry, SERIAL),
        ),
    )
    await hass.async_block_till_done()

    assert linked.id == charger.id
    assert linked.via_device_id == inverter.id
    assert linked.config_subentry_id == "charger"
    assert linked.identifiers == {(DOMAIN, EV_SERIAL)}
    assert linked.serial_number == EV_SERIAL
    assert linked.model == "SMILE-EVCT11"
    assert linked.name_by_user == "My charger"
    assert linked.area_id == area.id
    assert linked.disabled_by is dr.DeviceEntryDisabler.USER
    retained = ent_reg.async_get(entity.entity_id)
    assert retained is not None
    assert retained.id == entity.id
    assert retained.unique_id == entity.unique_id
    assert retained.device_id == charger.id
    assert retained.config_subentry_id == "charger"
    assert retained.name == "Car energy"
    assert retained.area_id == area.id
    assert retained.disabled_by is er.RegistryEntryDisabler.USER
    assert len(dev_reg.devices) == 2
    assert not ent_reg.deleted_entities


async def test_charger_first_creates_parent_identity_then_inverter_metadata_enriches_it(native_registry):
    hass, entries = native_registry
    entry = _entry("account", [_charger(), _inverter(serial=SERIAL.lower())])
    entries[entry.entry_id] = entry

    parent_id = async_get_inverter_device_id(hass, entry, SERIAL.lower())
    parent = dr.async_get(hass).async_get(parent_id)
    assert parent is not None
    assert parent.identifiers == {(DOMAIN, SERIAL)}
    assert parent.config_subentry_id == "inverter"
    assert parent.model is None
    charger = _register(
        hass, entry, "charger", build_ev_charger_device_info(_data(), via_device_id=parent_id),
    )
    inverter_data = {
        **_data(),
        AlphaESSNames.localIP: "192.168.1.5",
        AlphaESSNames.deviceStatus: 1,
        AlphaESSNames.softwareVersion: "1.2.3",
        AlphaESSNames.hardwareVersion: "A1",
    }
    enriched = _register(
        hass, entry, "inverter", build_inverter_device_info(SERIAL.lower(), inverter_data),
    )

    assert enriched.id == parent_id
    assert enriched.model == "SMILE5-INV"
    assert enriched.serial_number == SERIAL.lower()
    assert enriched.name == f"Alpha ESS Energy Statistics : {SERIAL}"
    assert enriched.configuration_url == "http://192.168.1.5"
    assert enriched.sw_version == "1.2.3"
    assert enriched.hw_version == "A1"
    assert dr.async_get(hass).async_get(charger.id).via_device_id == parent_id
    assert async_get_inverter_device_id(hass, entry, SERIAL) == parent_id
    assert len(dr.async_get(hass).devices) == 2


async def test_same_inverter_serial_in_another_account_is_not_used_as_parent(native_registry):
    hass, entries = native_registry
    foreign = _entry("other account", [_inverter()])
    own = _entry("charger account", [_inverter(), _charger()])
    entries.update({entry.entry_id: entry for entry in (foreign, own)})
    foreign_parent = _register(
        hass, foreign, "inverter", build_inverter_device_info(SERIAL, _data()),
    )

    own_parent_id = async_get_inverter_device_id(hass, own, SERIAL)
    assert own_parent_id != foreign_parent.id
    own_parent = dr.async_get(hass).async_get(own_parent_id)
    assert own_parent.config_entry_id == own.entry_id
    assert own_parent.config_subentry_id == "inverter"
    charger = _register(
        hass, own, "charger", build_ev_charger_device_info(_data(), via_device_id=own_parent_id),
    )

    assert charger.via_device_id == own_parent_id
    assert async_get_inverter_device_id(hass, foreign, SERIAL) == foreign_parent.id
    assert len(dr.async_get(hass).devices) == 3


@pytest.mark.parametrize("ambiguous", [False, True], ids=["missing", "ambiguous"])
async def test_unresolved_parent_is_omitted_without_creating_an_inverter(native_registry, ambiguous):
    hass, entries = native_registry
    subentries = [_charger()]
    if ambiguous:
        subentries.extend([_inverter(), _inverter(SERIAL.lower(), "duplicate-inverter")])
    entry = _entry("account", subentries)
    entries[entry.entry_id] = entry

    parent_id = async_get_inverter_device_id(hass, entry, SERIAL)
    assert parent_id is None
    info = build_ev_charger_device_info(_data(), via_device_id=parent_id)
    assert "via_device_id" not in _device_fields(info)
    charger = _register(hass, entry, "charger", info)

    assert charger.via_device_id is None
    assert charger.config_subentry_id == "charger"
    assert len(dr.async_get(hass).devices) == 1


@pytest.mark.parametrize("dedicated", [True, False], ids=["ev_subentries", "discovery_fallback"])
async def test_all_ev_platforms_link_each_charger_to_its_own_inverter(
    native_registry, make_coordinator, dedicated,
):
    hass, entries = native_registry
    other_serial, other_ev = "AL2000021000456", "EV456"
    # Dedicated charger subentries go first to exercise registration before the
    # inverter platforms have supplied their full device metadata.
    subentries = []
    if dedicated:
        subentries.extend([
            _charger(), _charger(other_ev, other_serial, "other-charger"),
        ])
    subentries.extend([_inverter(), _inverter(other_serial, "other-inverter")])
    entry = _entry("account", subentries)
    entries[entry.entry_id] = entry
    coordinator = make_coordinator(entry=entry)
    coordinator.data = {SERIAL: _data(), other_serial: _data(other_ev)}
    entry.runtime_data = coordinator
    expected = {
        EV_SERIAL: (SERIAL, "charger" if dedicated else "inverter"),
        other_ev: (other_serial, "other-charger" if dedicated else "other-inverter"),
    }
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
                if device.serial_number in expected:
                    ev_entities.append((platform_name, device, registered, config_subentry_id))

        await platform.async_setup_entry(hass, entry, add_entities)

    await hass.async_block_till_done()
    assert {platform for platform, _, _, _ in ev_entities} == {"sensor", "binary_sensor", "button", "number"}
    for ev_serial, (inverter_serial, ev_subentry_id) in expected.items():
        charger_entities = [item for item in ev_entities if item[1].serial_number == ev_serial]
        assert {item[0] for item in charger_entities} == {"sensor", "binary_sensor", "button", "number"}
        parent_id = async_get_inverter_device_id(hass, entry, inverter_serial)
        for _, device, entity, subentry_id in charger_entities:
            assert device.via_device_id == parent_id
            assert device.identifiers == {(DOMAIN, ev_serial)}
            assert device.model == "SMILE-EVCT11"
            assert device.config_subentry_id == ev_subentry_id
            assert entity.config_subentry_id == subentry_id == ev_subentry_id
            assert entity.device_id == device.id
    assert len(dr.async_get(hass).devices) == 4
    assert not er.async_get(hass).deleted_entities
