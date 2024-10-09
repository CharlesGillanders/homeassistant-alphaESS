from typing import List
import logging
from homeassistant.components.button import ButtonEntity, ButtonDeviceClass
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AlphaESSDataUpdateCoordinator
from .const import DOMAIN
from .sensorlist import SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: AlphaESSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    button_entities: List[ButtonEntity] = []

    full_button_supported_states = {
        description.key: description for description in SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS
    }

    for serial, data in coordinator.data.items():
        model = data.get("Model")
        if model != "Storion-S5":
            for description in full_button_supported_states:
                button_entities.append(
                    AlphaESSBatteryButton(coordinator, entry, serial, full_button_supported_states[description]))

    async_add_entities(button_entities)


class AlphaESSBatteryButton(CoordinatorEntity, ButtonEntity):

    def __init__(self, coordinator, config, serial, key_supported_states):
        super().__init__(coordinator)
        self._serial = serial
        self._coordinator = coordinator
        self._name = key_supported_states.name
        self._movement_state = self.name.split()[-1]
        self._icon = key_supported_states.icon
        self._entity_category = key_supported_states.entity_category
        self._config = config
        self._time = int(self._name.split()[0])

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

    async def async_press(self) -> None:
        if self._movement_state == "Discharge":
            await self._coordinator.update_discharge("batUseCap", self._serial, self._time)
        elif self._movement_state == "Charge":
            await self._coordinator.update_charge("batHighCap", self._serial, self._time)

    @property
    def unique_id(self):
        return f"{self._config.entry_id}_{self._serial} - {self._name}"

    @property
    def device_class(self):
        return ButtonDeviceClass.IDENTIFY

    @property
    def entity_category(self):
        return self._entity_category

    @property
    def name(self):
        return f"{self._serial}_{self._name}"

    @property
    def icon(self):
        return self._icon
