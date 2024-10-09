from typing import List
from homeassistant.components.number import NumberEntity
from homeassistant.const import EntityCategory, PERCENTAGE
from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.number import RestoreNumber
import logging
from custom_components.alphaess import DOMAIN, AlphaESSDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: AlphaESSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    number_entities: List[NumberEntity] = []

    for serial, data in coordinator.data.items():
        model = data.get("Model")
        if model != "Storion-S5":
            number_entities.append(batUseCapNumber(coordinator, serial, entry, float(10)))

    async_add_entities(number_entities)


class batUseCapNumber(CoordinatorEntity, RestoreNumber):
    """Battery use capacity number entity."""

    def __init__(self, coordinator, serial, config, initial_value):
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._serial = serial
        self._config = config
        self._def_initial_value = initial_value

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

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_number_data()
        last_value = last_state.native_value
        if last_value is not None:
            self._attr_native_value = last_value
        else:
            self._attr_native_value = self._def_initial_value
            _LOGGER.info(f"No saved state found for batUseCapNumber. Using initial value: {self._def_initial_value}")
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()

    @property
    def native_value(self):
        return self._attr_native_value

    @property
    def name(self):
        return f"{self._serial}_batUseCap_cutoff"

    @property
    def mode(self):
        return "box"

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE

    @property
    def unique_id(self):
        return f"{self._config.entry_id}_{self._serial} - batUseCap_cutoff"

    @property
    def entity_category(self):
        return EntityCategory.CONFIG

    @property
    def icon(self):
        return "mdi:battery-sync"





