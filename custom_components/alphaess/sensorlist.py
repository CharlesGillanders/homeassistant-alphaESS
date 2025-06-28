"""
AlphaESS Home Assistant Integration - Sensor Definitions

This module defines all sensor, button, and number entity descriptions for the AlphaESS integration.
Sensors are organized by category and access level (full vs limited API access).
"""

from typing import List

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfEnergy,
    PERCENTAGE,
    UnitOfPower,
    CURRENCY_DOLLAR,
    EntityCategory,
    UnitOfMass
)

from .entity import (
    AlphaESSSensorDescription,
    AlphaESSButtonDescription,
    AlphaESSNumberDescription
)
from .enums import AlphaESSNames


# ============================================================================
# SENSOR CATEGORIES
# ============================================================================

def _create_energy_sensor(key: AlphaESSNames, name: str,
                          increasing: bool = True) -> AlphaESSSensorDescription:
    """Helper to create energy sensors (kWh, total increasing)."""
    return AlphaESSSensorDescription(
        key=key,
        name=name,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING if increasing else SensorStateClass.TOTAL,
    )


def _create_power_sensor(key: AlphaESSNames, name: str,
                         icon: str = "mdi:flash") -> AlphaESSSensorDescription:
    """Helper to create instantaneous power sensors (W)."""
    return AlphaESSSensorDescription(
        key=key,
        name=name,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon=icon,
    )


def _create_diagnostic_sensor(key: AlphaESSNames, name: str, icon: str,
                              unit: str = None,
                              device_class: str = None,
                              state_class: SensorStateClass = None) -> AlphaESSSensorDescription:
    """Helper to create diagnostic sensors."""
    return AlphaESSSensorDescription(
        key=key,
        name=name,
        icon=icon,
        native_unit_of_measurement=unit,
        device_class=device_class,
        state_class=state_class,
        entity_category=EntityCategory.DIAGNOSTIC,
    )


# ============================================================================
# ENERGY FLOW SENSORS - Track energy movement between components
# ============================================================================

ENERGY_FLOW_SENSORS = [
    # Solar energy distribution
    _create_energy_sensor(AlphaESSNames.SolarProduction, "Solar Production"),
    _create_energy_sensor(AlphaESSNames.SolarToBattery, "Solar to Battery"),
    _create_energy_sensor(AlphaESSNames.SolarToGrid, "Solar to Grid"),
    _create_energy_sensor(AlphaESSNames.SolarToLoad, "Solar to Load"),

    # Grid interactions
    _create_energy_sensor(AlphaESSNames.GridToLoad, "Grid to Load"),
    _create_energy_sensor(AlphaESSNames.GridToBattery, "Grid to Battery"),

    # Battery operations
    _create_energy_sensor(AlphaESSNames.Charge, "Charge"),
    _create_energy_sensor(AlphaESSNames.Discharge, "Discharge"),

    # Consumption
    _create_energy_sensor(AlphaESSNames.TotalLoad, "Total Load"),
    _create_energy_sensor(AlphaESSNames.EVCharger, "EV Charger"),

    # Totals
    _create_energy_sensor(AlphaESSNames.Total_Generation, "Total Generation"),
]

# ============================================================================
# INSTANTANEOUS POWER SENSORS - Real-time power measurements
# ============================================================================

INSTANTANEOUS_POWER_SENSORS = [
    # Generation
    _create_power_sensor(AlphaESSNames.Generation, "Instantaneous Generation"),

    # Individual PV strings (only in FULL access)
    _create_power_sensor(AlphaESSNames.PPV1, "Instantaneous PPV1"),
    _create_power_sensor(AlphaESSNames.PPV2, "Instantaneous PPV2"),
    _create_power_sensor(AlphaESSNames.PPV3, "Instantaneous PPV3"),
    _create_power_sensor(AlphaESSNames.PPV4, "Instantaneous PPV4"),

    # Grid phases (only in FULL access)
    _create_power_sensor(AlphaESSNames.GridIOL1, "Instantaneous Grid I/O L1"),
    _create_power_sensor(AlphaESSNames.GridIOL2, "Instantaneous Grid I/O L2"),
    _create_power_sensor(AlphaESSNames.GridIOL3, "Instantaneous Grid I/O L3"),

    # Totals (available in both FULL and LIMITED)
    _create_power_sensor(AlphaESSNames.GridIOTotal, "Instantaneous Grid I/O Total"),
    _create_power_sensor(AlphaESSNames.Load, "Instantaneous Load"),

    # Battery
    _create_power_sensor(AlphaESSNames.BatteryIO, "Instantaneous Battery I/O"),

    # DC meter
    _create_power_sensor(AlphaESSNames.pmeterDc, "pmeterDc", "mdi:current-dc"),

    # Unknown purpose
    _create_power_sensor(AlphaESSNames.pev, "pev"),
    _create_power_sensor(AlphaESSNames.PrealL1, "PrealL1"),
    _create_power_sensor(AlphaESSNames.PrealL2, "PrealL2"),
    _create_power_sensor(AlphaESSNames.PrealL3, "PrealL3"),
]

# ============================================================================
# BATTERY SENSORS - Battery state and configuration
# ============================================================================

BATTERY_SENSORS = [
    # Battery state
    AlphaESSSensorDescription(
        key=AlphaESSNames.BatterySOC,
        name="Instantaneous Battery SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),

    # Alternative SOC (only in LIMITED access)
    AlphaESSSensorDescription(
        key=AlphaESSNames.StateOfCharge,
        name="State of Charge",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),

    # Battery capacity info
    _create_diagnostic_sensor(
        AlphaESSNames.usCapacity,
        "Maximum Battery Capacity",
        "mdi:home-percent",
        PERCENTAGE,
        state_class=SensorStateClass.TOTAL
    ),
    _create_diagnostic_sensor(
        AlphaESSNames.cobat,
        "Installed Capacity",
        "mdi:battery-heart-variant",
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL
    ),
    _create_diagnostic_sensor(
        AlphaESSNames.surplusCobat,
        "Current Capacity",
        "mdi:battery-heart-variant",
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL
    ),

    # Battery model
    _create_diagnostic_sensor(
        AlphaESSNames.mbat,
        "Battery Model",
        "mdi:battery-heart-variant"
    ),
]

# ============================================================================
# LOCAL IP SENSORS
# ============================================================================

LOCAL_IP_SYSTEM_SENSORS = [
    AlphaESSSensorDescription(
        key=AlphaESSNames.softwareVersion,
        name="software Version",
        icon="mdi:diversify",
        device_class=SensorDeviceClass.ENUM,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.localIP,
        name="IP Address",
        icon="mdi:ip-network",
        device_class=SensorDeviceClass.ENUM,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.hardwareVersion,
        name="Hardware Version",
        icon="mdi:wrench",
        device_class=SensorDeviceClass.ENUM,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.cloudConnectionStaus,
        name="Cloud Connection Status",
        icon="mdi:cloud-cog",
        native_unit_of_measurement=None,
        state_class=None,
    ),
]

# ============================================================================
# SYSTEM STATUS & PERFORMANCE SENSORS
# ============================================================================

SYSTEM_STATUS_SENSORS = [
    # Financial
    AlphaESSSensorDescription(
        key=AlphaESSNames.Income,
        name="Total Income",
        icon="mdi:cash-multiple",
        native_unit_of_measurement=CURRENCY_DOLLAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
    ),

    # Self-sufficiency metrics
    AlphaESSSensorDescription(
        key=AlphaESSNames.SelfConsumption,
        name="Self Consumption",
        icon="mdi:home-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.SelfSufficiency,
        name="Self Sufficiency",
        icon="mdi:home-percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
    ),

    # System status
    AlphaESSSensorDescription(
        key=AlphaESSNames.EmsStatus,
        name="EMS Status",
        icon="mdi:home-battery",
        device_class=SensorDeviceClass.ENUM,
        state_class=None,  # ENUM sensors cannot have a state_class
        entity_category=EntityCategory.DIAGNOSTIC
    ),

    # System specs
    _create_diagnostic_sensor(
        AlphaESSNames.poinv,
        "Inverter nominal Power",
        "mdi:lightning-bolt",
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL

    ),
    _create_diagnostic_sensor(
        AlphaESSNames.popv,
        "Pv nominal Power",
        "mdi:lightning-bolt",
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL
    ),

    # Environmental impact
    AlphaESSSensorDescription(
        key=AlphaESSNames.carbonReduction,
        name="Co2 Reduction",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:molecule-co2",
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.treePlanted,
        name="Trees Planted",
        native_unit_of_measurement=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tree",
    ),
]

# ============================================================================
# SCHEDULING SENSORS - Charge/discharge time configurations
# ============================================================================

SCHEDULING_SENSORS = [
    _create_diagnostic_sensor(
        AlphaESSNames.ChargeTime1, "Charge Time 1", "mdi:clock-time-ten"
    ),
    _create_diagnostic_sensor(
        AlphaESSNames.ChargeTime2, "Charge Time 2", "mdi:clock-time-ten"
    ),
    _create_diagnostic_sensor(
        AlphaESSNames.DischargeTime1, "Discharge Time 1", "mdi:clock-time-ten"
    ),
    _create_diagnostic_sensor(
        AlphaESSNames.DischargeTime2, "Discharge Time 2", "mdi:clock-time-ten"
    ),
    _create_diagnostic_sensor(
        AlphaESSNames.ChargeRange, "Charging Range", "mdi:battery-lock-open"
    ),
]

# ============================================================================
# EV CHARGER SENSORS
# ============================================================================

EV_POWER_SENSORS = [
    _create_power_sensor(
        AlphaESSNames.ElectricVehiclePowerOne,
        "Electric Vehicle Power One",
        "mdi:car-electric"
    ),
    _create_power_sensor(
        AlphaESSNames.ElectricVehiclePowerTwo,
        "Electric Vehicle Power Two",
        "mdi:car-electric"
    ),
    _create_power_sensor(
        AlphaESSNames.ElectricVehiclePowerThree,
        "Electric Vehicle Power Three",
        "mdi:car-electric"
    ),
    _create_power_sensor(
        AlphaESSNames.ElectricVehiclePowerFour,
        "Electric Vehicle Power Four",
        "mdi:car-electric"
    ),
]

EV_CHARGING_DETAILS: List[AlphaESSSensorDescription] = [
    AlphaESSSensorDescription(
        key=AlphaESSNames.evchargersn,
        name="EV Charger S/N",
        icon="mdi:ev-station",
        native_unit_of_measurement=None,
        state_class=None,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.evchargermodel,
        name="EV Charger Model",
        icon="mdi:ev-station",
        native_unit_of_measurement=None,
        state_class=None,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.evchargerstatusraw,
        name="EV Charger Status Raw",
        icon="mdi:ev-station",
        native_unit_of_measurement=None,
        state_class=None,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.evchargerstatus,
        name="EV Charger Status",
        icon="mdi:ev-station",
        device_class="enum",
        native_unit_of_measurement=None,
        state_class=None,
    ),
    AlphaESSSensorDescription(
        key=AlphaESSNames.evcurrentsetting,
        name="Household current setup",
        icon="mdi:ev-station",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement="A",
        state_class=None,
    )
]

# ============================================================================
# CONTROL ENTITIES - Buttons and Numbers
# ============================================================================

SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS: List[AlphaESSButtonDescription] = [
    AlphaESSButtonDescription(
        key=AlphaESSNames.ButtonDischargeFifteen,
        name="15 Minute Discharge",
        icon="mdi:battery-negative",
        entity_category=EntityCategory.CONFIG,
    ),
    AlphaESSButtonDescription(
        key=AlphaESSNames.ButtonDischargeThirty,
        name="30 Minute Discharge",
        icon="mdi:battery-negative",
        entity_category=EntityCategory.CONFIG,
    ),
    AlphaESSButtonDescription(
        key=AlphaESSNames.ButtonDischargeSixty,
        name="60 Minute Discharge",
        icon="mdi:battery-negative",
        entity_category=EntityCategory.CONFIG,
    ),
    AlphaESSButtonDescription(
        key=AlphaESSNames.ButtonChargeFifteen,
        name="15 Minute Charge",
        icon="mdi:battery-positive",
        entity_category=EntityCategory.CONFIG,
    ),
    AlphaESSButtonDescription(
        key=AlphaESSNames.ButtonChargeThirty,
        name="30 Minute Charge",
        icon="mdi:battery-positive",
        entity_category=EntityCategory.CONFIG,
    ),
    AlphaESSButtonDescription(
        key=AlphaESSNames.ButtonChargeSixty,
        name="60 Minute Charge",
        icon="mdi:battery-positive",
        entity_category=EntityCategory.CONFIG,
    ),
    AlphaESSButtonDescription(
        key=AlphaESSNames.ButtonRechargeConfig,
        name="Reset Charge/Discharge",
        icon="mdi:battery-off",
        entity_category=EntityCategory.CONFIG,
    )
]

DISCHARGE_AND_CHARGE_NUMBERS: List[AlphaESSNumberDescription] = [
    AlphaESSNumberDescription(
        key=AlphaESSNames.batHighCap,
        name="batHighCap",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:battery-sync",
        native_unit_of_measurement=PERCENTAGE,
    ),
    AlphaESSNumberDescription(
        key=AlphaESSNames.batUseCap,
        name="batUseCap",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:battery-sync",
        native_unit_of_measurement=PERCENTAGE,
    )
]

EV_DISCHARGE_AND_CHARGE_BUTTONS: List[AlphaESSButtonDescription] = [
    AlphaESSButtonDescription(
        key=AlphaESSNames.stopcharging,
        name="Stop Charging",
        icon="mdi:battery-off",
        entity_category=EntityCategory.CONFIG,
    ),
    AlphaESSButtonDescription(
        key=AlphaESSNames.startcharging,
        name="Start Charging",
        icon="mdi:battery-plus",
        entity_category=EntityCategory.CONFIG,
    )
]

# ============================================================================
# SENSOR COLLECTIONS - Full vs Limited API Access
# ============================================================================

# Sensors exclusive to FULL API access
FULL_ONLY_SENSORS = [
                        # Individual PV string monitoring
                        sensor for sensor in INSTANTANEOUS_POWER_SENSORS
                        if sensor.key in [
        AlphaESSNames.PPV1, AlphaESSNames.PPV2,
        AlphaESSNames.PPV3, AlphaESSNames.PPV4
    ]
                    ] + [
                        # Individual grid phase monitoring
                        sensor for sensor in INSTANTANEOUS_POWER_SENSORS
                        if sensor.key in [
        AlphaESSNames.GridIOL1, AlphaESSNames.GridIOL2,
        AlphaESSNames.GridIOL3
    ]
                    ] + [
                        # Solar to grid energy flow
                        _create_energy_sensor(AlphaESSNames.SolarToGrid, "Solar to Grid"),
                        _create_energy_sensor(AlphaESSNames.SolarToLoad, "Solar to Load"),

                        # Additional sensors only in full
                        _create_power_sensor(AlphaESSNames.Generation, "Instantaneous Generation"),
                        AlphaESSSensorDescription(
                            key=AlphaESSNames.BatterySOC,
                            name="Instantaneous Battery SOC",
                            native_unit_of_measurement=PERCENTAGE,
                            device_class=SensorDeviceClass.BATTERY,
                            state_class=SensorStateClass.MEASUREMENT,
                        ),
                        _create_power_sensor(AlphaESSNames.BatteryIO, "Instantaneous Battery I/O"),
                    ]

# Sensors exclusive to LIMITED API access
LIMITED_ONLY_SENSORS = [
    AlphaESSSensorDescription(
        key=AlphaESSNames.StateOfCharge,
        name="State of Charge",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
]

# Common sensors available in both FULL and LIMITED
COMMON_SENSORS = (
        ENERGY_FLOW_SENSORS +
        SYSTEM_STATUS_SENSORS +
        SCHEDULING_SENSORS +
        EV_POWER_SENSORS +
        [sensor for sensor in BATTERY_SENSORS if sensor.key != AlphaESSNames.StateOfCharge] +
        [sensor for sensor in INSTANTANEOUS_POWER_SENSORS
         if sensor.key in [
             AlphaESSNames.GridIOTotal, AlphaESSNames.Load,
             AlphaESSNames.pmeterDc, AlphaESSNames.pev,
             AlphaESSNames.PrealL1, AlphaESSNames.PrealL2, AlphaESSNames.PrealL3
         ]]
)

# Remove duplicates from COMMON_SENSORS
full_only_keys = {sensor.key for sensor in FULL_ONLY_SENSORS}
COMMON_SENSORS = [sensor for sensor in COMMON_SENSORS if sensor.key not in full_only_keys]

# Final collections
FULL_SENSOR_DESCRIPTIONS: List[AlphaESSSensorDescription] = (
        COMMON_SENSORS + FULL_ONLY_SENSORS
)

LIMITED_SENSOR_DESCRIPTIONS: List[AlphaESSSensorDescription] = (
        COMMON_SENSORS + LIMITED_ONLY_SENSORS
)
