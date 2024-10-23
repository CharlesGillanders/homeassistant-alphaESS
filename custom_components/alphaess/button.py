from datetime import datetime
from typing import List
import logging
from homeassistant.components.button import ButtonEntity, ButtonDeviceClass
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AlphaESSDataUpdateCoordinator
from .const import DOMAIN, ALPHA_POST_REQUEST_RESTRICTION
from .sensorlist import SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS
from .enums import AlphaESSNames

_LOGGER: logging.Logger = logging.getLogger(__package__)

last_discharge_update = {}
last_charge_update = {}


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
        self._key = key_supported_states.key
        self._movement_state = self.name.split()[-1]
        self._icon = key_supported_states.icon
        self._entity_category = key_supported_states.entity_category
        self._config = config
        if self._key != AlphaESSNames.ButtonRechargeConfig:
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
        global last_discharge_update
        global last_charge_update

        async def handle_time_restriction(last_update_dict, update_fn, update_key):
            local_current_time = datetime.now()
            last_update = last_update_dict.get(self._serial)
            if last_update is None or local_current_time - last_update >= ALPHA_POST_REQUEST_RESTRICTION:
                last_update_dict[self._serial] = local_current_time
                await update_fn(update_key, self._serial, self._time)
            else:
                remaining_time = ALPHA_POST_REQUEST_RESTRICTION - (local_current_time - last_update)
                minutes, seconds = divmod(remaining_time.total_seconds(), 60)
                _LOGGER.warning(
                    f"{self._serial}: Has not been {ALPHA_POST_REQUEST_RESTRICTION.total_seconds() // 60} minutes since last {update_key} config post call."
                    f"Please wait {int(minutes)} minutes and {int(seconds)} seconds."
                )
            return last_update_dict

        current_time = datetime.now()

        if self._key == AlphaESSNames.ButtonRechargeConfig:
            if (last_charge_update.get(self._serial) is None or current_time - last_charge_update[self._serial] >= ALPHA_POST_REQUEST_RESTRICTION) and \
                    (last_discharge_update.get(self._serial) is None or current_time - last_discharge_update[self._serial] >= ALPHA_POST_REQUEST_RESTRICTION):
                last_discharge_update[self._serial] = last_charge_update[self._serial] = current_time
                await self._coordinator.reset_config(self._serial)
            else:
                last_charge_update = await handle_time_restriction(last_charge_update, self._coordinator.update_charge,
                                                                   "charge")
                last_discharge_update = await handle_time_restriction(last_discharge_update,
                                                                      self._coordinator.update_discharge, "discharge")
        elif self._movement_state == "Discharge":
            last_discharge_update = await handle_time_restriction(last_discharge_update,
                                                                  self._coordinator.update_discharge, "batUseCap")
        elif self._movement_state == "Charge":
            last_charge_update = await handle_time_restriction(last_charge_update, self._coordinator.update_charge,
                                                               "batHighCap")

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
