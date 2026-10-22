"""Shared device info builders for AlphaESS integration."""

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
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
) -> DeviceInfo:
    """Build DeviceInfo for an EV charger."""
    ev_sn = data.get(AlphaESSNames.evchargersn)

    return DeviceInfo(
        identifiers={(DOMAIN, ev_sn)},
        manufacturer="AlphaESS",
        model=data.get(AlphaESSNames.evchargermodel),
        serial_number=ev_sn,
        name=f"Alpha ESS Charger : {ev_sn}",
    )
