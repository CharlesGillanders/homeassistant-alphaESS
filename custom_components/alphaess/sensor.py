"""Alpha ESS Sensor definitions."""
import logging
from typing import List

from homeassistant.components.sensor import (
    SensorEntity
)
from homeassistant.const import CURRENCY_DOLLAR

from .sensorlist import FULL_SENSOR_DESCRIPTIONS, LIMITED_SENSOR_DESCRIPTIONS

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AlphaESSDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Defer sensor setup to the shared sensor module."""

    currency = hass.config.currency

    coordinator: AlphaESSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: List[AlphaESSSensor] = []

    full_key_supported_states = {
        description.key: description for description in FULL_SENSOR_DESCRIPTIONS
    }
    limited_key_supported_states = {
        description.key: description for description in LIMITED_SENSOR_DESCRIPTIONS
    }

    _LOGGER.info(f"INITIALIZING DEVICES")
    for serial, data in coordinator.data.items():
        model = data.get("Model")
        _LOGGER.info(f"Serial: {serial}, Model: {model}")

        if model == "Storion-S5":
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
    async_add_entities(entities)

    return


class AlphaESSSensor(CoordinatorEntity, SensorEntity):
    """Alpha ESS Base Sensor."""

    def __init__(self, coordinator, config, serial, key_supported_states, currency):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config = config
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
            if self._serial == serial:
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
        return f"{self._serial}_{self._name}"

    @property
    def native_value(self):
        """Return the state of the resources."""
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
