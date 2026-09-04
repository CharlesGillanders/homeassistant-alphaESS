"""Redacted export of everything the integration last received from AlphaESS.

The diagnostics download answers most reports, but it carries the parsed
entity view. When the question is "what did the API actually return", the
answer has to be the response body itself. This module builds that export
from the per-endpoint responses the coordinator retains, with every
identifier that could name a person or a system replaced before it leaves
Home Assistant.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.redact import async_redact_data
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PARENT_INVERTER,
    CONF_SERIAL_NUMBER,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from .coordinator import RAW_ACCOUNT_SCOPE, AlphaESSDataUpdateCoordinator
from .diagnostics import TO_REDACT_CONFIG, TO_REDACT_DATA
from .enums import AlphaESSNames

SNAPSHOT_FORMAT = "alphaess-raw-snapshot-v1"
LOCAL_ENDPOINT = "getIPData"

# Keys whose values identify a person or an account wherever they appear.
# sysSn/evchargerSn are handled by the serial map instead, so a placeholder
# keeps the relationship between records readable.
TO_REDACT_RAW = frozenset(TO_REDACT_DATA) | frozenset(TO_REDACT_CONFIG) | frozenset({
    "appId", "appSecret", "checkCode", "verificationCode",
})

# The local dongle endpoints answer with the wifi credentials, the register
# key and the dongle serial in the clear; these are their raw field names.
TO_REDACT_LOCAL = frozenset({
    "ip", "connssid", "wifiip", "wifimask", "wifigateway",
    "sn", "key", "username", "password",
})

_SYSTEM_KEYS = ("sysSn",)
_CHARGER_KEYS = ("evchargerSn",)


def _plain_key(key: Any) -> Any:
    """Return a JSON-safe dict key: enum members become their value."""
    if isinstance(key, Enum):
        return key.value
    return key


def _collect_ids(value: Any, inverters: list[str], chargers: list[str]) -> None:
    """Add every system/charger serial named inside a response."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _SYSTEM_KEYS and isinstance(item, str) and item:
                if item not in inverters:
                    inverters.append(item)
            elif key in _CHARGER_KEYS and isinstance(item, str) and item:
                if item not in chargers:
                    chargers.append(item)
            else:
                _collect_ids(item, inverters, chargers)
    elif isinstance(value, list | tuple):
        for item in value:
            _collect_ids(item, inverters, chargers)


def _serial_map(
    entry: ConfigEntry,
    coordinator: AlphaESSDataUpdateCoordinator,
    raw: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Map every known serial to a stable placeholder.

    Inverters are numbered in the order the coordinator publishes them, which
    is the order the diagnostics download uses, so the two line up.
    """
    inverters: list[str] = [serial for serial in (coordinator.data or {}) if serial]
    chargers: list[str] = []

    def _add(target: list[str], serial: Any) -> None:
        if isinstance(serial, str) and serial and serial not in target:
            target.append(serial)

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_INVERTER:
            _add(inverters, subentry.data.get(CONF_SERIAL_NUMBER))
        elif subentry.subentry_type == SUBENTRY_TYPE_EV_CHARGER:
            _add(chargers, subentry.data.get(CONF_SERIAL_NUMBER))
            _add(inverters, subentry.data.get(CONF_PARENT_INVERTER))
    for values in (coordinator.data or {}).values():
        _add(chargers, values.get(AlphaESSNames.evchargersn))
    for scope, endpoints in raw.items():
        if scope != RAW_ACCOUNT_SCOPE:
            _add(inverters, scope)
        _collect_ids(endpoints, inverters, chargers)

    mapping = {serial: f"inverter_{idx}" for idx, serial in enumerate(inverters, start=1)}
    mapping.update({
        serial: f"ev_charger_{idx}" for idx, serial in enumerate(chargers, start=1)
    })
    return mapping


def _replace_serials(text: str, serial_map: dict[str, str]) -> str:
    """Replace every serial inside a string, longest first, any case.

    Entity IDs carry serials in lower case and the API answers them in
    upper case; both have to go.
    """
    for serial in sorted(serial_map, key=len, reverse=True):
        text = re.sub(re.escape(serial), serial_map[serial], text, flags=re.IGNORECASE)
    return text


def _walk(value: Any, serial_map: dict[str, str]) -> Any:
    """Return a JSON-safe copy with every serial replaced."""
    if isinstance(value, dict):
        return {
            _walk(_plain_key(key), serial_map): _walk(item, serial_map)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [_walk(item, serial_map) for item in value]
    if isinstance(value, Enum):
        return _walk(value.value, serial_map)
    if isinstance(value, str):
        return _replace_serials(value, serial_map)
    return value


def _system_export(
    coordinator: AlphaESSDataUpdateCoordinator,
    endpoints: dict[str, Any],
    values: dict[str, Any] | None,
    serial: str,
) -> dict[str, Any]:
    """Describe one system: its raw answers and what was made of them."""
    if LOCAL_ENDPOINT in endpoints:
        endpoints[LOCAL_ENDPOINT] = async_redact_data(
            endpoints[LOCAL_ENDPOINT], TO_REDACT_LOCAL
        )
    return {
        "model": None if values is None else values.get("Model"),
        "endpoints_captured": sorted(endpoints),
        "raw": endpoints,
        "schedule": None if values is None else coordinator.schedule_diagnostics(serial),
        "data": values,
    }


def build_raw_snapshot(
    entry: ConfigEntry,
    coordinator: AlphaESSDataUpdateCoordinator,
    *,
    version: str,
) -> dict[str, Any]:
    """Return the last response from every endpoint, with identities removed.

    Nothing here makes an API request: the export is what the integration
    already holds. Serials become inverter_N / ev_charger_N everywhere they
    appear, including inside response bodies and entity IDs; credentials,
    register keys, wifi details and the dongle serial are redacted by key.
    """
    raw = coordinator.raw_responses()
    serial_map = _serial_map(entry, coordinator, raw)
    data = coordinator.diagnostics_data_snapshot()

    inverters: dict[str, Any] = {
        serial: _system_export(coordinator, raw.get(serial, {}), values, serial)
        for serial, values in data.items()
    }
    # A system the coordinator has stopped publishing (pruned, or never
    # parsed) may still have responses worth seeing.
    for scope, endpoints in raw.items():
        if scope != RAW_ACCOUNT_SCOPE and scope not in inverters:
            inverters[scope] = _system_export(coordinator, endpoints, None, scope)

    payload: dict[str, Any] = {
        "format": SNAPSHOT_FORMAT,
        "generated_at": dt_util.utcnow().isoformat(timespec="seconds"),
        "integration_version": version,
        "redaction": {
            "inverters": sum(1 for name in serial_map.values() if name.startswith("inverter_")),
            "ev_chargers": sum(1 for name in serial_map.values() if name.startswith("ev_charger_")),
            "keys": sorted(str(_plain_key(key)) for key in TO_REDACT_RAW | TO_REDACT_LOCAL),
            "note": (
                "Serials are replaced by inverter_N / ev_charger_N wherever they "
                "appear, matching the diagnostics download. Responses are the "
                "'data' member of each OpenAPI envelope, as the client library "
                "returns it; a rejected call is recorded as its return code."
            ),
        },
        "entry": {
            "options": dict(entry.options),
            "subentries": [
                {
                    "type": subentry.subentry_type,
                    "serial": subentry.data.get(CONF_SERIAL_NUMBER),
                    "parent": subentry.data.get(CONF_PARENT_INVERTER),
                    "model": subentry.data.get("inverter_model")
                    or subentry.data.get("ev_charger_model"),
                }
                for subentry in entry.subentries.values()
            ],
        },
        "coordinator": {
            "alt_polling_mode": coordinator.alt_polling_mode,
            "cloud_available": coordinator.cloud_available,
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "inverter_count": coordinator.inverter_count,
            "model_list": list(coordinator.model_list),
            "has_throttle": coordinator.has_throttle,
            "assume_schedule_flags_enabled": coordinator.assume_schedule_flags_enabled,
            "api": coordinator.api_diagnostics(),
        },
        "account": raw.get(RAW_ACCOUNT_SCOPE, {}),
        "inverters": inverters,
    }
    return _walk(async_redact_data(payload, TO_REDACT_RAW), serial_map)
