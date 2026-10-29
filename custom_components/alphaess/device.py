"""Device information and charger hierarchy migration for AlphaESS."""

import logging

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
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


def legacy_charger_serials(entry: ConfigEntry) -> set[str]:
    """Return chargers that still have a pre-hierarchy EV subentry."""
    return {
        serial.upper()
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_EV_CHARGER
        and (serial := subentry.data.get(CONF_SERIAL_NUMBER))
    }


@callback
def async_get_charger_device_info(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator,
    serial: str,
) -> ChildDeviceInfo | None:
    """Return the charger's child-device info, or None while it can't be nested.

    Shared by every platform so a deferred or unresolved charger is skipped
    consistently instead of being registered as a standalone device.
    """
    data = coordinator.data.get(serial) or {}
    ev_serial = data.get(AlphaESSNames.evchargersn)
    if not ev_serial or ev_serial.upper() in coordinator.deferred_charger_serials:
        return None
    parent_device_id = async_get_inverter_device_id(hass, entry, serial)
    if parent_device_id is None:
        return None
    return build_ev_charger_device_info(data, parent_device_id=parent_device_id)


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

    def _defer(key: str, reason: str) -> None:
        # Name the charger: one HA instance can hold several accounts. Not the
        # entry title, which is the AppID that diagnostics redacts.
        deferred.add(key)
        _LOGGER.warning(
            "Skipping EV charger %s until it can be nested beneath its inverter: %s",
            charger_serials[key], reason,
        )

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
            _defer(key, f"it is associated with systems {', '.join(sorted(parent_serials))}")
            continue
        parent_serial = next(iter(parent_serials))
        parent_subentry = get_inverter_subentry(entry, parent_serial)
        if parent_subentry is None:
            _defer(key, f"inverter {parent_serial} is not configured in this account")
            continue
        parent_id = async_get_inverter_device_id(hass, entry, parent_serial)
        parent = registry.async_get(parent_id, include_child_devices=False)
        if parent is None or parent.config_subentry_id != parent_subentry.subentry_id:
            _defer(key, f"inverter {parent_serial} is not attached to its subentry yet")
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
            _defer(key, "more than one registered device matches its serial")
            continue
        device = matches[0] if matches else None
        target_subentry = parent_subentry.subentry_id
        if device is not None and device.id == parent_id:
            _defer(key, f"its serial also identifies inverter {parent_serial}")
            continue
        if isinstance(device, dr.ChildDeviceEntry):
            if device.parent_device_id != parent_id or device.config_subentry_id != target_subentry:
                _defer(key, f"it is already nested beneath a system other than {parent_serial}")
                continue
        elif device is not None and dr.async_entries_for_parent_device(registry, device.id):
            _defer(key, "its existing device has child devices of its own")
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
        try:
            child = registry.async_get_or_create_child(
                config_entry_id=entry.entry_id,
                config_subentry_id=target_subentry,
                identifiers=identifiers,
                name=_charger_device_name(model),
                parent_device_id=parent_id,
            )
        except HomeAssistantError as err:
            # Runs on every start: a registry refusal must cost only the
            # charger's entities, not the whole account's setup.
            _defer(key, f"the device registry refused it ({err})")
            continue

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
