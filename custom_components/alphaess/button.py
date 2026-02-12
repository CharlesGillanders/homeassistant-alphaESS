import time as time_mod
from typing import List
import logging
from homeassistant.components.button import ButtonEntity, ButtonDeviceClass
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ALPHA_POST_REQUEST_RESTRICTION, INVERTER_SETTING_BLACKLIST, CONF_SERIAL_NUMBER, \
    SUBENTRY_TYPE_INVERTER, SUBENTRY_TYPE_EV_CHARGER, CONF_PARENT_INVERTER, CONF_DISABLE_NOTIFICATIONS
from .coordinator import AlphaESSDataUpdateCoordinator
from .sensorlist import SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS, EV_DISCHARGE_AND_CHARGE_BUTTONS
from .enums import AlphaESSNames
from .sensor import _build_inverter_device_info, _build_ev_charger_device_info

_LOGGER: logging.Logger = logging.getLogger(__package__)


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

    full_button_supported_states = {
        description.key: description for description in SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS
    }

    ev_charging_supported_states = {
        description.key: description for description in EV_DISCHARGE_AND_CHARGE_BUTTONS
    }

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_INVERTER:
            serial = subentry.data.get(CONF_SERIAL_NUMBER)
            if not serial or serial not in coordinator.data:
                continue

            data = coordinator.data[serial]
            model = data.get("Model")
            inverter_device_info = _build_inverter_device_info(coordinator, serial, data)

            inverter_buttons: List[ButtonEntity] = []

            if model not in INVERTER_SETTING_BLACKLIST:
                for description in full_button_supported_states:
                    inverter_buttons.append(
                        AlphaESSBatteryButton(
                            coordinator, entry, serial,
                            full_button_supported_states[description],
                            device_info=inverter_device_info,
                            subentry=subentry,
                        )
                    )

            # Auto-discovered EV charger buttons (no dedicated EV subentry)
            ev_charger = data.get("EV Charger S/N")
            ev_subentry_serials = {
                sub.data.get(CONF_SERIAL_NUMBER)
                for sub in entry.subentries.values()
                if sub.subentry_type == SUBENTRY_TYPE_EV_CHARGER
            }
            if ev_charger and ev_charger not in ev_subentry_serials:
                ev_device_info = _build_ev_charger_device_info(coordinator, data)
                for description in ev_charging_supported_states:
                    inverter_buttons.append(
                        AlphaESSBatteryButton(
                            coordinator, entry, serial,
                            ev_charging_supported_states[description],
                            ev_charger=True,
                            ev_serial=ev_charger,
                            device_info=ev_device_info,
                            subentry=subentry,
                        )
                    )

            if inverter_buttons:
                async_add_entities(
                    inverter_buttons,
                    config_subentry_id=subentry.subentry_id,
                )

        elif subentry.subentry_type == SUBENTRY_TYPE_EV_CHARGER:
            parent_serial = subentry.data.get(CONF_PARENT_INVERTER)
            if not parent_serial or parent_serial not in coordinator.data:
                continue

            data = coordinator.data[parent_serial]
            ev_charger = data.get("EV Charger S/N")
            if not ev_charger:
                continue

            ev_device_info = _build_ev_charger_device_info(coordinator, data)
            ev_buttons: List[ButtonEntity] = []
            for description in ev_charging_supported_states:
                ev_buttons.append(
                    AlphaESSBatteryButton(
                        coordinator, entry, parent_serial,
                        ev_charging_supported_states[description],
                        ev_charger=True,
                        ev_serial=ev_charger,
                        device_info=ev_device_info,
                    )
                )

            if ev_buttons:
                async_add_entities(
                    ev_buttons,
                    config_subentry_id=subentry.subentry_id,
                )


class AlphaESSBatteryButton(CoordinatorEntity, ButtonEntity):

    def __init__(self, coordinator, config, serial, key_supported_states, ev_charger=False, ev_serial=None, device_info=None, subentry=None):
        super().__init__(coordinator)
        self._serial = serial
        self._coordinator = coordinator
        self._name = key_supported_states.name
        self._key = key_supported_states.key
        if not ev_charger:
            self._movement_state = self.name.split()[-1]

        self._icon = key_supported_states.icon
        self._entity_category = key_supported_states.entity_category
        self._config = config
        self._subentry = subentry
        self._ev_serial = ev_serial

        if self._key != AlphaESSNames.ButtonRechargeConfig:
            if not ev_charger:
                self._time = int(self._name.split()[0])

        if device_info:
            self._attr_device_info = device_info

    @property
    def _notifications_disabled(self) -> bool:
        """Check if notifications are disabled for this inverter's subentry."""
        # Look up live subentry data from config entry (not the stale snapshot)
        if self._subentry is not None:
            entry = self.hass.config_entries.async_get_entry(self._config.entry_id)
            if entry:
                live_subentry = entry.subentries.get(self._subentry.subentry_id)
                if live_subentry:
                    result = live_subentry.data.get(CONF_DISABLE_NOTIFICATIONS, True)
                    _LOGGER.debug(
                        "Notifications disabled check for %s: subentry=%s, value=%s",
                        self._serial, self._subentry.subentry_id, result,
                    )
                    return result
                else:
                    _LOGGER.warning("Could not find live subentry %s", self._subentry.subentry_id)
            else:
                _LOGGER.warning("Could not find config entry %s", self._config.entry_id)
        else:
            _LOGGER.debug("No subentry set for button %s - notifications disabled", self._name)
        return True

    async def async_press(self) -> None:

        if self._key == AlphaESSNames.stopcharging:
            _LOGGER.info("Stopped charging")
            self._movement_state = None
            await self._coordinator.control_ev(self._serial, self._ev_serial, 0)
            if not self._notifications_disabled:
                await create_persistent_notification(self.hass,
                                                     message=f"EV charger stop command sent for {self._serial}.",
                                                     title=f"{self._serial} EV Charger")
            return

        if self._key == AlphaESSNames.startcharging:
            _LOGGER.info("started charging")
            self._movement_state = None
            await self._coordinator.control_ev(self._serial, self._ev_serial, 1)
            if not self._notifications_disabled:
                await create_persistent_notification(self.hass,
                                                     message=f"EV charger start command sent for {self._serial}.",
                                                     title=f"{self._serial} EV Charger")
            return

        last_discharge_update = self._coordinator.last_discharge_update
        last_charge_update = self._coordinator.last_charge_update
        rate_limit = ALPHA_POST_REQUEST_RESTRICTION.total_seconds()

        async def handle_time_restriction(last_update_dict, update_fn, update_key, movement_direction):
            now = time_mod.monotonic()
            last_update = last_update_dict.get(self._serial)
            if last_update is None or now - last_update >= rate_limit:
                last_update_dict[self._serial] = now
                await update_fn(update_key, self._serial, self._time)
                _LOGGER.info("Notifications disabled = %s for %s", self._notifications_disabled, self._serial)
                if not self._notifications_disabled:
                    _LOGGER.info("Sending notification for %s %s", self._serial, movement_direction)
                    await create_persistent_notification(self.hass,
                                                         message=f"{movement_direction} command sent successfully for {self._serial}.",
                                                         title=f"{self._serial} {movement_direction}")
            else:
                remaining = rate_limit - (now - last_update)
                minutes, seconds = divmod(remaining, 60)

                if not self._notifications_disabled:
                    await create_persistent_notification(self.hass,
                                                         message=f"Please wait {int(minutes)} minutes and {int(seconds)} seconds.",
                                                         title=f"{self._serial} cannot call {movement_direction}")

        now = time_mod.monotonic()

        if self._key == AlphaESSNames.ButtonRechargeConfig:
            if (last_charge_update.get(self._serial) is None or now - last_charge_update[
                self._serial] >= rate_limit) and \
                    (last_discharge_update.get(self._serial) is None or now - last_discharge_update[
                        self._serial] >= rate_limit):
                last_discharge_update[self._serial] = last_charge_update[self._serial] = now
                await self._coordinator.reset_config(self._serial)
                if not self._notifications_disabled:
                    await create_persistent_notification(self.hass,
                                                         message=f"Charge and discharge configuration reset for {self._serial}.",
                                                         title=f"{self._serial} Reset")
            else:
                # Reset button is throttled - just show wait message
                last_update = last_charge_update.get(self._serial) or last_discharge_update.get(self._serial)
                if last_update:
                    remaining = rate_limit - (now - last_update)
                    minutes, seconds = divmod(remaining, 60)
                    if not self._notifications_disabled:
                        await create_persistent_notification(self.hass,
                                                             message=f"Please wait {int(minutes)} minutes and {int(seconds)} seconds.",
                                                             title=f"{self._serial} cannot reset yet")
        elif self._movement_state == "Discharge":
            await handle_time_restriction(last_discharge_update,
                                          self._coordinator.update_discharge, "batUseCap",
                                          self._movement_state)
        elif self._movement_state == "Charge":
            await handle_time_restriction(last_charge_update, self._coordinator.update_charge,
                                          "batHighCap", self._movement_state)

    @property
    def available(self) -> bool:
        """Buttons require cloud API to function."""
        if not self.coordinator.last_update_success:
            return False
        return self._coordinator.cloud_available

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
