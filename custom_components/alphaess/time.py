"""Time platform for AlphaESS integration."""
import logging
from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL_NUMBER, INVERTER_SETTING_BLACKLIST, SUBENTRY_TYPE_INVERTER
from .coordinator import AlphaESSDataUpdateCoordinator
from .device import build_inverter_device_info
from .sensorlist import CHARGE_DISCHARGE_TIMES

_LOGGER = logging.getLogger(__name__)

# Serialize time writes; the AlphaESS API rate-limits config writes.
PARALLEL_UPDATES = 1

# Coordinator key -> the legacy API field the coordinator writers expect.
CHARGE_TIME_KEYS = {
    "charge_timeChaf1": "timeChaf1",
    "charge_timeChae1": "timeChae1",
    "charge_timeChaf2": "timeChaf2",
    "charge_timeChae2": "timeChae2",
}

DISCHARGE_TIME_KEYS = {
    "discharge_timeDisf1": "timeDisf1",
    "discharge_timeDise1": "timeDise1",
    "discharge_timeDisf2": "timeDisf2",
    "discharge_timeDise2": "timeDise2",
}

# Each slot's other half. AlphaESS has no "empty" value: an unused slot is
# stored as start == end (usually 00:00-00:00), so a slot whose halves match
# is displayed as unknown rather than as a zero-length midnight window.
_SLOT_PARTNERS = {
    "charge_timeChaf1": "charge_timeChae1",
    "charge_timeChae1": "charge_timeChaf1",
    "charge_timeChaf2": "charge_timeChae2",
    "charge_timeChae2": "charge_timeChaf2",
    "discharge_timeDisf1": "discharge_timeDise1",
    "discharge_timeDise1": "discharge_timeDisf1",
    "discharge_timeDisf2": "discharge_timeDise2",
    "discharge_timeDise2": "discharge_timeDisf2",
}


def _parse_time(raw) -> time | None:
    """Parse an HH:MM string, tolerating missing or malformed values."""
    if not raw:
        return None
    try:
        parts = str(raw).split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up AlphaESS time entities."""
    coordinator: AlphaESSDataUpdateCoordinator = entry.runtime_data

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_INVERTER:
            continue

        serial = subentry.data.get(CONF_SERIAL_NUMBER)
        if not serial or serial not in coordinator.data:
            continue

        data = coordinator.data[serial]
        model = data.get("Model")
        inverter_device_info = build_inverter_device_info(serial, data)

        time_entities: list[TimeEntity] = []

        if model not in INVERTER_SETTING_BLACKLIST:
            for description in CHARGE_DISCHARGE_TIMES:
                time_entities.append(
                    AlphaTime(
                        coordinator, serial, entry, description,
                        device_info=inverter_device_info,
                    )
                )

        if time_entities:
            async_add_entities(
                time_entities,
                config_subentry_id=subentry.subentry_id,
            )


class AlphaTime(CoordinatorEntity, TimeEntity):
    """Time entity for charge/discharge time slots."""

    def __init__(self, coordinator, serial, config, description, device_info=None):
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._serial = serial
        self._config = config
        self._description = description
        self.key = description.key
        self._coordinator_key = description.coordinator_key
        self._entity_category = description.entity_category
        self._icon = description.icon
        self._name = description.name

        if device_info:
            self._attr_device_info = device_info

    @property
    def native_value(self) -> time | None:
        """Return the current time value from coordinator data."""
        if self._attr_native_value is not None:
            return self._attr_native_value
        return self._value_from_coordinator()

    def _value_from_coordinator(self) -> time | None:
        """Parse the time value from coordinator data.

        A slot whose start equals its end is the API's representation of
        "not set", so both halves read as unknown instead of 00:00. A half that
        was explicitly staged keeps showing what was staged: while the other
        half is still being edited the two can match briefly, and blanking the
        value then reads as a lost edit — to a person and to an automation that
        writes one half at a time and reads it back.
        """
        data = self._coordinator.data.get(self._serial, {})
        value = _parse_time(data.get(self._coordinator_key))
        if value is None:
            return None
        if self._coordinator.is_schedule_field_staged(
            self._serial, self._coordinator_key
        ):
            return value
        partner = _parse_time(data.get(_SLOT_PARTNERS[self._coordinator_key]))
        if partner is not None and partner == value:
            return None
        return value

    def _handle_coordinator_update(self) -> None:
        """Update local value when coordinator refreshes."""
        self._attr_native_value = self._value_from_coordinator()
        super()._handle_coordinator_update()

    async def async_set_value(self, value: time) -> None:
        """Stage a rounded time value for the atomic Apply Schedule action."""
        # Round to nearest 15-minute interval
        total_minutes = value.hour * 60 + value.minute
        rounded_minutes = round(total_minutes / 15) * 15
        # Handle 24:00 edge case (rounds back to 00:00)
        rounded_minutes = rounded_minutes % 1440
        value = time(rounded_minutes // 60, rounded_minutes % 60)
        time_str = value.strftime("%H:%M")

        # Update displayed value immediately (optimistic)
        previous_value = self._attr_native_value
        self._attr_native_value = value
        self.async_write_ha_state()

        try:
            if self._coordinator_key in CHARGE_TIME_KEYS:
                self._coordinator.stage_schedule_change(
                    self._serial,
                    charge={CHARGE_TIME_KEYS[self._coordinator_key]: time_str},
                )
            elif self._coordinator_key in DISCHARGE_TIME_KEYS:
                self._coordinator.stage_schedule_change(
                    self._serial,
                    discharge={DISCHARGE_TIME_KEYS[self._coordinator_key]: time_str},
                )
        except Exception:
            _LOGGER.exception("Failed to stage time for %s, reverting", self._coordinator_key)
            self._attr_native_value = previous_value
            self.async_write_ha_state()
            raise

    @property
    def available(self) -> bool:
        """Time controls require the cloud API, a usable schedule store, and
        time-based control being active — in a self-consumption mode there is
        nothing a time edit could act on."""
        if (
            not self.coordinator.last_update_success
            or self._serial not in self._coordinator.data
        ):
            return False
        return (
            self._coordinator.cloud_available
            and self._coordinator.can_modify_time_controls(self._serial)
        )

    @property
    def name(self):
        return f"{self._name}"

    @property
    def unique_id(self):
        return f"{self._config.entry_id}_{self._serial} - {self._name}"

    @property
    def entity_category(self):
        return self._entity_category

    @property
    def icon(self):
        return self._icon
