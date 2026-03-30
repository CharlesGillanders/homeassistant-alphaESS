"""Shared device info builders for AlphaESS integration."""

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .coordinator import AlphaESSDataUpdateCoordinator


def build_inverter_device_info(
    coordinator: AlphaESSDataUpdateCoordinator,
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

    if "Local IP" in data and data.get("Local IP") != "0" and data.get("Device Status") is not None:
        kwargs["serial_number"] = data.get("Device Serial Number")
        kwargs["sw_version"] = data.get("Software Version")
        kwargs["hw_version"] = data.get("Hardware Version")
        kwargs["configuration_url"] = f"http://{data['Local IP']}"

    return DeviceInfo(**kwargs)


def build_ev_charger_device_info(
    coordinator: AlphaESSDataUpdateCoordinator,
    data: dict,
) -> DeviceInfo:
    """Build DeviceInfo for an EV charger."""
    ev_sn = data.get("EV Charger S/N")

    kwargs = {
        "entry_type": DeviceEntryType.SERVICE,
        "identifiers": {(DOMAIN, ev_sn)},
        "manufacturer": "AlphaESS",
        "model": data.get("EV Charger Model"),
        "model_id": ev_sn,
        "name": f"Alpha ESS Charger : {ev_sn}",
    }

    return DeviceInfo(**kwargs)

