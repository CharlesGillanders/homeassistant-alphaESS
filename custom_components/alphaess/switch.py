"""Switch platform for AlphaESS integration."""
from typing import Any, List
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, INVERTER_SETTING_BLACKLIST, CONF_SERIAL_NUMBER, SUBENTRY_TYPE_INVERTER,
)
from .coordinator import AlphaESSDataUpdateCoordinator
from .enums import AlphaESSNames
from .sensorlist import CHARGE_DISCHARGE_SWITCHES
from .sensor import _build_inverter_device_info

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up AlphaESS switch entities."""
    coordinator: AlphaESSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    switch_descriptions = {
        description.key: description for description in CHARGE_DISCHARGE_SWITCHES
    }

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_INVERTER:
            continue

        serial = subentry.data.get(CONF_SERIAL_NUMBER)
        if not serial or serial not in coordinator.data:
            continue

        data = coordinator.data[serial]
        model = data.get("Model")
        inverter_device_info = _build_inverter_device_info(coordinator, serial, data)

        switch_entities: List[SwitchEntity] = []

        if model not in INVERTER_SETTING_BLACKLIST:
            for description in switch_descriptions:
                switch_entities.append(
                    AlphaSwitch(
                        coordinator, serial, entry,
                        switch_descriptions[description],
                        device_info=inverter_device_info,
                    )
                )

        if switch_entities:
            async_add_entities(
                switch_entities,
                config_subentry_id=subentry.subentry_id,
            )


class AlphaSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for grid charge / discharge time control."""

    def __init__(self, coordinator, serial, config, description, device_info=None):
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._serial = serial
        self._config = config
        self._description = description
        self._name = description.name
        self._icon = description.icon
        self._entity_category = description.entity_category
        self._coordinator_key = description.coordinator_key

        if device_info:
            self._attr_device_info = device_info

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        data = self._coordinator.data.get(self._serial, {})
        value = data.get(self._coordinator_key)
        if value is None:
            return None
        return int(value) == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on (enable) the setting."""
        await self._set_value(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off (disable) the setting."""
        await self._set_value(0)

    async def _set_value(self, value: int) -> None:
        """Send the updated config to the API."""
        data = self._coordinator.data.get(self._serial, {})

        if self._coordinator_key == "gridCharge":
            bat_high_cap = data.get(AlphaESSNames.batHighCap, 90)
            result = await self._coordinator.api.updateChargeConfigInfo(
                self._serial,
                bat_high_cap,
                value,
                data.get("charge_timeChae1") or "00:00",
                data.get("charge_timeChae2") or "00:00",
                data.get("charge_timeChaf1") or "00:00",
                data.get("charge_timeChaf2") or "00:00",
            )
            _LOGGER.info(
                "Updated gridCharge for %s to %s - Result: %s",
                self._serial, value, result,
            )
        elif self._coordinator_key == "ctrDis":
            bat_use_cap = data.get(AlphaESSNames.batUseCap, 10)
            result = await self._coordinator.api.updateDisChargeConfigInfo(
                self._serial,
                bat_use_cap,
                value,
                data.get("discharge_timeDise1") or "00:00",
                data.get("discharge_timeDise2") or "00:00",
                data.get("discharge_timeDisf1") or "00:00",
                data.get("discharge_timeDisf2") or "00:00",
            )
            _LOGGER.info(
                "Updated ctrDis for %s to %s - Result: %s",
                self._serial, value, result,
            )

        await self._coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        """Switch controls require cloud API to function."""
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
