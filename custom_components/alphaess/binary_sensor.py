"""Binary sensor platform for AlphaESS integration."""
import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PARENT_INVERTER,
    CONF_SERIAL_NUMBER,
    INVERTER_SETTING_BLACKLIST,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from .coordinator import AlphaESSDataUpdateCoordinator
from .device import build_ev_charger_device_info, build_inverter_device_info
from .enums import AlphaESSNames
from .sensorlist import EV_CHARGER_BINARY_SENSORS, INVERTER_BINARY_SENSORS

_LOGGER = logging.getLogger(__name__)

# Read-only platform; the coordinator centralizes polling.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up EV charger readiness binary sensors."""
    coordinator: AlphaESSDataUpdateCoordinator = entry.runtime_data

    ev_binary_supported_states = {
        description.key: description for description in EV_CHARGER_BINARY_SENSORS
    }

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_INVERTER:
            serial = subentry.data.get(CONF_SERIAL_NUMBER)
            if not serial or serial not in coordinator.data:
                continue

            data = coordinator.data[serial]
            entities: list[BinarySensorEntity] = []

            if data.get("Model") not in INVERTER_SETTING_BLACKLIST:
                inverter_device_info = build_inverter_device_info(serial, data)
                for description in INVERTER_BINARY_SENSORS:
                    entities.append(
                        AlphaScheduleControlBinarySensor(
                            coordinator,
                            serial,
                            entry,
                            description,
                            device_info=inverter_device_info,
                        )
                    )

            ev_charger = data.get(AlphaESSNames.evchargersn)
            ev_subentry_serials = {
                sub.data.get(CONF_SERIAL_NUMBER)
                for sub in entry.subentries.values()
                if sub.subentry_type == SUBENTRY_TYPE_EV_CHARGER
            }
            if ev_charger and ev_charger not in ev_subentry_serials:
                ev_device_info = build_ev_charger_device_info(data)
                for description in ev_binary_supported_states.values():
                    entities.append(
                        AlphaEVReadinessBinarySensor(
                            coordinator,
                            serial,
                            entry,
                            description,
                            ev_serial=ev_charger,
                            device_info=ev_device_info,
                        )
                    )

            if entities:
                async_add_entities(entities, config_subentry_id=subentry.subentry_id)

        elif subentry.subentry_type == SUBENTRY_TYPE_EV_CHARGER:
            parent_serial = subentry.data.get(CONF_PARENT_INVERTER)
            if not parent_serial or parent_serial not in coordinator.data:
                continue

            data = coordinator.data[parent_serial]
            ev_charger = data.get(AlphaESSNames.evchargersn)
            if not ev_charger:
                continue

            ev_device_info = build_ev_charger_device_info(data)
            ev_entities: list[BinarySensorEntity] = []
            for description in ev_binary_supported_states.values():
                ev_entities.append(
                    AlphaEVReadinessBinarySensor(
                        coordinator,
                        parent_serial,
                        entry,
                        description,
                        ev_serial=ev_charger,
                        device_info=ev_device_info,
                    )
                )

            if ev_entities:
                async_add_entities(ev_entities, config_subentry_id=subentry.subentry_id)


class AlphaScheduleControlBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Whether AlphaESS time-based charge/discharge control is active.

    On when either enable flag is set in whichever schedule store governs
    the system (periodic cycles or the legacy backup flags). Off means a
    non-timed mode such as Self Consumption Plus is running. Unavailable
    while no schedule store is readable.
    """

    def __init__(self, coordinator, serial, config, description, device_info=None):
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._serial = serial
        self._config = config
        self._description = description
        self._name = description.name
        self._icon = description.icon
        self._entity_category = description.entity_category

        if device_info:
            self._attr_device_info = device_info

    @property
    def is_on(self) -> bool | None:
        value = self._coordinator.data.get(self._serial, {}).get(
            AlphaESSNames.TimeBasedControl
        )
        return None if value is None else bool(value)

    @property
    def available(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        serial_data = self._coordinator.data.get(self._serial)
        if serial_data is None:
            return False
        return (
            self._coordinator.cloud_available
            and AlphaESSNames.TimeBasedControl in serial_data
        )

    @property
    def unique_id(self):
        return f"{self._config.entry_id}_{self._serial} - {self._name}"

    @property
    def name(self):
        return f"{self._name}"

    @property
    def suggested_object_id(self):
        return f"{self._serial} {self._name}"

    @property
    def entity_category(self):
        return self._entity_category

    @property
    def icon(self):
        return self._icon


class AlphaEVReadinessBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Readiness sensor for EV charger start/stop commands."""

    def __init__(self, coordinator, serial, config, description, ev_serial=None, device_info=None):
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._serial = serial
        self._config = config
        self._description = description
        self._ev_serial = ev_serial
        self._name = description.name
        self._icon = description.icon
        self._entity_category = description.entity_category
        self._direction = description.direction

        if device_info:
            self._attr_device_info = device_info

    @property
    def is_on(self) -> bool | None:
        """Return readiness to execute EV command."""
        if self._direction is None:
            return None

        if self._coordinator.get_ev_charger_status_raw(self._serial) is None:
            return None

        return self._coordinator.can_control_ev(self._serial, self._direction)

    @property
    def available(self) -> bool:
        """Readiness sensors require cloud EV data."""
        if not self.coordinator.last_update_success:
            return False
        if not self._coordinator.cloud_available:
            return False

        serial_data = self._coordinator.data.get(self._serial, {})
        return serial_data.get("EV Charger S/N") is not None

    @property
    def unique_id(self):
        return f"{self._config.entry_id}_{self._serial} - {self._name}"

    @property
    def name(self):
        return f"{self._name}"

    @property
    def suggested_object_id(self):
        return f"{self._serial} {self._name}"

    @property
    def entity_category(self):
        return self._entity_category

    @property
    def icon(self):
        return self._icon
