"""Alpha ESS Sensor definitions."""
from typing import List

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy.KILO_WATT_HOUR, PERCENTAGE, UnitOfPower.WATT
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AlphaESSDataUpdateCoordinator
from .entity import AlphaESSSensorDescription
from .enums import AlphaESSNames

SENSOR_DESCRIPTIONS: List[AlphaESSSensorDescription] = [
    AlphaESSSensorDescription(
        key=AlphaESSNames.SolarProduction,
        name="Solar Production",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.SolarToBattery,
        name="Solar to Battery",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.SolarToGrid,
        name="Solar to Grid",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.SolarToLoad,
        name="Solar to Load",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.TotalLoad,
        name="Total Load",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.GridToLoad,
        name="Grid to Load",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.GridToBattery,
        name="Grid to Battery",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.StateOfCharge,
        name="State of Charge",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.Charge,
        name="Charge",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.Discharge,
        name="Discharge",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.EVCharger,
        name="EV Charger",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.Generation,
        name="Instantaneous Generation",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.PPV1,
        name="Instantaneous PPV1",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.PPV2,
        name="Instantaneous PPV2",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.PPV3,
        name="Instantaneous PPV3",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.PPV4,
        name="Instantaneous PPV4",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.GridIOL1,
        name="Instantaneous Grid I/O L1",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.GridIOL2,
        name="Instantaneous Grid I/O L2",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.GridIOL3,
        name="Instantaneous Grid I/O L3",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.BatterySOC,
        name="Instantaneous Battery SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.BatteryIO,
        name="Instantaneous Battery I/O",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.GridIOTotal,
        name="Instantaneous Grid I/O Total",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
        AlphaESSSensorDescription(
        key=AlphaESSNames.Load,
        name="Instantaneous Load",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
]

async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Defer sensor setup to the shared sensor module."""

    coordinator: AlphaESSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: List[AlphaESSSensor] = []

    key_supported_states = {
        description.key: description for description in SENSOR_DESCRIPTIONS
    }

    for serial in coordinator.data:
        for description in key_supported_states:
            entities.append(
                AlphaESSSensor(
                    coordinator, entry, serial, key_supported_states[description]
                )
            )
    async_add_entities(entities)

    return


class AlphaESSSensor(CoordinatorEntity, SensorEntity):
    """Alpha ESS Base Sensor."""

    def __init__(self, coordinator, config, serial, key_supported_states):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config = config
        self._name = key_supported_states.name
        self._native_unit_of_measurement = key_supported_states.native_unit_of_measurement
        self._device_class=key_supported_states.device_class
        self._state_class=key_supported_states.state_class
        self._serial = serial
        self._coordinator = coordinator

        for invertor in coordinator.data:
            serial = invertor.upper()
            if self._serial == serial:
                self._attr_device_info = DeviceInfo(
                    entry_type=DeviceEntryType.SERVICE,
                    identifiers={(DOMAIN, serial)},
                    manufacturer="AlphaESS",
                    model=coordinator.data[invertor]["Model"],
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
