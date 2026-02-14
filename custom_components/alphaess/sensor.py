"""Alpha ESS Sensor definitions."""
import logging
from typing import List

from homeassistant.components.sensor import (
    SensorEntity, SensorDeviceClass
)
from homeassistant.const import CURRENCY_DOLLAR
from homeassistant.helpers.typing import StateType

from .enums import AlphaESSNames
from .sensorlist import FULL_SENSOR_DESCRIPTIONS, LIMITED_SENSOR_DESCRIPTIONS, EV_CHARGING_DETAILS, LOCAL_IP_SYSTEM_SENSORS

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, LIMITED_INVERTER_SENSOR_LIST, EV_CHARGER_STATE_KEYS, TCP_STATUS_KEYS, ETHERNET_STATUS_KEYS, \
    FOUR_G_STATUS_KEYS, WIFI_STATUS_KEYS, CONF_SERIAL_NUMBER, SUBENTRY_TYPE_INVERTER, SUBENTRY_TYPE_EV_CHARGER, \
    CONF_PARENT_INVERTER
from .coordinator import AlphaESSDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


def _build_inverter_device_info(
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


def _build_ev_charger_device_info(
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


def _add_ev_entities(coordinator, entry, serial, data, currency, ev_charging_supported_states, subentry_id, async_add_entities):
    """Create and register EV charger sensor entities."""
    ev_charger = data.get("EV Charger S/N")
    ev_model = data.get("EV Charger Model")
    ev_device_info = _build_ev_charger_device_info(coordinator, data)
    _LOGGER.info(f"New EV Charger: Serial: {ev_charger}, Model: {ev_model}")

    ev_entities: List[AlphaESSSensor] = []
    for description in EV_CHARGING_DETAILS:
        ev_entities.append(
            AlphaESSSensor(
                coordinator, entry, serial,
                ev_charging_supported_states[description.key],
                currency, device_info=ev_device_info,
            )
        )

    async_add_entities(ev_entities, config_subentry_id=subentry_id)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up sensor entities for each subentry."""

    coordinator: AlphaESSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    full_key_supported_states = {
        description.key: description for description in FULL_SENSOR_DESCRIPTIONS
    }
    limited_key_supported_states = {
        description.key: description for description in LIMITED_SENSOR_DESCRIPTIONS
    }

    ev_charging_supported_states = {
        description.key: description for description in EV_CHARGING_DETAILS
    }

    local_ip_supported_states = {
        description.key: description for description in LOCAL_IP_SYSTEM_SENSORS
    }

    _LOGGER.info(f"Initializing Inverters")

    # Create entities per inverter subentry
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_INVERTER:
            serial = subentry.data.get(CONF_SERIAL_NUMBER)
            if not serial or serial not in coordinator.data:
                continue

            data = coordinator.data[serial]
            model = data.get("Model")
            currency = data.get("Currency")
            if currency is None:
                currency = hass.config.currency

            _LOGGER.info(f"New Inverter: Serial: {serial}, Model: {model}")

            has_local_ip_data = 'Local IP' in data
            inverter_device_info = _build_inverter_device_info(coordinator, serial, data)

            inverter_entities: List[AlphaESSSensor] = []

            if model in LIMITED_INVERTER_SENSOR_LIST:
                for description in limited_key_supported_states:
                    inverter_entities.append(
                        AlphaESSSensor(
                            coordinator, entry, serial,
                            limited_key_supported_states[description],
                            currency, device_info=inverter_device_info,
                        )
                    )
            else:
                for description in full_key_supported_states:
                    inverter_entities.append(
                        AlphaESSSensor(
                            coordinator, entry, serial,
                            full_key_supported_states[description],
                            currency, device_info=inverter_device_info,
                        )
                    )

            if has_local_ip_data and data.get('Local IP') != '0' and data.get('Device Status') is not None:
                _LOGGER.info(f"New local IP system sensor for {serial}")
                for description in LOCAL_IP_SYSTEM_SENSORS:
                    inverter_entities.append(
                        AlphaESSSensor(
                            coordinator, entry, serial,
                            local_ip_supported_states[description.key],
                            currency, device_info=inverter_device_info,
                        )
                    )

            async_add_entities(
                inverter_entities,
                config_subentry_id=subentry.subentry_id,
            )

        elif subentry.subentry_type == SUBENTRY_TYPE_EV_CHARGER:
            parent_serial = subentry.data.get(CONF_PARENT_INVERTER)
            if not parent_serial or parent_serial not in coordinator.data:
                continue

            data = coordinator.data[parent_serial]
            ev_charger = data.get("EV Charger S/N")
            if not ev_charger:
                continue

            currency = data.get("Currency")
            if currency is None:
                currency = hass.config.currency

            ev_model = data.get("EV Charger Model")
            _LOGGER.info(f"New EV Charger: Serial: {ev_charger}, Model: {ev_model}")

            _add_ev_entities(
                coordinator, entry, parent_serial, data, currency,
                ev_charging_supported_states, subentry.subentry_id, async_add_entities,
            )

    # Handle inverters with EV chargers that don't have a dedicated EV subentry
    # (auto-discovered EV chargers without explicit subentries)
    ev_subentry_serials = {
        sub.data.get(CONF_SERIAL_NUMBER)
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_EV_CHARGER
    }

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_INVERTER:
            continue
        serial = subentry.data.get(CONF_SERIAL_NUMBER)
        if not serial or serial not in coordinator.data:
            continue

        data = coordinator.data[serial]
        ev_charger = data.get("EV Charger S/N")
        if not ev_charger or ev_charger in ev_subentry_serials:
            continue

        currency = data.get("Currency")
        if currency is None:
            currency = hass.config.currency

        _add_ev_entities(
            coordinator, entry, serial, data, currency,
            ev_charging_supported_states, subentry.subentry_id, async_add_entities,
        )


class AlphaESSSensor(CoordinatorEntity, SensorEntity):
    """Alpha ESS Base Sensor."""

    def __init__(self, coordinator, config, serial, key_supported_states, currency, device_info=None):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config = config
        self._key = key_supported_states.key
        self._name = key_supported_states.name
        self._entity_category = key_supported_states.entity_category
        self._icon = key_supported_states.icon
        self._device_class = key_supported_states.device_class
        self._state_class = key_supported_states.state_class
        self._serial = serial
        self._coordinator = coordinator

        if key_supported_states.native_unit_of_measurement is CURRENCY_DOLLAR:
            self._native_unit_of_measurement = currency
        else:
            self._native_unit_of_measurement = key_supported_states.native_unit_of_measurement

        if device_info:
            self._attr_device_info = device_info

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}_{self._serial} - {self._name}"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._name}"

    @property
    def suggested_object_id(self):
        """Return suggested object id."""
        return f"{self._serial} {self._name}"

    @property
    def available(self) -> bool:
        """Return if entity is available based on whether its key exists in the data."""
        if not self.coordinator.last_update_success:
            return False
        if self._coordinator.data is None:
            return False
        serial_data = self._coordinator.data.get(self._serial)
        if serial_data is None:
            return False
        return self._key in serial_data

    @property
    def native_value(self) -> StateType:
        """Return the value of the sensor."""
        if self._coordinator.data is None:
            return None

        # Handle EV charger status enum
        if self._key == AlphaESSNames.evchargerstatus:
            raw_state = self._coordinator.data.get(self._serial, {}).get(self._key)
            if raw_state is None:
                return None
            return EV_CHARGER_STATE_KEYS.get(raw_state, "unknown")

        # Handle integer-mapped status sensors
        _STATUS_LOOKUPS = {
            AlphaESSNames.cloudConnectionStatus: (TCP_STATUS_KEYS, "connect_fail"),
            AlphaESSNames.ethernetModule: (ETHERNET_STATUS_KEYS, "link_down"),
            AlphaESSNames.fourGModule: (FOUR_G_STATUS_KEYS, "unknown_error"),
            AlphaESSNames.wifiStatus: (WIFI_STATUS_KEYS, "unknown_error"),
        }

        if self._key in _STATUS_LOOKUPS:
            raw_state = self._coordinator.data.get(self._serial, {}).get(self._key)
            if raw_state is None:
                return None
            lookup, default = _STATUS_LOOKUPS[self._key]
            try:
                return lookup.get(int(raw_state), default)
            except (ValueError, TypeError):
                return default

        if self._key in [AlphaESSNames.ChargeTime1, AlphaESSNames.ChargeTime2,
                         AlphaESSNames.DischargeTime1, AlphaESSNames.DischargeTime2]:
            return self._coordinator.data.get(self._serial, {}).get(self._key)

        # Normal sensor handling - use the key instead of name for consistency
        return self._coordinator.data.get(self._serial, {}).get(self._key)

    @property
    def native_unit_of_measurement(self):
        """Return the native unit of measurement of the sensor."""
        return self._native_unit_of_measurement

    @property
    def device_class(self):
        """Return the device_class of the sensor."""
        return self._device_class

    @property
    def options(self) -> list[str] | None:
        """Return the list of possible options for enum sensors."""
        if self._key == AlphaESSNames.evchargerstatus:
            return ["available", "preparing", "charging", "suspended_evse",
                    "suspended_ev", "finishing", "faulted", "unknown"]

        if self._key == AlphaESSNames.cloudConnectionStatus:
            return ["connected_ok", "initialization", "not_connected_router", "dns_lookup_error",
                    "connect_fail", "signal_too_weak", "failed_register_base_station",
                    "sim_card_not_inserted", "not_bound_plant", "key_error", "sn_error",
                    "communication_timeout", "communication_abort_server", "server_address_error"]

        if self._key == AlphaESSNames.ethernetModule:
            return ["link_up", "link_down"]

        if self._key == AlphaESSNames.fourGModule:
            return ["ok", "initialization", "connected_fail", "connected_lost", "unknown_error"]

        if self._key == AlphaESSNames.wifiStatus:
            return ["connection_idle", "connecting", "password_error", "ap_not_found",
                    "connect_fail", "connected_ok", "unknown_error"]

        return None

    @property
    def translation_key(self) -> str | None:
        """Return the translation key."""
        if self._key == AlphaESSNames.evchargerstatus and self._device_class == SensorDeviceClass.ENUM:
            return "ev_charger_status"
        if self._key == AlphaESSNames.cloudConnectionStatus and self._device_class == SensorDeviceClass.ENUM:
            return "tcp_status"
        if self._key == AlphaESSNames.ethernetModule and self._device_class == SensorDeviceClass.ENUM:
            return "ethernet_status"
        if self._key == AlphaESSNames.fourGModule and self._device_class == SensorDeviceClass.ENUM:
            return "four_g_status"
        if self._key == AlphaESSNames.wifiStatus and self._device_class == SensorDeviceClass.ENUM:
            return "wifi_status"
        return None

    @property
    def state_class(self):
        """Return the state_class of the sensor."""
        return self._state_class

    @property
    def entity_category(self):
        """Return the entity_category of the sensor."""
        return self._entity_category

    @property
    def icon(self):
        """Return the entity_category of the sensor."""
        return self._icon

    def get_charge(self):
        """Get battery charge range."""
        bat_high_cap = self._coordinator.data[self._serial].get("batHighCap")
        bat_use_cap = self._coordinator.data[self._serial].get("batUseCap")

        if bat_high_cap is not None and bat_use_cap is not None:
            return f"{bat_use_cap}% - {bat_high_cap}%"
        return None

    def get_time(self, name, value):
        """Get formatted time range for Discharge or Charge."""
        direction = name.split()[0]

        def get_time_range(prefix):
            """Helper to retrieve and format time ranges."""
            start_time = self._coordinator.data[self._serial].get(f"{prefix}_time{prefix[:3].capitalize()}f{value}")
            end_time = self._coordinator.data[self._serial].get(f"{prefix}_time{prefix[:3].capitalize()}e{value}")
            if start_time and end_time:
                return f"{start_time} - {end_time}"
            return None

        if direction == "Discharge":
            return get_time_range("discharge")
        elif direction == "Charge":
            return get_time_range("charge")

        return None
