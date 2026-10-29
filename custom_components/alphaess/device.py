"""Device information and charger hierarchy migration for AlphaESS."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo
from homeassistant.helpers.typing import UNDEFINED

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


@callback
def async_migrate_charger_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: dict[str, dict],
) -> dict[str, ChildDeviceInfo]:
    """Nest each charger beneath its inverter, keeping device and entity identities.

    Returns the charger's device info keyed by the inverter serial it is
    reported under. Platforms add charger entities only for those inverters,
    so a charger that can't be nested safely is never registered on its own.
    """
    registry = dr.async_get(hass)
    entities = er.async_get(hass)
    parents: dict[str, set[str]] = {}
    charger_serials: dict[str, str] = {}
    charger_models: dict[str, str] = {}
    data_serials: dict[str, str] = {}
    legacy_subentry_ids: dict[str, set[str]] = {}
    nested: dict[str, ChildDeviceInfo] = {}

    def _defer(key: str, reason: str) -> None:
        # Name the charger: one HA instance can hold several accounts. Not the
        # entry title, which is the AppID that diagnostics redacts.
        _LOGGER.warning(
            "Skipping EV charger %s until it can be nested beneath its inverter: %s",
            charger_serials[key], reason,
        )

    def _devices() -> list[dr.AnyDeviceEntry]:
        return [
            *dr.async_entries_for_config_entry(registry, entry.entry_id),
            *dr.async_child_entries_for_config_entry(registry, entry.entry_id),
        ]

    for subentry in entry.subentries.values():
        ev_serial = subentry.data.get(CONF_SERIAL_NUMBER)
        parent_serial = subentry.data.get(CONF_PARENT_INVERTER)
        if subentry.subentry_type == SUBENTRY_TYPE_EV_CHARGER and ev_serial and parent_serial:
            key = ev_serial.upper()
            parents.setdefault(key, set()).add(parent_serial.upper())
            charger_serials[key] = ev_serial
            legacy_subentry_ids.setdefault(key, set()).add(subentry.subentry_id)
            if model := _known_charger_model(subentry.data.get(CONF_EV_CHARGER_MODEL)):
                charger_models[key] = model
    for serial, values in data.items():
        if ev_serial := values.get(AlphaESSNames.evchargersn):
            key = ev_serial.upper()
            parents.setdefault(key, set()).add(serial.upper())
            charger_serials[key] = ev_serial
            data_serials[key] = serial
            if model := _known_charger_model(values.get(AlphaESSNames.evchargermodel)):
                charger_models[key] = model

    for key, parent_serials in parents.items():
        if len(parent_serials) != 1 or key in parent_serials:
            _defer(key, f"it is associated with systems {', '.join(sorted(parent_serials))}")
            continue
        parent_serial = next(iter(parent_serials))
        inverter_subentries = [
            subentry.subentry_id for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_INVERTER
            and (subentry.data.get(CONF_SERIAL_NUMBER) or "").upper() == parent_serial
        ]
        if len(inverter_subentries) != 1:
            _defer(key, f"inverter {parent_serial} is not configured exactly once in this account")
            continue
        target_subentry = inverter_subentries[0]
        # Register the inverter identity first: platforms set up concurrently.
        parent = registry.async_get_device_by_identifier(
            (DOMAIN, parent_serial), entry.entry_id,
        ) or registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=target_subentry,
            identifiers={(DOMAIN, parent_serial)},
        )
        if parent.config_subentry_id != target_subentry:
            _defer(key, f"inverter {parent_serial} is not attached to its subentry yet")
            continue
        matches = [
            device for device in _devices()
            if any(domain == DOMAIN and serial.upper() == key for domain, serial in device.identifiers)
        ]
        if len(matches) > 1:
            _defer(key, "more than one registered device matches its serial")
            continue
        device = matches[0] if matches else None
        is_child = isinstance(device, dr.ChildDeviceEntry)
        # Core would refuse these conversions, but only after the moves below.
        if device is not None and device.id == parent.id:
            _defer(key, f"its serial also identifies inverter {parent_serial}")
            continue
        if is_child and device.parent_device_id != parent.id:
            _defer(key, f"it is already nested beneath a system other than {parent_serial}")
            continue
        if device is not None and not is_child and dr.async_entries_for_parent_device(registry, device.id):
            _defer(key, "its existing device has child devices of its own")
            continue

        model = charger_models.get(key)
        if model is None and device is not None and not is_child:
            model = _known_charger_model(device.model)
        # An existing child keeps its name while discovery lacks the model.
        name = f"EV Charger ({model})" if model else UNDEFINED if is_child else "EV Charger"

        old_subentry_ids = legacy_subentry_ids.get(key, set())
        # A device subentry move clears entities left on the previous subentry.
        # Move their records first, including disabled and currently unloaded ones.
        for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
            if (
                (device is not None and entity.device_id == device.id)
                or (entity.config_subentry_id in old_subentry_ids and entity.device_id is None)
            ) and entity.config_subentry_id != target_subentry:
                entities.async_update_entity(entity.entity_id, config_subentry_id=target_subentry)
        if device is not None and device.config_subentry_id != target_subentry:
            registry.async_update_device(device.id, new_config_subentry_id=target_subentry)
        try:
            child = registry.async_get_or_create_child(
                config_entry_id=entry.entry_id,
                config_subentry_id=target_subentry,
                identifiers={(DOMAIN, charger_serials[key]), *(device.identifiers if device else ())},
                name=name,
                parent_device_id=parent.id,
            )
        except HomeAssistantError as err:
            # Runs on every start: a registry refusal must cost only the
            # charger's entities, not the whole account's setup.
            _defer(key, f"the device registry refused it ({err})")
            continue
        if key in data_serials:
            nested[data_serials[key]] = ChildDeviceInfo(
                identifiers={(DOMAIN, charger_serials[key])}, parent_device_id=parent.id,
            )

        # Retire the old UI group only after every owned record has moved.
        for subentry_id in old_subentry_ids:
            if not any(
                record.config_subentry_id == subentry_id
                for record in [*er.async_entries_for_config_entry(entities, entry.entry_id), *_devices()]
            ):
                hass.config_entries.async_remove_subentry(entry, subentry_id)
        _LOGGER.debug("Registered charger child device %s beneath inverter %s", child.id, parent.id)
    return nested
