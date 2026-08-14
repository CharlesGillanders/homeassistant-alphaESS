"""Diagnostics support for AlphaESS."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import AlphaESSDataUpdateCoordinator
from .enums import AlphaESSNames

TO_REDACT_CONFIG = {"AppID", "AppSecret"}

# AlphaESSNames is a str-mixin enum, so members hash/compare as their values
# and can be used directly alongside plain string keys.
TO_REDACT_DATA = {
    AlphaESSNames.deviceSerialNumber,
    AlphaESSNames.registerKey,
    AlphaESSNames.username,
    AlphaESSNames.password,
    AlphaESSNames.connectedSSID,
    AlphaESSNames.localIP,
    AlphaESSNames.wifiIP,
    AlphaESSNames.wifiGateway,
    AlphaESSNames.evchargersn,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: AlphaESSDataUpdateCoordinator = entry.runtime_data

    # Anonymise inverter serial numbers in the top-level keys
    inverters = {
        f"inverter_{idx + 1}": async_redact_data(values, TO_REDACT_DATA)
        for idx, values in enumerate((coordinator.data or {}).values())
    }

    # Keyed the same way so it lines up with the anonymised data above
    periodic_schedule_read = {
        f"inverter_{idx + 1}": coordinator.get_periodic_read_state(serial)
        for idx, serial in enumerate(coordinator.data or {})
    }

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT_CONFIG),
            "options": dict(entry.options),
            "subentry_types": [
                sub.subentry_type for sub in entry.subentries.values()
            ],
        },
        "coordinator": {
            "alt_polling_mode": coordinator.alt_polling_mode,
            "cloud_available": coordinator.cloud_available,
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "inverter_count": coordinator.inverter_count,
            "model_list": coordinator.model_list,
            "has_throttle": coordinator.has_throttle,
            "periodic_schedule_read": periodic_schedule_read,
        },
        "data": inverters,
    }
