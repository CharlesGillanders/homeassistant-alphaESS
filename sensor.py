
from homeassistant.components.sensor import (
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL_INCREASING,
    SensorEntity,
)

from homeassistant.const import (
    DEVICE_CLASS_BATTERY,
    DEVICE_CLASS_ENERGY,
    ENERGY_KILO_WATT_HOUR,
    PERCENTAGE
)

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
import datetime

_LOGGER: logging.Logger = logging.getLogger(__package__)

async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Defer sensor setup to the shared sensor module."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    for invertor in coordinator.data:
        serial = invertor["sys_sn"]
        async_add_entities(
            [
                AlphaESSSensor(coordinator,entry,serial,"Solar Production"),
                AlphaESSSensor(coordinator,entry,serial,"Solar to Battery"),
                AlphaESSSensor(coordinator,entry,serial,"Solar to Grid"),
                AlphaESSSensor(coordinator,entry,serial,"Solar to Load"),
                AlphaESSSensor(coordinator,entry,serial,"Total Load"),
                AlphaESSSensor(coordinator,entry,serial,"Grid to Load"),
                AlphaESSSensor(coordinator,entry,serial,"Grid to Battery"),
                AlphaESSSensor(coordinator,entry,serial,"State of Charge"),
                AlphaESSSensor(coordinator,entry,serial,"Charge"),
                AlphaESSSensor(coordinator,entry,serial,"Discharge"),
                AlphaESSSensor(coordinator,entry,serial,"EV Charger")
            ]
        )

    return True



class AlphaESSSensor(CoordinatorEntity, SensorEntity):


    def __init__(self, coordinator, config,serial, name):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config = config
        self._name = name
        self._serial = serial
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

        if name == "State of Charge":
            self._attr_state_class = STATE_CLASS_MEASUREMENT
            self._attr_device_class = DEVICE_CLASS_BATTERY
        else:
            self._attr_state_class = STATE_CLASS_TOTAL_INCREASING
            self._attr_device_class = DEVICE_CLASS_ENERGY
    
    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config.entry_id}_{self._serial} - {self._name}"

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._serial} - {self._name}"

    @property
    def native_unit_of_measurement(self):
        """Return the unit the value is expressed in."""
        if self._name == "State of Charge":
            return PERCENTAGE
        else: 
            return ENERGY_KILO_WATT_HOUR
        

    @property
    def native_value(self):
        """Return the state of the resources."""
        for invertor in self._coordinator.data:
            serial = invertor["sys_sn"]
            if self._serial == serial:
                index = int(datetime.date.today().strftime("%d")) - 1
                if self._name == "Solar Production":
                    return invertor["statistics"]["EpvT"]
                elif self._name == "Solar to Battery":
                    return invertor["statistics"]["Epvcharge"]
                elif self._name == "Solar to Grid":
                        return invertor["statistics"]["Eout"]
                elif self._name == "Solar to Load":
                        return invertor["statistics"]["Epv2load"]
                elif self._name == "Total Load":
                        return invertor["statistics"]["EHomeLoad"]
                elif self._name == "Grid to Load":
                        return invertor["statistics"]["EGrid2Load"]
                elif self._name == "Grid to Battery":
                        return invertor["statistics"]["EGridCharge"]
                elif self._name == "State of Charge":
                        return invertor["statistics"]["Soc"]
                elif self._name == "Charge":
                        return  invertor["system_statistics"]["ECharge"][index]
                elif self._name == "Discharge":
                        return  invertor["system_statistics"]["EDischarge"][index]
                elif self._name == "EV Charger":
                        return  invertor["statistics"]["EChargingPile"][index]
