"""Alpha ESS Sensor definitions."""
import logging
from typing import List

from homeassistant.components.sensor import (
    SensorEntity
)
from homeassistant.const import CURRENCY_DOLLAR

from .enums import AlphaESSNames
from .sensorlist import FULL_SENSOR_DESCRIPTIONS, LIMITED_SENSOR_DESCRIPTIONS, EV_CHARGING_DETAILS

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LIMITED_INVERTER_SENSOR_LIST, ev_charger_states
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

    _LOGGER.info(f"Initializing Inverters")
    for serial, data in coordinator.data.items():
        model = data.get("Model")
        currency = data.get("Currency")
        if currency is None:
            currency = hass.config.currency

        _LOGGER.info(f"New Inverter: Serial: {serial}, Model: {model}")

        # This is done due to the limited data that inverters like the Storion-S5 support
        if model in LIMITED_INVERTER_SENSOR_LIST:
            for description in limited_key_supported_states:
                entities.append(
                    AlphaESSSensor(
                        coordinator, entry, serial, limited_key_supported_states[description], currency
                    )
                )
        else:
            for description in full_key_supported_states:
                entities.append(
                    AlphaESSSensor(
                        coordinator, entry, serial, full_key_supported_states[description], currency
                    )
                )

        ev_charger = data.get("EV Charger S/N")

        if ev_charger:
            ev_model = data.get("EV Charger Model")
            _LOGGER.info(f"New EV Charger: Serial: {ev_charger}, Model: {ev_model}")
            for description in EV_CHARGING_DETAILS:
                entities.append(
                    AlphaESSSensor(
                        coordinator, entry, serial, ev_charging_supported_states[description.key], currency, True
                    )
                )

    async_add_entities(entities)

    return


class AlphaESSSensor(CoordinatorEntity, SensorEntity):
    """Alpha ESS Base Sensor."""

    def __init__(self, coordinator, config, serial, key_supported_states, currency, ev_charger=False):
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
    def native_value(self):
        """Return the state of the resources."""
        keys = {
            AlphaESSNames.DischargeTime1,
            AlphaESSNames.ChargeTime1,
            AlphaESSNames.DischargeTime2,
            AlphaESSNames.DischargeTime2,
            AlphaESSNames.ChargeTime2
        }

        if self._key in keys:
            time_value = str(self._name.split()[-1])
            return self.get_time(self._name, time_value)

        if self._key == AlphaESSNames.evchargerstatus:
            return ev_charger_states.get(self._coordinator.data[self._serial][self._name], "Unknown state")

        if self._key == AlphaESSNames.ChargeRange:
            return self.get_charge()

        return self._coordinator.data[self._serial][self._name]

    @property
    def native_unit_of_measurement(self):
        """Return the native unit of measurement of the sensor."""
        return self._native_unit_of_measurement

    @property
    def device_class(self):
        """Return the device_class of the sensor."""
        return self._device_class

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
