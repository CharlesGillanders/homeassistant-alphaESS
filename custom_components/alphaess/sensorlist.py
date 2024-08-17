from typing import List

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, PERCENTAGE, UnitOfPower, CURRENCY_DOLLAR, EntityCategory

from .entity import AlphaESSSensorDescription
from .enums import AlphaESSNames

FULL_SENSOR_DESCRIPTIONS: List[AlphaESSSensorDescription] = [
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
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.Income,
        name="Total Income",
        icon="mdi:cash-multiple",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None,
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.SelfConsumption,
        name="Self Consumption",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=None,
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.SelfSufficiency,
        name="Self Sufficiency",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=None,
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.EmsStatus,
        name="EMS Status",
        device_class=SensorDeviceClass.ENUM,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.usCapacity,
        name="Maximum Battery Capacity",
        native_unit_of_measurement=PERCENTAGE,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.cobat,
        name="Installed Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=None,
        device_class=SensorDeviceClass.ENERGY,
        entity_category=EntityCategory.DIAGNOSTIC
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.surplusCobat,
        name="Current Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=None,
        device_class=SensorDeviceClass.ENERGY,
        entity_category=EntityCategory.DIAGNOSTIC
    )
]

LIMITED_SENSOR_DESCRIPTIONS: List[AlphaESSSensorDescription] = [
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
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.GridIOTotal,
        name="Instantaneous Grid I/O Total",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.Load,
        name="Instantaneous Load",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.Income,
        name="Total Income",
        icon="mdi:cash-multiple",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=None,
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.SelfConsumption,
        name="Self Consumption",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=None,
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.SelfSufficiency,
        name="Self Sufficiency",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=None,
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.EmsStatus,
        name="EMS Status",
        device_class=SensorDeviceClass.ENUM,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.usCapacity,
        name="Maximum Battery Capacity",
        native_unit_of_measurement=PERCENTAGE,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.cobat,
        name="Installed Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=None,
        device_class=SensorDeviceClass.ENERGY,
        entity_category=EntityCategory.DIAGNOSTIC
    ), AlphaESSSensorDescription(
        key=AlphaESSNames.surplusCobat,
        name="Current Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=None,
        device_class=SensorDeviceClass.ENERGY,
        entity_category=EntityCategory.DIAGNOSTIC
    )
]
