from homeassistant.exceptions import InvalidStateError
from homeassistant.components.sensor import (
    STATE_CLASS_TOTAL_INCREASING,
    SensorEntity,
)

from homeassistant.const import (
    DEVICE_CLASS_ENERGY,
    ENERGY_KILO_WATT_HOUR
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ATTR_ENTRY_TYPE,
    ENTRY_TYPE_SERVICE
)

from homeassistant.const import (
    ATTR_IDENTIFIERS,
    ATTR_MANUFACTURER,
    ATTR_MODEL,
    ATTR_NAME
)

import logging

_LOGGER: logging.Logger = logging.getLogger(__package__)

async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Defer sensor setup to the shared sensor module."""
    coordinator = hass.data[DOMAIN][entry.entry_id]


    allsensors = []

    for invertor in coordinator.data:
        serial = invertor["sys_sn"]
        async_add_entities(
            [
                AlphaESSSensor(coordinator,entry,serial,"Solar Production","EpvT",ENERGY_KILO_WATT_HOUR),
                AlphaESSSensor(coordinator,entry,serial,"Solar to Battery","Echarge",ENERGY_KILO_WATT_HOUR),
                AlphaESSSensor(coordinator,entry,serial,"Solar to Grid","Eout",ENERGY_KILO_WATT_HOUR),
                AlphaESSSensor(coordinator,entry,serial,"Solar to Load","Epv2load",ENERGY_KILO_WATT_HOUR),
                AlphaESSSensor(coordinator,entry,serial,"Battery to Load","Ebat",ENERGY_KILO_WATT_HOUR),
                AlphaESSSensor(coordinator,entry,serial,"Total Load","EHomeLoad",ENERGY_KILO_WATT_HOUR),
                AlphaESSSensor(coordinator,entry,serial,"Grid to Load","EGrid2Load",ENERGY_KILO_WATT_HOUR),
                AlphaESSSensor(coordinator,entry,serial,"Grid to Battery","EGridCharge",ENERGY_KILO_WATT_HOUR)
            ]
        )

    return True



class AlphaESSSensor(CoordinatorEntity, SensorEntity):
    _attr_state_class = STATE_CLASS_TOTAL_INCREASING
    _attr_device_class = DEVICE_CLASS_ENERGY

    def __init__(self, coordinator, config,serial, name, measurement,unit):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config = config
        self._name = f"{serial} - {name}"
        self._serial = serial
        self._measurement = measurement
        self._unit = unit
        self._coordinator = coordinator

        for invertor in self._coordinator.data:
            serial = invertor["sys_sn"]
            if self._serial == serial:
                model = invertor["minv"]

        self._attr_device_info = {
            ATTR_IDENTIFIERS: {(DOMAIN,serial)},
            ATTR_NAME: f"Alpha ESS Energy Statistics : {serial}",
            ATTR_MANUFACTURER: "AlphaESS",
            ATTR_MODEL: model,
            ATTR_ENTRY_TYPE: ENTRY_TYPE_SERVICE,
        }
    
    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}_{self._name}"
        #return f"{DOMAIN}.{self._serial}.{self._name}"

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def native_unit_of_measurement(self):
        """Return the unit the value is expressed in."""
        return self._unit

    @property
    def native_value(self):
        """Return the state of the resources."""
        for invertor in self._coordinator.data:
            serial = invertor["sys_sn"]
            if self._serial == serial:
                return invertor["statistics"][self._measurement]


