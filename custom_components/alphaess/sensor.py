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
    FOUR_G_STATUS_KEYS, WIFI_STATUS_KEYS
from .coordinator import AlphaESSDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Defer sensor setup to the shared sensor module."""

    coordinator: AlphaESSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: List[AlphaESSSensor] = []

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
    for serial, data in coordinator.data.items():
        model = data.get("Model")
        currency = data.get("Currency")
        if currency is None:
            currency = hass.config.currency

        _LOGGER.info(f"New Inverter: Serial: {serial}, Model: {model}")

        _LOGGER.info("DATA RECEIVED IS: %s", data)

        has_local_ip_data = 'Local IP' in data

        # This is done due to the limited data that inverters like the Storion-S5 support
        if model in LIMITED_INVERTER_SENSOR_LIST:
            for description in limited_key_supported_states:
                entities.append(
                    AlphaESSSensor(
                        coordinator, entry, serial, limited_key_supported_states[description], currency, has_local_connection=has_local_ip_data
                    )
                )
        else:
            for description in full_key_supported_states:
                entities.append(
                    AlphaESSSensor(
                        coordinator, entry, serial, full_key_supported_states[description], currency, has_local_connection=has_local_ip_data
                    )
                )

        ev_charger = data.get("EV Charger S/N")

        if ev_charger:
            ev_model = data.get("EV Charger Model")
            _LOGGER.info(f"New EV Charger: Serial: {ev_charger}, Model: {ev_model}")
            for description in EV_CHARGING_DETAILS:
                entities.append(
                    AlphaESSSensor(
                        coordinator, entry, serial, ev_charging_supported_states[description.key], currency, True, has_local_connection=has_local_ip_data
                    )
                )

        if has_local_ip_data and data.get('Local IP') != '0' and data.get('Device Status') is not None:
            _LOGGER.info(f"New local IP system sensor for {serial}")
            for description in LOCAL_IP_SYSTEM_SENSORS:
                entities.append(
                    AlphaESSSensor(
                        coordinator,
                        entry,
                        serial,
                        local_ip_supported_states[description.key],
                        currency,
                        has_local_connection=has_local_ip_data
                    )
                )

    async_add_entities(entities)

    return


class AlphaESSSensor(CoordinatorEntity, SensorEntity):
    """Alpha ESS Base Sensor."""

    def __init__(self, coordinator, config, serial, key_supported_states, currency, ev_charger=False, has_local_connection=False):
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

        for invertor in coordinator.data:
            serial = invertor.upper()
            if ev_charger:
                self._attr_device_info = DeviceInfo(
                    entry_type=DeviceEntryType.SERVICE,
                    identifiers={(DOMAIN, coordinator.data[invertor]["EV Charger S/N"])},
                    manufacturer="AlphaESS",
                    model=coordinator.data[invertor]["EV Charger Model"],
                    model_id=coordinator.data[invertor]["EV Charger S/N"],
                    name=f"Alpha ESS Charger : {coordinator.data[invertor]["EV Charger S/N"]}",
                )
            elif "Local IP" in coordinator.data[invertor]:
                self._attr_device_info = DeviceInfo(
                    entry_type=DeviceEntryType.SERVICE,
                    identifiers={(DOMAIN, serial)},
                    serial_number=coordinator.data[invertor]["Device Serial Number"],
                    sw_version=coordinator.data[invertor]["Software Version"],
                    hw_version=coordinator.data[invertor]["Hardware Version"],
                    manufacturer="AlphaESS",
                    model=coordinator.data[invertor]["Model"],
                    model_id=self._serial,
                    name=f"Alpha ESS Energy Statistics : {serial}",
                    configuration_url=f"http://{coordinator.data[invertor]["Local IP"]}"
                )
            elif self._serial == serial:
                self._attr_device_info = DeviceInfo(
                    entry_type=DeviceEntryType.SERVICE,
                    identifiers={(DOMAIN, serial)},
                    manufacturer="AlphaESS",
                    model=coordinator.data[invertor]["Model"],
                    model_id=self._serial,
                    name=f"Alpha ESS Energy Statistics : {serial}",
                )


    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}_{self._serial} - {self._name}"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._name}"

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

        # Handle TCP status for cloud connection
        if self._key == AlphaESSNames.cloudConnectionStatus:
            raw_state = self._coordinator.data.get(self._serial, {}).get(self._key)
            if raw_state is None:
                return None
            try:
                tcp_status = int(raw_state)
                return TCP_STATUS_KEYS.get(tcp_status, "connect_fail")
            except (ValueError, TypeError):
                return "connect_fail"

        # Handle Ethernet status
        if self._key == AlphaESSNames.ethernetModule:
            raw_state = self._coordinator.data.get(self._serial, {}).get(self._key)
            if raw_state is None:
                return None
            try:
                eth_status = int(raw_state)
                return ETHERNET_STATUS_KEYS.get(eth_status, "link_down")
            except (ValueError, TypeError):
                return "link_down"

        # Handle 4G status
        if self._key == AlphaESSNames.fourGModule:
            raw_state = self._coordinator.data.get(self._serial, {}).get(self._key)
            if raw_state is None:
                return None
            try:
                g4_status = int(raw_state)
                return FOUR_G_STATUS_KEYS.get(g4_status, "unknown_error")
            except (ValueError, TypeError):
                return "unknown_error"

        # Handle WiFi status
        if self._key == AlphaESSNames.wifiStatus:
            raw_state = self._coordinator.data.get(self._serial, {}).get(self._key)
            if raw_state is None:
                return None
            try:
                wifi_status = int(raw_state)
                return WIFI_STATUS_KEYS.get(wifi_status, "unknown_error")
            except (ValueError, TypeError):
                return "unknown_error"

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