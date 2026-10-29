"""Shared device info builders for AlphaESS integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_SERIAL_NUMBER, DOMAIN, SUBENTRY_TYPE_INVERTER
from .enums import AlphaESSNames


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
        manufacturer="AlphaESS",
        model=data.get("Model"),
        serial_number=serial,
        name=f"Alpha ESS Energy Statistics : {serial_upper}",
        **local_metadata,
    )


def build_ev_charger_device_info(
    data: dict,
    *,
    via_device_id: str | None = None,
) -> DeviceInfo:
    """Build charger metadata, optionally linked to its inverter."""
    ev_sn = data.get(AlphaESSNames.evchargersn)
    parent = {"via_device_id": via_device_id} if via_device_id is not None else {}

    return DeviceInfo(
        identifiers={(DOMAIN, ev_sn)},
        manufacturer="AlphaESS",
        model=data.get(AlphaESSNames.evchargermodel),
        serial_number=ev_sn,
        name=f"Alpha ESS Charger : {ev_sn}",
        **parent,
    )


@callback
def async_get_inverter_device_id(
    hass: HomeAssistant,
    entry: ConfigEntry,
    serial: str,
) -> str | None:
    """Resolve the charger's inverter within this account before linking it."""
    subentries = [
        subentry for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_INVERTER
        and (subentry.data.get(CONF_SERIAL_NUMBER) or "").upper() == serial.upper()
    ]
    if len(subentries) != 1:
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
            config_subentry_id=subentries[0].subentry_id,
            identifiers={(DOMAIN, serial.upper())},
        )
        return device.id
