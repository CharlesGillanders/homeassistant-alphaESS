"""Shared device info builders for AlphaESS integration."""

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .enums import AlphaESSNames


def build_inverter_device_info(
    serial: str,
    data: dict,
) -> DeviceInfo:
    """Build DeviceInfo for an inverter."""
    serial_upper = serial.upper()

    kwargs = {
        "entry_type": DeviceEntryType.SERVICE,
        "identifiers": {(DOMAIN, serial_upper)},
        "manufacturer": "AlphaESS",
        "model": data.get("Model"),
        "model_id": serial,
        "name": f"Alpha ESS Energy Statistics : {serial_upper}",
    }

    local_ip = data.get(AlphaESSNames.localIP)
    if local_ip and local_ip != "0" and data.get(AlphaESSNames.deviceStatus) is not None:
        kwargs["serial_number"] = data.get(AlphaESSNames.deviceSerialNumber)
        kwargs["sw_version"] = data.get(AlphaESSNames.softwareVersion)
        kwargs["hw_version"] = data.get(AlphaESSNames.hardwareVersion)
        kwargs["configuration_url"] = f"http://{local_ip}"

    return DeviceInfo(**kwargs)


def build_ev_charger_device_info(
    data: dict,
) -> DeviceInfo:
    """Build DeviceInfo for an EV charger."""
    ev_sn = data.get(AlphaESSNames.evchargersn)

    kwargs = {
        "entry_type": DeviceEntryType.SERVICE,
        "identifiers": {(DOMAIN, ev_sn)},
        "manufacturer": "AlphaESS",
        "model": data.get(AlphaESSNames.evchargermodel),
        "model_id": ev_sn,
        "name": f"Alpha ESS Charger : {ev_sn}",
    }

    return DeviceInfo(**kwargs)

