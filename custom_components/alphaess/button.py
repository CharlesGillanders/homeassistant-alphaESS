from datetime import datetime, timedelta
from typing import List
import logging
from homeassistant.components.button import ButtonEntity, ButtonDeviceClass
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AlphaESSDataUpdateCoordinator
from .const import DOMAIN, ALPHA_POST_REQUEST_RESTRICTION, INVERTER_SETTING_BLACKLIST
from .sensorlist import SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS, EV_DISCHARGE_AND_CHARGE_BUTTONS
from .enums import AlphaESSNames

_LOGGER: logging.Logger = logging.getLogger(__package__)

last_discharge_update = {}
last_charge_update = {}


async def create_persistent_notification(hass, message, title="Error"):
    """Create a persistent notification in the Home Assistant frontend."""
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": title,
            "message": message,
        },
        blocking=True
    )


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: AlphaESSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    button_entities: List[ButtonEntity] = []

    full_button_supported_states = {
        description.key: description for description in SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS
    }

    ev_charging_supported_states = {
        description.key: description for description in EV_DISCHARGE_AND_CHARGE_BUTTONS
    }

    for serial, data in coordinator.data.items():
        model = data.get("Model")
        has_local_ip_data = 'Local IP' in data
        if model not in INVERTER_SETTING_BLACKLIST:
            for description in full_button_supported_states:
                button_entities.append(
                    AlphaESSBatteryButton(coordinator, entry, serial, full_button_supported_states[description],
                                          has_local_connection=has_local_ip_data))

        ev_charger = data.get("EV Charger S/N")
        if ev_charger:
            for description in ev_charging_supported_states:
                button_entities.append(
                    AlphaESSBatteryButton(
                        coordinator, entry, serial, ev_charging_supported_states[description], True,
                        has_local_connection=has_local_ip_data
                    )
                )

    async_add_entities(button_entities)


class AlphaESSBatteryButton(CoordinatorEntity, ButtonEntity):

    def __init__(self, coordinator: AlphaESSDataUpdateCoordinator, config, serial, key_supported_states, ev_charger=False, has_local_connection=False):
        super().__init__(coordinator)
        self._serial = serial
        self._coordinator: AlphaESSDataUpdateCoordinator = coordinator
        self._name = key_supported_states.name
        self._key = key_supported_states.key
        if not ev_charger:
            self._movement_state = self.name.split()[-1]

        self._icon = key_supported_states.icon
        self._entity_category = key_supported_states.entity_category
        self._config = config
        self._has_local_connection = has_local_connection

        self._time = None

        if self._key != AlphaESSNames.ButtonRechargeConfig:
            if not ev_charger:
                try:
                    self._time = int(self._name.split()[0])
                except (ValueError, IndexError):
                    _LOGGER.warning(f"Could not extract time from button name: {self._name}")
                    self._time = None

        for invertor in coordinator.data:
            serial = invertor.upper()
            if ev_charger:
                self._ev_serial = coordinator.data[invertor]["EV Charger S/N"]
                self._attr_device_info = DeviceInfo(
                    entry_type=DeviceEntryType.SERVICE,
                    identifiers={(DOMAIN, coordinator.data[invertor]["EV Charger S/N"])},
                    manufacturer="AlphaESS",
                    model=coordinator.data[invertor]["EV Charger Model"],
                    model_id=coordinator.data[invertor]["EV Charger S/N"],
                    name=f"Alpha ESS Charger : {coordinator.data[invertor]["EV Charger S/N"]}",
                )
            elif "Local IP" in coordinator.data[invertor] and coordinator.data[invertor].get('Local IP') != '0':
                self._attr_device_info = DeviceInfo(
                    entry_type=DeviceEntryType.SERVICE,
                    identifiers={(DOMAIN, serial)},
                    serial_number=coordinator.data[invertor]["Device Serial Number"],
                    sw_version=coordinator.data[invertor]["Software Version"],
                    hw_version=coordinator.data[invertor]["Hardware Version"],
                    manufacturer="AlphaESS",
                    model=coordinator.data[invertor]["Model"],
                    model_id=self._serial,
                    name=f"Alpha ESS Energy Statistics : {serial}",
                    configuration_url=f"http://{coordinator.data[invertor]["Local IP"]}"
                )
            elif self._serial == serial:
                self._attr_device_info = DeviceInfo(
                    entry_type=DeviceEntryType.SERVICE,
                    identifiers={(DOMAIN, serial)},
                    manufacturer="AlphaESS",
                    model=coordinator.data[invertor]["Model"],
                    model_id=self._serial,
                    name=f"Alpha ESS Energy Statistics : {serial}",
                )

    async def async_press(self) -> None:

        if self._key == AlphaESSNames.stopcharging:
            _LOGGER.info("Stopped charging")
            self._movement_state = None
            await self._coordinator.control_ev(self._serial, self._ev_serial, 0)

        if self._key == AlphaESSNames.startcharging:
            _LOGGER.info("started charging")
            self._movement_state = None
            await self._coordinator.control_ev(self._serial, self._ev_serial, 1)

        global last_discharge_update
        global last_charge_update

        async def handle_time_restriction(last_update_dict, update_fn, update_key, movement_direction):
            local_current_time = datetime.now()
            last_update = last_update_dict.get(self._serial)
            if last_update is None or local_current_time - last_update >= ALPHA_POST_REQUEST_RESTRICTION:
                last_update_dict[self._serial] = local_current_time
                await update_fn(update_key, self._serial, self._time)
            else:
                time_remaining = ALPHA_POST_REQUEST_RESTRICTION - (local_current_time - last_update)
                mins, secs = divmod(time_remaining.total_seconds(), 60)
                await create_persistent_notification(
                    self.hass,
                    message=f"Please wait {int(mins)} minutes and {int(secs)} seconds.",
                    title=f"{self._serial} cannot call {movement_direction}"
                )
            return last_update_dict

        current_time = datetime.now()

        if self._key == AlphaESSNames.ButtonRechargeConfig:
            if (last_charge_update.get(self._serial) is None or current_time - last_charge_update[
                self._serial] >= ALPHA_POST_REQUEST_RESTRICTION) and \
                    (last_discharge_update.get(self._serial) is None or current_time - last_discharge_update[
                        self._serial] >= ALPHA_POST_REQUEST_RESTRICTION):
                last_discharge_update[self._serial] = last_charge_update[self._serial] = current_time
                await self._coordinator.reset_config(self._serial)
            else:
                charge_remaining = timedelta(0)
                discharge_remaining = timedelta(0)

                if last_charge_update.get(self._serial) is not None:
                    charge_remaining = ALPHA_POST_REQUEST_RESTRICTION - (
                            current_time - last_charge_update[self._serial])

                if last_discharge_update.get(self._serial) is not None:
                    discharge_remaining = ALPHA_POST_REQUEST_RESTRICTION - (
                            current_time - last_discharge_update[self._serial])

                remaining_time = max(charge_remaining, discharge_remaining)
                minutes, seconds = divmod(remaining_time.total_seconds(), 60)

                await create_persistent_notification(self.hass,
                                                     message=f"Please wait {int(minutes)} minutes and {int(seconds)} seconds.",
                                                     title=f"{self._serial} cannot reset configuration")
        elif self._movement_state == "Discharge":
            last_discharge_update = await handle_time_restriction(last_discharge_update,
                                                                  self._coordinator.update_discharge, "batUseCap",
                                                                  self._movement_state)
        elif self._movement_state == "Charge":
            last_charge_update = await handle_time_restriction(last_charge_update, self._coordinator.update_charge,
                                                               "batHighCap", self._movement_state)

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
        return f"{self._name}"

    @property
    def icon(self):
        return self._icon
