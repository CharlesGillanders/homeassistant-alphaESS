from typing import List
from homeassistant.components.number import NumberEntity
from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.number import RestoreNumber
import logging
from custom_components.alphaess import DOMAIN, AlphaESSDataUpdateCoordinator
from custom_components.alphaess.const import INVERTER_SETTING_BLACKLIST
from custom_components.alphaess.enums import AlphaESSNames
from custom_components.alphaess.sensorlist import DISCHARGE_AND_CHARGE_NUMBERS

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: AlphaESSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    number_entities: List[NumberEntity] = []

    full_number_supported_states = {
        description.key: description for description in DISCHARGE_AND_CHARGE_NUMBERS
    }

    for serial, data in coordinator.data.items():
        model = data.get("Model")
        if model not in INVERTER_SETTING_BLACKLIST:
            for description in full_number_supported_states:
                number_entities.append(
                    AlphaNumber(coordinator, serial, entry, full_number_supported_states[description]))

    async_add_entities(number_entities)


class AlphaNumber(CoordinatorEntity, RestoreNumber):
    """Battery use capacity number entity."""

    def __init__(self, coordinator, serial, config, full_number_supported_states):
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._serial = serial
        self._config = config
        self.key = full_number_supported_states.key
        self._entity_category = full_number_supported_states.entity_category
        self._native_unit_of_measurement = full_number_supported_states.native_unit_of_measurement
        self._icon = full_number_supported_states.icon
        self._name = full_number_supported_states.name

        if self.key is AlphaESSNames.batHighCap:
            self._def_initial_value = float(90)
        else:
            self._def_initial_value = float(10)

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

        try:
            last_value = last_state.native_value
            if last_value is not None:
                self._attr_native_value = last_value
            await self.save_value(self._attr_native_value)
        except Exception:
            self._attr_native_value = self._def_initial_value
            _LOGGER.info(
                f"No saved state found for {self._name}. Using initial value: {self._def_initial_value}")
            await self.save_value(self._attr_native_value)
            self.async_write_ha_state()

    async def save_value(self, value):
        if DOMAIN not in self.hass.data:
            self.hass.data[DOMAIN] = {}
        if self._serial not in self.hass.data[DOMAIN]:
            self.hass.data[DOMAIN][self._serial] = {}

        self.hass.data[DOMAIN][self._serial][self._name] = value
        _LOGGER.info(f"SAVED DATA TO HASS, VALUE {self.hass.data[DOMAIN][self._serial].get(self._name, None)}")

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        await self.save_value(value)
        self.async_write_ha_state()

    @property
    def native_value(self):
        return self._attr_native_value

    @property
    def name(self):
        return f"{self._name}"

    @property
    def mode(self):
        return "box"

    @property
    def native_unit_of_measurement(self):
        return self._native_unit_of_measurement

    @property
    def unique_id(self):
        return f"{self._config.entry_id}_{self._serial} - {self._name}"

    @property
    def entity_category(self):
        return self._entity_category

    @property
    def icon(self):
        return self._icon
