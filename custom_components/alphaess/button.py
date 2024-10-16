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

last_discharge_update = None
last_charge_update = None


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

        if self._key == AlphaESSNames.ButtonRechargeConfig:
            current_time = datetime.now()
            if last_charge_update is None or current_time - last_charge_update >= ALPHA_POST_REQUEST_RESTRICTION:
                if last_discharge_update is None or current_time - last_discharge_update >= ALPHA_POST_REQUEST_RESTRICTION:
                    last_discharge_update = current_time
                    last_charge_update = current_time
                    await self._coordinator.reset_config(self._serial)
                else:
                    remaining_time = ALPHA_POST_REQUEST_RESTRICTION - (current_time - last_discharge_update)
                    minutes, seconds = divmod(remaining_time.total_seconds(), 60)
                    _LOGGER.warning(
                        f"Has not been {ALPHA_POST_REQUEST_RESTRICTION.total_seconds() // 60} minutes since last discharge config post call. Please wait {int(minutes)} minutes and {int(seconds)} seconds.")
            else:
                remaining_time = ALPHA_POST_REQUEST_RESTRICTION - (current_time - last_charge_update)
                minutes, seconds = divmod(remaining_time.total_seconds(), 60)
                _LOGGER.warning(
                    f"Has not been {ALPHA_POST_REQUEST_RESTRICTION.total_seconds() // 60} minutes since last charge config post call. Please wait {int(minutes)} minutes and {int(seconds)} seconds.")
        elif self._movement_state == "Discharge":
            current_time = datetime.now()
            if last_discharge_update is None or current_time - last_discharge_update >= ALPHA_POST_REQUEST_RESTRICTION:
                last_discharge_update = current_time
                await self._coordinator.update_discharge("batUseCap", self._serial, self._time)
            else:
                remaining_time = ALPHA_POST_REQUEST_RESTRICTION - (current_time - last_discharge_update)
                minutes, seconds = divmod(remaining_time.total_seconds(), 60)
                _LOGGER.warning(
                    f"Has not been {ALPHA_POST_REQUEST_RESTRICTION.total_seconds() // 60} minutes since last discharge config post call. Please wait {int(minutes)} minutes and {int(seconds)} seconds.")
        elif self._movement_state == "Charge":
            current_time = datetime.now()
            if last_charge_update is None or current_time - last_charge_update >= ALPHA_POST_REQUEST_RESTRICTION:
                last_charge_update = current_time
                await self._coordinator.update_charge("batHighCap", self._serial, self._time)
            else:
                remaining_time = ALPHA_POST_REQUEST_RESTRICTION - (current_time - last_charge_update)
                minutes, seconds = divmod(remaining_time.total_seconds(), 60)
                _LOGGER.warning(
                    f"Has not been {ALPHA_POST_REQUEST_RESTRICTION.total_seconds() // 60} minutes since last charge config post call. Please wait {int(minutes)} minutes and {int(seconds)} seconds.")

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
