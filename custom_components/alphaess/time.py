"""Time platform for AlphaESS integration."""
from datetime import time
from typing import List
import logging

from homeassistant.components.time import TimeEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, INVERTER_SETTING_BLACKLIST, CONF_SERIAL_NUMBER, SUBENTRY_TYPE_INVERTER
from .coordinator import AlphaESSDataUpdateCoordinator
from .enums import AlphaESSNames
from .sensorlist import CHARGE_DISCHARGE_TIMES
from .sensor import _build_inverter_device_info

_LOGGER: logging.Logger = logging.getLogger(__package__)

# Mapping from coordinator key to the API parameter position
# Charge API: updateChargeConfigInfo(serial, batHighCap, gridCharge, timeChae1, timeChae2, timeChaf1, timeChaf2)
# Discharge API: updateDisChargeConfigInfo(serial, batUseCap, ctrDis, timeDise1, timeDise2, timeDisf1, timeDisf2)
CHARGE_TIME_KEYS = {
    "charge_timeChaf1",
    "charge_timeChae1",
    "charge_timeChaf2",
    "charge_timeChae2",
}

DISCHARGE_TIME_KEYS = {
    "discharge_timeDisf1",
    "discharge_timeDise1",
    "discharge_timeDisf2",
    "discharge_timeDise2",
}


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up AlphaESS time entities."""
    coordinator: AlphaESSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_INVERTER:
            continue

        serial = subentry.data.get(CONF_SERIAL_NUMBER)
        if not serial or serial not in coordinator.data:
            continue

        data = coordinator.data[serial]
        model = data.get("Model")
        inverter_device_info = _build_inverter_device_info(coordinator, serial, data)

        time_entities: List[TimeEntity] = []

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

    def _value_from_coordinator(self) -> time:
        """Parse the time value from coordinator data."""
        data = self._coordinator.data.get(self._serial, {})
        raw_time = data.get(self._coordinator_key)
        if raw_time:
            try:
                parts = raw_time.split(":")
                return time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                pass
        return time(0, 0)

    def _handle_coordinator_update(self) -> None:
        """Update local value when coordinator refreshes."""
        self._attr_native_value = self._value_from_coordinator()
        super()._handle_coordinator_update()

    async def async_set_value(self, value: time) -> None:
        """Update the time value via API, rounded to nearest 15 minutes."""
        # Round to nearest 15-minute interval
        total_minutes = value.hour * 60 + value.minute
        rounded_minutes = round(total_minutes / 15) * 15
        # Handle 24:00 edge case (rounds back to 00:00)
        rounded_minutes = rounded_minutes % 1440
        value = time(rounded_minutes // 60, rounded_minutes % 60)
        time_str = value.strftime("%H:%M")

        # Update displayed value immediately
        self._attr_native_value = value
        self.async_write_ha_state()

        if self._coordinator_key in CHARGE_TIME_KEYS:
            await self._update_charge_config(time_str)
        elif self._coordinator_key in DISCHARGE_TIME_KEYS:
            await self._update_discharge_config(time_str)

        await self._coordinator.async_request_refresh()

    async def _update_charge_config(self, new_time_str: str) -> None:
        """Send updated charge config to the API."""
        data = self._coordinator.data.get(self._serial, {})

        # Read current values
        current = {
            "timeChaf1": data.get("charge_timeChaf1") or "00:00",
            "timeChae1": data.get("charge_timeChae1") or "00:00",
            "timeChaf2": data.get("charge_timeChaf2") or "00:00",
            "timeChae2": data.get("charge_timeChae2") or "00:00",
        }

        # Map coordinator key to API parameter
        key_map = {
            "charge_timeChaf1": "timeChaf1",
            "charge_timeChae1": "timeChae1",
            "charge_timeChaf2": "timeChaf2",
            "charge_timeChae2": "timeChae2",
        }

        api_key = key_map[self._coordinator_key]
        current[api_key] = new_time_str

        bat_high_cap = data.get(AlphaESSNames.batHighCap, 90)
        grid_charge = data.get("gridCharge", 1)

        result = await self._coordinator.api.updateChargeConfigInfo(
            self._serial,
            bat_high_cap,
            grid_charge,
            current["timeChae1"],
            current["timeChae2"],
            current["timeChaf1"],
            current["timeChaf2"],
        )

        _LOGGER.info(
            "Updated charge config for %s: %s=%s, Result: %s",
            self._serial, self._coordinator_key, new_time_str, result,
        )

    async def _update_discharge_config(self, new_time_str: str) -> None:
        """Send updated discharge config to the API."""
        data = self._coordinator.data.get(self._serial, {})

        # Read current values
        current = {
            "timeDisf1": data.get("discharge_timeDisf1") or "00:00",
            "timeDise1": data.get("discharge_timeDise1") or "00:00",
            "timeDisf2": data.get("discharge_timeDisf2") or "00:00",
            "timeDise2": data.get("discharge_timeDise2") or "00:00",
        }

        # Map coordinator key to API parameter
        key_map = {
            "discharge_timeDisf1": "timeDisf1",
            "discharge_timeDise1": "timeDise1",
            "discharge_timeDisf2": "timeDisf2",
            "discharge_timeDise2": "timeDise2",
        }

        api_key = key_map[self._coordinator_key]
        current[api_key] = new_time_str

        bat_use_cap = data.get(AlphaESSNames.batUseCap, 10)
        ctr_dis = data.get("ctrDis", 1)

        result = await self._coordinator.api.updateDisChargeConfigInfo(
            self._serial,
            bat_use_cap,
            ctr_dis,
            current["timeDise1"],
            current["timeDise2"],
            current["timeDisf1"],
            current["timeDisf2"],
        )

        _LOGGER.info(
            "Updated discharge config for %s: %s=%s, Result: %s",
            self._serial, self._coordinator_key, new_time_str, result,
        )

    @property
    def available(self) -> bool:
        """Time controls require cloud API to function."""
        if not self.coordinator.last_update_success:
            return False
        return self._coordinator.cloud_available

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
