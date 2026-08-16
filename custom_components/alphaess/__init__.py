"""The AlphaEss integration."""
from __future__ import annotations

import ipaddress
import logging
from datetime import timedelta
from typing import Any

import aiohttp
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

from alphaess import alphaess
from alphaess.alphaess import AlphaESSApiError

from .const import (
    AUTH_FAILURE_CODES,
    CONF_ALT_POLLING_MODE,
    CONF_DISABLE_NOTIFICATIONS,
    CONF_EV_CHARGER_MODEL,
    CONF_FAST_SCAN_INTERVAL_SECONDS,
    CONF_INVERTER_MODEL,
    CONF_IP_ADDRESS,
    CONF_PARENT_INVERTER,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_SERIAL_NUMBER,
    DEFAULT_FAST_SCAN_INTERVAL_SECONDS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MAX_FAST_SCAN_INTERVAL_SECONDS,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_FAST_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    PLATFORMS,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from .coordinator import AlphaESSDataUpdateCoordinator
from .enums import AlphaESSNames

_LOGGER = logging.getLogger(__name__)

type AlphaESSConfigEntry = ConfigEntry[AlphaESSDataUpdateCoordinator]

def _api_time(value: Any) -> str:
    """Normalise a service time to what the API accepts.

    It wants zero-padded HH:mm on the quarter hour: "9:00" comes back as 6001,
    and an off-grid minute is silently unusable. The time entities already round
    the same way, so this only makes the services behave consistently.
    """
    parsed = cv.time(value)
    minutes = round((parsed.hour * 60 + parsed.minute) / 15) * 15 % (24 * 60)
    normalised = f"{minutes // 60:02d}:{minutes % 60:02d}"
    if normalised != f"{parsed.hour:02d}:{parsed.minute:02d}":
        _LOGGER.debug("Rounded service time %s to %s", value, normalised)
    return normalised


SERVICE_BATTERY_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Required('serial'): cv.string,
        vol.Required('enabled'): cv.boolean,
        vol.Required('cp1start'): _api_time,
        vol.Required('cp1end'): _api_time,
        vol.Required('cp2start'): _api_time,
        vol.Required('cp2end'): _api_time,
        vol.Required('chargestopsoc'): vol.All(cv.positive_int, vol.Range(min=0, max=100)),
    }
)

SERVICE_BATTERY_DISCHARGE_SCHEMA = vol.Schema(
    {
        vol.Required('serial'): cv.string,
        vol.Required('enabled'): cv.boolean,
        vol.Required('dp1start'): _api_time,
        vol.Required('dp1end'): _api_time,
        vol.Required('dp2start'): _api_time,
        vol.Required('dp2end'): _api_time,
        vol.Required('dischargecutoffsoc'): vol.All(cv.positive_int, vol.Range(min=0, max=100)),
    }
)


def _build_ip_address_map(entry: ConfigEntry) -> dict[str, str | None]:
    """Build a mapping of serial number to IP address from subentries."""
    ip_map: dict[str, str | None] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_INVERTER:
            serial = subentry.data.get(CONF_SERIAL_NUMBER)
            ip_addr = subentry.data.get(CONF_IP_ADDRESS, "")
            if serial:
                # Validate IP address
                if ip_addr and ip_addr != "0" and ip_addr.strip():
                    try:
                        ipaddress.ip_address(ip_addr)
                        ip_map[serial] = ip_addr
                    except ValueError:
                        ip_map[serial] = None
                else:
                    ip_map[serial] = None
    return ip_map


def _build_inverter_model_list(entry: ConfigEntry) -> list[str]:
    """Build a list of inverter models from subentries."""
    models = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_INVERTER:
            model = subentry.data.get(CONF_INVERTER_MODEL, "")
            if model:
                models.append(model)
    return models


def _has_inverter_subentries(entry: ConfigEntry) -> bool:
    """Check if entry has any inverter subentries."""
    return any(
        subentry.subentry_type == SUBENTRY_TYPE_INVERTER
        for subentry in entry.subentries.values()
    )


def _migrate_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rename entity IDs to include inverter serial prefix.

    Parses unique_id format '{entry_id}_{serial} - {name}' to compute
    desired entity_id '{domain}.{serial_lower}_{slugified_name}'.
    Skips if entity already has the correct ID or if target ID is taken.
    """
    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    prefix = f"{entry.entry_id}_"

    for entity_entry in entities:
        uid = entity_entry.unique_id
        if not uid or not uid.startswith(prefix):
            continue

        remainder = uid[len(prefix):]
        # Expected format: '{serial} - {name}'
        if " - " not in remainder:
            continue

        serial, name = remainder.split(" - ", 1)
        desired_id = f"{entity_entry.domain}.{serial.lower()}_{slugify(name)}"

        if entity_entry.entity_id == desired_id:
            continue

        # Don't rename if target is already taken by a different entity
        if ent_reg.async_get(desired_id) is not None:
            _LOGGER.debug(
                "Skipping entity_id rename for %s: target %s already exists",
                entity_entry.entity_id,
                desired_id,
            )
            continue

        _LOGGER.debug(
            "Renaming entity %s -> %s",
            entity_entry.entity_id,
            desired_id,
        )
        ent_reg.async_update_entity(entity_entry.entity_id, new_entity_id=desired_id)


def _cleanup_stale_ev_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: AlphaESSDataUpdateCoordinator,
) -> None:
    """Remove stale EV entities that are no longer supported for each inverter.

    This is a one-time migration helper to remove old EV entities from the
    registry when the latest coordinator data indicates they should not exist.
    """
    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    prefix = f"{entry.entry_id}_"

    connector_name_to_key = {
        AlphaESSNames.ElectricVehiclePowerOne.value: AlphaESSNames.ElectricVehiclePowerOne,
        AlphaESSNames.ElectricVehiclePowerTwo.value: AlphaESSNames.ElectricVehiclePowerTwo,
        AlphaESSNames.ElectricVehiclePowerThree.value: AlphaESSNames.ElectricVehiclePowerThree,
        AlphaESSNames.ElectricVehiclePowerFour.value: AlphaESSNames.ElectricVehiclePowerFour,
    }

    for entity_entry in entities:
        uid = entity_entry.unique_id
        if not uid or not uid.startswith(prefix) or " - " not in uid:
            continue

        remainder = uid[len(prefix):]
        serial, entity_name = remainder.split(" - ", 1)
        serial_data = coordinator.data.get(serial)
        if not serial_data:
            continue

        ev_present = serial_data.get(AlphaESSNames.evchargersn) is not None
        connector_one_present = serial_data.get(AlphaESSNames.ElectricVehiclePowerOne) is not None

        should_remove = False
        if entity_name == AlphaESSNames.pev.value:
            should_remove = (not ev_present) or (not connector_one_present)
        elif entity_name in connector_name_to_key:
            connector_key = connector_name_to_key[entity_name]
            should_remove = (not ev_present) or (serial_data.get(connector_key) is None)

        if should_remove:
            _LOGGER.info("Removing stale EV entity %s", entity_entry.entity_id)
            ent_reg.async_remove(entity_entry.entity_id)


def _resolve_coordinator_for_serial(
    hass: HomeAssistant, serial: str
) -> AlphaESSDataUpdateCoordinator:
    """Find the coordinator managing the given inverter serial.

    Resolved at call time so services keep working across entry reloads.
    """
    fallback = None
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if not isinstance(coordinator, AlphaESSDataUpdateCoordinator):
            continue
        if serial in (coordinator.data or {}):
            return coordinator
        if fallback is None:
            fallback = coordinator
    if fallback is not None:
        return fallback
    raise HomeAssistantError(
        f"No loaded AlphaESS config entry found for serial {serial}"
    )


async def _async_service_battery_charge(call: ServiceCall) -> None:
    """Handle the setbatterycharge service."""
    serial = call.data["serial"]
    coordinator = _resolve_coordinator_for_serial(call.hass, serial)
    try:
        await coordinator.async_write_charge_config(
            serial,
            bat_high_cap=call.data["chargestopsoc"],
            grid_charge=int(call.data["enabled"] is True),
            times={
                "timeChaf1": call.data["cp1start"], "timeChae1": call.data["cp1end"],
                "timeChaf2": call.data["cp2start"], "timeChae2": call.data["cp2end"],
            },
        )
    except AlphaESSApiError as err:
        raise HomeAssistantError(
            f"AlphaESS rejected the charge settings for {serial}: {err}") from err


async def _async_service_battery_discharge(call: ServiceCall) -> None:
    """Handle the setbatterydischarge service."""
    serial = call.data["serial"]
    coordinator = _resolve_coordinator_for_serial(call.hass, serial)
    try:
        await coordinator.async_write_discharge_config(
            serial,
            bat_use_cap=call.data["dischargecutoffsoc"],
            ctr_dis=int(call.data["enabled"] is True),
            times={
                "timeDisf1": call.data["dp1start"], "timeDise1": call.data["dp1end"],
                "timeDisf2": call.data["dp2start"], "timeDise2": call.data["dp2end"],
            },
        )
    except AlphaESSApiError as err:
        raise HomeAssistantError(
            f"AlphaESS rejected the discharge settings for {serial}: {err}") from err


async def async_setup_entry(hass: HomeAssistant, entry: AlphaESSConfigEntry) -> bool:
    """Set up Alpha ESS from a config entry."""

    verify_ssl = entry.options.get(
        "Verify SSL Certificate",
        entry.data.get("Verify SSL Certificate", True)
    )

    # Build per-inverter IP address mapping from subentries
    ip_address_map = _build_ip_address_map(entry)

    # Don't set a single IP on the client - the coordinator handles per-inverter IPs.
    # Use HA's shared aiohttp session; the library applies verify_ssl per request.
    # raise_on_error surfaces the API's own return code instead of collapsing
    # every failure to None. That is the only way to tell an accepted schedule
    # write from a rejected one, since those endpoints answer with data: null
    # either way. Reads stay tolerant of it — see AlphaESSDataUpdateCoordinator._read.
    client = alphaess.alphaess(
        entry.data["AppID"],
        entry.data["AppSecret"],
        session=async_get_clientsession(hass),
        verify_ssl=verify_ssl,
        raise_on_error=True,
    )

    # Call getESSList to initialise the API client and discover systems
    # This is required before getdata() will work
    try:
        ess_list = await client.getESSList()
    except AlphaESSApiError as err:
        # The API answered and refused. Bad credentials show up here as a
        # return code rather than an HTTP 401.
        if err.code in AUTH_FAILURE_CODES:
            raise ConfigEntryAuthFailed(
                f"AlphaESS credentials rejected: {err}") from err
        raise ConfigEntryNotReady(f"AlphaESS cloud API refused the request: {err}") from err
    except aiohttp.ClientResponseError as err:
        if err.status == 401:
            raise ConfigEntryAuthFailed("AlphaESS credentials rejected") from err
        raise ConfigEntryNotReady(f"AlphaESS cloud API not reachable: {err}") from err
    except aiohttp.ClientError as err:
        raise ConfigEntryNotReady(f"AlphaESS cloud API not reachable: {err}") from err

    # If no subentries exist (e.g. after migration from v1), auto-create them
    if not _has_inverter_subentries(entry) and ess_list:
        migrated_ip = entry.options.get("_migrated_ip", "")
        # Migrate entry-level notification setting to each subentry
        migrated_disable_notif = entry.options.get(
            "Disable Notifications On Charge/Discharge Confirmation",
            entry.data.get("Disable Notifications On Charge/Discharge Confirmation", True),
        )

        for idx, unit in enumerate(ess_list):
            serial = unit.get("sysSn")
            if not serial:
                continue

            model = unit.get("minv", "Unknown")
            # Assign migrated IP to the first inverter only
            ip_for_inverter = migrated_ip if idx == 0 and migrated_ip else ""

            subentry = ConfigSubentry(
                data={
                    CONF_SERIAL_NUMBER: serial,
                    CONF_INVERTER_MODEL: model,
                    CONF_IP_ADDRESS: ip_for_inverter,
                    CONF_DISABLE_NOTIFICATIONS: migrated_disable_notif,
                },
                subentry_type=SUBENTRY_TYPE_INVERTER,
                title=f"{model} ({serial})",
                unique_id=f"{SUBENTRY_TYPE_INVERTER}_{serial}",
            )
            hass.config_entries.async_add_subentry(entry, subentry)

        # Clear the temporary migrated IP from options (keep cleanup flag)
        if migrated_ip:
            remaining_options = {
                k: v for k, v in entry.options.items()
                if k != "_migrated_ip"
            }
            hass.config_entries.async_update_entry(entry, options=remaining_options)

        # Rebuild IP map now that subentries exist
        ip_address_map = _build_ip_address_map(entry)

    inverter_models = _build_inverter_model_list(entry)

    scan_interval_seconds = entry.options.get(
        CONF_SCAN_INTERVAL_SECONDS,
        DEFAULT_SCAN_INTERVAL_SECONDS,
    )
    try:
        scan_interval_seconds = int(scan_interval_seconds)
    except (TypeError, ValueError):
        scan_interval_seconds = DEFAULT_SCAN_INTERVAL_SECONDS

    scan_interval_seconds = max(
        MIN_SCAN_INTERVAL_SECONDS,
        min(MAX_SCAN_INTERVAL_SECONDS, scan_interval_seconds),
    )

    # Alt polling mode settings
    alt_polling_mode = entry.options.get(CONF_ALT_POLLING_MODE, False)
    fast_scan_interval_seconds = entry.options.get(
        CONF_FAST_SCAN_INTERVAL_SECONDS,
        DEFAULT_FAST_SCAN_INTERVAL_SECONDS,
    )
    try:
        fast_scan_interval_seconds = int(fast_scan_interval_seconds)
    except (TypeError, ValueError):
        fast_scan_interval_seconds = DEFAULT_FAST_SCAN_INTERVAL_SECONDS

    fast_scan_interval_seconds = max(
        MIN_FAST_SCAN_INTERVAL_SECONDS,
        min(MAX_FAST_SCAN_INTERVAL_SECONDS, fast_scan_interval_seconds),
    )

    _coordinator = AlphaESSDataUpdateCoordinator(
        hass,
        client=client,
        ip_address_map=ip_address_map,
        inverter_models=inverter_models,
        entry=entry,
        scan_interval=timedelta(seconds=scan_interval_seconds),
        alt_polling_mode=alt_polling_mode,
        fast_scan_interval=timedelta(seconds=fast_scan_interval_seconds),
    )
    await _coordinator.async_config_entry_first_refresh()

    entry.runtime_data = _coordinator

    # Determine once per inverter whether the periodic schedule API is usable.
    # Systems that aren't entitled return 6017 and keep using only the legacy
    # endpoints. Never fatal — a failure here just leaves it to be retried on
    # the first write.
    for serial in list(_coordinator.data):
        await _coordinator.async_probe_periodic_readable(serial)

    # Auto-create EV charger subentries for any discovered chargers
    existing_ev_serials = {
        sub.data.get(CONF_SERIAL_NUMBER)
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_EV_CHARGER
    }
    for serial, data in _coordinator.data.items():
        ev_sn = data.get(AlphaESSNames.evchargersn)
        if ev_sn and ev_sn not in existing_ev_serials:
            ev_model = data.get(AlphaESSNames.evchargermodel, "Unknown")
            ev_subentry = ConfigSubentry(
                data={
                    CONF_SERIAL_NUMBER: ev_sn,
                    CONF_EV_CHARGER_MODEL: ev_model,
                    CONF_PARENT_INVERTER: serial,
                },
                subentry_type=SUBENTRY_TYPE_EV_CHARGER,
                title=f"{ev_model} ({ev_sn})",
                unique_id=f"{SUBENTRY_TYPE_EV_CHARGER}_{ev_sn}",
            )
            hass.config_entries.async_add_subentry(entry, ev_subentry)
            existing_ev_serials.add(ev_sn)

    # One-time cleanup: remove stale EV entities no longer supported by data.
    # Only run when cloud data is available; in local-fallback mode EV keys are
    # intentionally absent and we must not remove valid entities.
    cloud_available = getattr(_coordinator, "cloud_available", True)
    if cloud_available and not entry.options.get("_ev_entity_cleanup_done", False):
        _cleanup_stale_ev_entities(hass, entry, _coordinator)
        new_options = {**entry.options, "_ev_entity_cleanup_done": True}
        hass.config_entries.async_update_entry(entry, options=new_options)

    # One-time cleanup: remove old device associations from pre-subentry era.
    # Old devices were registered with (config_entry_id, None) - no subentry.
    # Remove them so platforms recreate devices with proper subentry associations.
    if entry.options.get("_needs_device_cleanup"):
        dev_reg = dr.async_get(hass)
        devices = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
        for device_entry in devices:
            dev_reg.async_update_device(
                device_entry.id, remove_config_entry_id=entry.entry_id
            )
        # Clear the flag so this only runs once
        new_options = {
            k: v for k, v in entry.options.items()
            if k != "_needs_device_cleanup"
        }
        hass.config_entries.async_update_entry(entry, options=new_options)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # One-time migration: rename entity IDs to serial-prefixed format.
    # Only runs once (when flag is set during config entry migration).
    if entry.options.get("_needs_entity_id_migration"):
        _migrate_entity_ids(hass, entry)
        new_options = {
            k: v for k, v in entry.options.items()
            if k != "_needs_entity_id_migration"
        }
        hass.config_entries.async_update_entry(entry, options=new_options)

    # No update listener: the options flow inherits OptionsFlowWithReload,
    # so HA reloads the entry automatically when options change.

    # Register services (only once per domain); handlers resolve the
    # owning client at call time so reloads don't leave stale references.
    if not hass.services.has_service(DOMAIN, 'setbatterycharge'):
        hass.services.async_register(
            DOMAIN, 'setbatterycharge', _async_service_battery_charge,
            SERVICE_BATTERY_CHARGE_SCHEMA)

        hass.services.async_register(
            DOMAIN, 'setbatterydischarge', _async_service_battery_discharge,
            SERVICE_BATTERY_DISCHARGE_SCHEMA)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: AlphaESSConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Remove domain services when the last loaded entry is unloaded
        remaining = [
            other
            for other in hass.config_entries.async_entries(DOMAIN)
            if other.entry_id != entry.entry_id
            and other.state is ConfigEntryState.LOADED
        ]
        if not remaining:
            hass.services.async_remove(DOMAIN, 'setbatterycharge')
            hass.services.async_remove(DOMAIN, 'setbatterydischarge')

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry from version 1 to version 2."""
    _LOGGER.debug(
        "Migrating configuration from version %s",
        config_entry.version,
    )

    if config_entry.version > 2:
        return False

    if config_entry.version == 1:
        # Get old IP address from data or options
        old_ip = config_entry.options.get(
            "IPAddress",
            config_entry.data.get("IPAddress", "")
        )

        # Clean up entry data - remove IPAddress, keep credentials
        new_data = {
            "AppID": config_entry.data["AppID"],
            "AppSecret": config_entry.data["AppSecret"],
            "Verify SSL Certificate": config_entry.options.get(
                "Verify SSL Certificate",
                config_entry.data.get("Verify SSL Certificate", True)
            ),
        }

        # Store the old IP temporarily so async_setup_entry can assign it
        # to the first inverter when it auto-creates subentries.
        # Also flag that old devices need cleanup (pre-subentry associations).
        new_options = {
            "_needs_device_cleanup": True,
            "_needs_entity_id_migration": True,
        }
        if old_ip and old_ip != "0":
            new_options["_migrated_ip"] = old_ip

        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            options=new_options,
            version=2,
        )

        _LOGGER.info("Migration to version 2 successful")

    return True
