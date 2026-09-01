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
    # Normal polling replaces one inverter at a time before publishing the
    # completed cycle. Use the coordinator's last complete snapshot so a
    # multi-inverter download cannot mix two poll ticks.
    data_snapshot = coordinator.diagnostics_data_snapshot()
    schedule_snapshot = coordinator.diagnostics_schedule_snapshot()

    # Anonymise inverter serial numbers in the top-level keys
    inverters = {
        f"inverter_{idx + 1}": async_redact_data(values, TO_REDACT_DATA)
        for idx, values in enumerate(data_snapshot.values())
    }

    # Keyed the same way so it lines up with the anonymised data above
    periodic_schedule_read = {
        f"inverter_{idx + 1}": (
            schedule_snapshot.get(serial, {}).get("periodic_read", "unknown")
        )
        for idx, serial in enumerate(data_snapshot)
    }

    # The state behind the schedule surface: which store governs, why the
    # controls are in the state they are, and what the stores actually hold.
    # None of it identifies anyone, and it is what a report needs to be
    # answerable without a round trip.
    schedule = {
        f"inverter_{idx + 1}": schedule_snapshot.get(serial, {})
        for idx, serial in enumerate(data_snapshot)
    }
    # Drafts and in-flight Apply state can change between polls. Keep that
    # useful transaction view separate instead of pretending it belongs to the
    # atomic completed-poll snapshot above.
    schedule_live = {
        f"inverter_{idx + 1}": coordinator.schedule_diagnostics(serial)
        for idx, serial in enumerate(data_snapshot)
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
            "api": coordinator.diagnostics_api_snapshot(),
        },
        "schedule": schedule,
        "schedule_live": schedule_live,
        "data": inverters,
    }
