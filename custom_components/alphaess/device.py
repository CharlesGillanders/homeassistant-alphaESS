"""Device information and charger hierarchy migration for AlphaESS."""

import logging

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo

from .const import (
    CONF_EV_CHARGER_MODEL,
    CONF_PARENT_INVERTER,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from .enums import AlphaESSNames

_LOGGER = logging.getLogger(__name__)


def _known_charger_model(model: str | None) -> str | None:
    """Return useful model metadata rather than a missing-model placeholder."""
    model = model.strip() if model else ""
    return model if model and model.casefold() != "unknown" else None


def _charger_device_name(model: str | None) -> str:
    """Include the charger's own model in its child-device name."""
    model = _known_charger_model(model)
    return f"EV Charger ({model})" if model else "EV Charger"


def build_inverter_device_info(
    serial: str,
    data: dict,
) -> DeviceInfo:
    """Build DeviceInfo for an inverter."""
    serial_upper = serial.upper()

    # Supply optional fields at construction so older TypedDict-based Home
    # Assistant versions and the DeviceInfo dataclass receive the same metadata.
    local_metadata = {}
    local_ip = data.get(AlphaESSNames.localIP)
    if local_ip and local_ip != "0" and data.get(AlphaESSNames.deviceStatus) is not None:
        local_metadata = {
            "sw_version": data.get(AlphaESSNames.softwareVersion),
            "hw_version": data.get(AlphaESSNames.hardwareVersion),
            "configuration_url": f"http://{local_ip}",
        }

    return DeviceInfo(
        identifiers={(DOMAIN, serial_upper)},
        # Clear legacy service classification on physical ESS hardware so Core
        # renders the charger's child-device tree.
        entry_type=None,
        manufacturer="AlphaESS",
        model=data.get("Model"),
        serial_number=serial,
        name=f"Alpha ESS Energy Statistics : {serial_upper}",
        **local_metadata,
    )


def build_ev_charger_device_info(
    data: dict,
    *,
    parent_device_id: str,
) -> ChildDeviceInfo:
    """Group the charger's entities beneath their ESS device."""
    ev_sn = data.get(AlphaESSNames.evchargersn)

    return ChildDeviceInfo(
        identifiers={(DOMAIN, ev_sn)},
        name=_charger_device_name(data.get(AlphaESSNames.evchargermodel)),
        parent_device_id=parent_device_id,
    )


def get_inverter_subentry(
    entry: ConfigEntry,
    serial: str,
) -> ConfigSubentry | None:
    """Return the configured inverter only when its identity is unambiguous."""
    subentries = [
        subentry for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_INVERTER
        and (subentry.data.get(CONF_SERIAL_NUMBER) or "").upper() == serial.upper()
    ]
    if len(subentries) != 1:
        return None
    return subentries[0]


@callback
def async_get_inverter_device_id(
    hass: HomeAssistant,
    entry: ConfigEntry,
    serial: str,
) -> str | None:
    """Resolve the charger's inverter within this account before registering it."""
    subentry = get_inverter_subentry(entry, serial)
    if subentry is None:
        return None

    try:
        return dr.async_get_device_id_by_identifier(
            hass, (DOMAIN, serial.upper()), config_entry_id=entry.entry_id,
        )
    except ValueError:
        # Platforms are forwarded concurrently. Register the inverter identity
        # before the charger, even when its own entities have not been added yet.
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry.subentry_id,
            identifiers={(DOMAIN, serial.upper())},
        )
        return device.id


@callback
def async_migrate_charger_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: dict[str, dict],
) -> set[str]:
    """Adopt existing chargers without replacing device or entity identities."""
    registry = dr.async_get(hass)
    entities = er.async_get(hass)
    parents: dict[str, set[str]] = {}
    charger_serials: dict[str, str] = {}
    charger_models: dict[str, str] = {}
    legacy_subentries: dict[str, list[ConfigSubentry]] = {}
    deferred: set[str] = set()
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_EV_CHARGER:
            continue
        ev_serial = subentry.data.get(CONF_SERIAL_NUMBER)
        parent_serial = subentry.data.get(CONF_PARENT_INVERTER)
        if ev_serial and parent_serial:
            key = ev_serial.upper()
            parents.setdefault(key, set()).add(parent_serial.upper())
            charger_serials[key] = ev_serial
            legacy_subentries.setdefault(key, []).append(subentry)
            if model := _known_charger_model(subentry.data.get(CONF_EV_CHARGER_MODEL)):
                charger_models[key] = model
    for serial, values in data.items():
        if ev_serial := values.get(AlphaESSNames.evchargersn):
            key = ev_serial.upper()
            parents.setdefault(key, set()).add(serial.upper())
            charger_serials[key] = ev_serial
            if model := _known_charger_model(values.get(AlphaESSNames.evchargermodel)):
                charger_models[key] = model

    for key, parent_serials in parents.items():
        if len(parent_serials) != 1 or key in parent_serials:
            deferred.add(key)
            _LOGGER.warning("Deferring charger hierarchy migration: ambiguous system association")
            continue
        parent_serial = next(iter(parent_serials))
        parent_subentry = get_inverter_subentry(entry, parent_serial)
        if parent_subentry is None:
            deferred.add(key)
            _LOGGER.warning("Deferring charger hierarchy migration: inverter is not configured")
            continue
        parent_id = async_get_inverter_device_id(hass, entry, parent_serial)
        parent = registry.async_get(parent_id, include_child_devices=False)
        if parent is None or parent.config_subentry_id != parent_subentry.subentry_id:
            deferred.add(key)
            _LOGGER.warning("Deferring charger hierarchy migration: inverter subentry is not ready")
            continue
        main_devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
        all_devices = [
            *main_devices,
            *(child for device in main_devices
              for child in dr.async_entries_for_parent_device(registry, device.id)),
        ]
        matches = [
            device for device in all_devices
            if any(domain == DOMAIN and identifier.upper() == key
                   for domain, identifier in device.identifiers)
        ]
        if len(matches) > 1:
            deferred.add(key)
            _LOGGER.warning("Deferring charger hierarchy migration: ambiguous device identity")
            continue
        device = matches[0] if matches else None
        target_subentry = parent_subentry.subentry_id
        if device is not None and device.id == parent_id:
            deferred.add(key)
            _LOGGER.warning("Deferring charger hierarchy migration: identity also identifies the inverter")
            continue
        if isinstance(device, dr.ChildDeviceEntry):
            if device.parent_device_id != parent_id or device.config_subentry_id != target_subentry:
                deferred.add(key)
                _LOGGER.warning("Deferring charger hierarchy migration: existing child belongs to another system")
                continue
        elif device is not None and dr.async_entries_for_parent_device(registry, device.id):
            deferred.add(key)
            _LOGGER.warning("Deferring charger hierarchy migration: charger already has child devices")
            continue

        model = charger_models.get(key)
        if model is None and isinstance(device, dr.DeviceEntry):
            model = _known_charger_model(device.model)
        if model is None and isinstance(device, dr.ChildDeviceEntry):
            if device.name and device.name.startswith("EV Charger (") and device.name.endswith(")"):
                model = _known_charger_model(device.name[len("EV Charger ("):-1])
        # Keep the platforms' shared builder consistent when discovery supplied
        # the charger identity but temporarily omitted its model metadata.
        if model is not None:
            for values in data.values():
                if (
                    (values.get(AlphaESSNames.evchargersn) or "").upper() == key
                    and _known_charger_model(values.get(AlphaESSNames.evchargermodel)) is None
                ):
                    values[AlphaESSNames.evchargermodel] = model

        old_subentry_ids = {
            subentry.subentry_id for subentry in legacy_subentries.get(key, ())
        }
        # A device subentry move clears entities left on the previous subentry.
        # Move their records first, including disabled and currently unloaded ones.
        for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
            if (
                (device is not None and entity.device_id == device.id)
                or (entity.config_subentry_id in old_subentry_ids and entity.device_id is None)
            ) and entity.config_subentry_id != target_subentry:
                entities.async_update_entity(
                    entity.entity_id, config_subentry_id=target_subentry,
                )
        if device is not None and not isinstance(device, dr.ChildDeviceEntry):
            if device.config_subentry_id != target_subentry:
                registry.async_update_device(
                    device.id, new_config_subentry_id=target_subentry,
                )
        identifiers = {(DOMAIN, charger_serials[key])}
        if device is not None:
            identifiers |= device.identifiers
        child = registry.async_get_or_create_child(
            config_entry_id=entry.entry_id,
            config_subentry_id=target_subentry,
            identifiers=identifiers,
            name=_charger_device_name(model),
            parent_device_id=parent_id,
        )

        # Retire the old UI group only after every owned record has moved.
        for subentry_id in old_subentry_ids:
            remaining_entities = any(
                entity.config_subentry_id == subentry_id
                for entity in er.async_entries_for_config_entry(entities, entry.entry_id)
            )
            main_devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
            remaining_devices = any(
                candidate.config_subentry_id == subentry_id
                for candidate in [
                    *main_devices,
                    *(item for parent in main_devices
                      for item in dr.async_entries_for_parent_device(registry, parent.id)),
                ]
            )
            if not remaining_entities and not remaining_devices:
                hass.config_entries.async_remove_subentry(entry, subentry_id)
        _LOGGER.debug("Registered charger child device %s beneath inverter %s", child.id, parent_id)
    return deferred
