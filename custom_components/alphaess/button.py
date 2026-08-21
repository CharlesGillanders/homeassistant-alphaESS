import logging
import time as time_mod

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from alphaess.alphaess import AlphaESSApiError

from .const import (
    ALPHA_POST_REQUEST_RESTRICTION,
    CONF_DISABLE_NOTIFICATIONS,
    CONF_PARENT_INVERTER,
    CONF_SERIAL_NUMBER,
    INVERTER_SETTING_BLACKLIST,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from .coordinator import (
    AlphaESSDataUpdateCoordinator,
    SchedulePartialWriteError,
    ScheduleWriteUnknownError,
)
from .device import build_ev_charger_device_info, build_inverter_device_info
from .enums import AlphaESSNames
from .sensorlist import EV_DISCHARGE_AND_CHARGE_BUTTONS, SUPPORT_DISCHARGE_AND_CHARGE_BUTTON_DESCRIPTIONS

_LOGGER = logging.getLogger(__name__)

# Serialize button presses; the AlphaESS API rate-limits config writes.
PARALLEL_UPDATES = 1


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
    coordinator: AlphaESSDataUpdateCoordinator = entry.runtime_data

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
            inverter_device_info = build_inverter_device_info(serial, data)

            inverter_buttons: list[ButtonEntity] = []

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
            ev_charger = data.get(AlphaESSNames.evchargersn)
            ev_subentry_serials = {
                sub.data.get(CONF_SERIAL_NUMBER)
                for sub in entry.subentries.values()
                if sub.subentry_type == SUBENTRY_TYPE_EV_CHARGER
            }
            if ev_charger and ev_charger not in ev_subentry_serials:
                ev_device_info = build_ev_charger_device_info(data)
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
            ev_charger = data.get(AlphaESSNames.evchargersn)
            if not ev_charger:
                continue

            ev_device_info = build_ev_charger_device_info(data)
            ev_buttons: list[ButtonEntity] = []
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
        schedule_action_keys = {
            AlphaESSNames.ButtonApplySchedule,
            AlphaESSNames.ButtonDiscardSchedule,
            AlphaESSNames.ButtonRechargeConfig,
        }
        if not ev_charger and self._key not in schedule_action_keys:
            self._movement_state = self.name.split()[-1]

        self._icon = key_supported_states.icon
        self._entity_category = key_supported_states.entity_category
        self._config = config
        self._subentry = subentry
        self._ev_serial = ev_serial

        if self._key not in schedule_action_keys:
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

        async def _send_ev_command(action: str, direction: int) -> None:
            """Send the command and report what the charger said.

            No local state pre-check: the status it would test is only as fresh
            as the last poll, and when the status endpoint had failed there was
            no status at all, which used to block every command indefinitely.
            """
            self._movement_state = None
            try:
                await self._coordinator.control_ev(self._serial, self._ev_serial, direction)
            except Exception as err:
                api_rejection = isinstance(err, AlphaESSApiError)
                failure = "rejected" if api_rejection else "could not send"
                _LOGGER.error("EV %s command %s for %s: %s",
                              action.lower(), failure, self._serial, err)
                if not self._notifications_disabled:
                    await create_persistent_notification(
                        self.hass,
                        message=(
                            f"AlphaESS {failure} the EV charger {action.lower()} command for "
                            f"{self._serial}: {err}. Check EV Charger Status and try again."
                        ),
                        title=f"{self._serial} EV Charger {action} failed")
                if isinstance(err, HomeAssistantError):
                    raise
                # A raw AlphaESSApiError surfaces in the frontend as an
                # anonymous "Unknown error"; keep the API's own explanation.
                raise HomeAssistantError(
                    f"AlphaESS {failure} the EV charger {action.lower()} command "
                    f"for {self._serial}: {err}"
                ) from err

            _LOGGER.info("EV charger %s command sent for %s", action.lower(), self._serial)
            if not self._notifications_disabled:
                await create_persistent_notification(
                    self.hass,
                    message=f"EV charger {action.lower()} command sent for {self._serial}.",
                    title=f"{self._serial} EV Charger")

        if self._key == AlphaESSNames.stopcharging:
            await _send_ev_command("Stop", 0)
            return

        if self._key == AlphaESSNames.startcharging:
            await _send_ev_command("Start", 1)
            return

        if self._key == AlphaESSNames.ButtonApplySchedule:
            try:
                await self._coordinator.async_apply_schedule_draft(self._serial)
            except (SchedulePartialWriteError, ScheduleWriteUnknownError) as err:
                # Part of the change may already be live (backup mode writes
                # two stores), or the response was lost after the POST. Say
                # so; a silent log line hides an active schedule.
                outcome = (
                    "has an unknown outcome"
                    if isinstance(err, ScheduleWriteUnknownError)
                    else "was only partly applied"
                )
                _LOGGER.error(
                    "Apply Schedule %s for %s: %s", outcome, self._serial, err,
                )
                if not self._notifications_disabled:
                    await create_persistent_notification(
                        self.hass,
                        message=(
                            f"The schedule Apply for {self._serial} {outcome} "
                            f"and may already be active: {err}. "
                            "Check the AlphaESS app before retrying."
                        ),
                        title=f"{self._serial} Apply outcome uncertain",
                    )
                raise
            except Exception as err:
                _LOGGER.error("Apply Schedule failed for %s: %s", self._serial, err)
                if not self._notifications_disabled:
                    await create_persistent_notification(
                        self.hass,
                        message=(
                            f"AlphaESS could not apply the schedule changes for "
                            f"{self._serial}: {err}"
                        ),
                        title=f"{self._serial} Apply failed",
                    )
                raise
            if not self._notifications_disabled:
                newer_edits = self._coordinator.has_schedule_draft(self._serial)
                await create_persistent_notification(
                    self.hass,
                    message=(
                        f"AlphaESS accepted the submitted schedule changes for "
                        f"{self._serial}. Newer edits remain pending; select Apply again."
                        if newer_edits
                        else f"AlphaESS accepted the schedule changes for {self._serial}."
                    ),
                    title=(
                        f"{self._serial} Schedule Partly Applied"
                        if newer_edits
                        else f"{self._serial} Schedule Accepted"
                    ),
                )
            return

        if self._key == AlphaESSNames.ButtonDiscardSchedule:
            self._coordinator.discard_schedule_draft(self._serial)
            if not self._notifications_disabled:
                await create_persistent_notification(
                    self.hass,
                    message=f"Pending schedule changes discarded for {self._serial}.",
                    title=f"{self._serial} Schedule Restored",
                )
            return

        last_discharge_update = self._coordinator.last_discharge_update
        last_charge_update = self._coordinator.last_charge_update
        rate_limit = ALPHA_POST_REQUEST_RESTRICTION.total_seconds()

        async def handle_time_restriction(last_update_dict, update_fn, update_key, movement_direction):
            now = time_mod.monotonic()
            last_update = last_update_dict.get(self._serial)
            if last_update is None or now - last_update >= rate_limit:
                last_update_dict[self._serial] = now
                try:
                    await update_fn(update_key, self._serial, self._time)
                except (SchedulePartialWriteError, ScheduleWriteUnknownError) as err:
                    # Part of the change may already be live, or a lost
                    # response left it indeterminate. Keep the cooldown so an
                    # immediate retry cannot duplicate a potentially live
                    # charge/discharge command.
                    last_update_dict[self._serial] = time_mod.monotonic()
                    outcome = (
                        "has an unknown outcome"
                        if isinstance(err, ScheduleWriteUnknownError)
                        else "was only partly applied"
                    )
                    _LOGGER.error(
                        "%s command %s for %s: %s",
                        movement_direction, outcome, self._serial, err,
                    )
                    if not self._notifications_disabled:
                        await create_persistent_notification(
                            self.hass,
                            message=(
                                f"The {movement_direction.lower()} command for "
                                f"{self._serial} {outcome} and may already be "
                                f"active: {err}. Check the AlphaESS app before "
                                "retrying."
                            ),
                            title=f"{self._serial} {movement_direction} outcome uncertain",
                        )
                    raise
                except Exception as err:
                    # Nothing was applied, so don't make the user sit out the
                    # rate limit before they can try again.
                    last_update_dict[self._serial] = last_update
                    _LOGGER.error("%s command failed for %s: %s",
                                  movement_direction, self._serial, err)
                    if not self._notifications_disabled:
                        await create_persistent_notification(
                            self.hass,
                            message=f"AlphaESS could not apply the {movement_direction.lower()} command "
                                    f"for {self._serial}: {err}",
                            title=f"{self._serial} {movement_direction} failed")
                    raise
                last_update_dict[self._serial] = time_mod.monotonic()
                _LOGGER.info("Notifications disabled = %s for %s", self._notifications_disabled, self._serial)
                if not self._notifications_disabled:
                    _LOGGER.info("Sending notification for %s %s", self._serial, movement_direction)
                    await create_persistent_notification(self.hass,
                                                         message=f"{movement_direction} command sent successfully for {self._serial}.",
                                                         title=f"{self._serial} {movement_direction}")
            else:
                remaining = rate_limit - (now - last_update)
                minutes, seconds = divmod(remaining, 60)
                wait_message = (
                    f"Please wait {int(minutes)} minutes and {int(seconds)} seconds."
                )

                if not self._notifications_disabled:
                    await create_persistent_notification(self.hass,
                                                         message=wait_message,
                                                         title=f"{self._serial} cannot call {movement_direction}")
                # No command was sent. Returning silently here reads as
                # success to the frontend and to automations.
                raise HomeAssistantError(
                    f"The {movement_direction.lower()} command for {self._serial} "
                    f"is rate limited. {wait_message}"
                )

        now = time_mod.monotonic()

        if self._key == AlphaESSNames.ButtonRechargeConfig:
            if (last_charge_update.get(self._serial) is None or now - last_charge_update[
                self._serial] >= rate_limit) and \
                    (last_discharge_update.get(self._serial) is None or now - last_discharge_update[
                        self._serial] >= rate_limit):
                previous_charge = last_charge_update.get(self._serial)
                previous_discharge = last_discharge_update.get(self._serial)
                last_discharge_update[self._serial] = last_charge_update[self._serial] = now
                try:
                    await self._coordinator.reset_config(self._serial)
                except (SchedulePartialWriteError, ScheduleWriteUnknownError) as err:
                    completed = time_mod.monotonic()
                    last_discharge_update[self._serial] = completed
                    last_charge_update[self._serial] = completed
                    outcome = (
                        "has an unknown outcome"
                        if isinstance(err, ScheduleWriteUnknownError)
                        else "was only partly applied"
                    )
                    _LOGGER.error("Reset %s for %s: %s", outcome, self._serial, err)
                    if not self._notifications_disabled:
                        await create_persistent_notification(
                            self.hass,
                            message=(
                                f"The reset for {self._serial} {outcome}: {err}. "
                                "Some settings may already have changed; check the "
                                "AlphaESS app before retrying."
                            ),
                            title=f"{self._serial} Reset outcome uncertain",
                        )
                    raise
                except Exception as err:
                    last_charge_update[self._serial] = previous_charge
                    last_discharge_update[self._serial] = previous_discharge
                    _LOGGER.error("Reset failed for %s: %s", self._serial, err)
                    if not self._notifications_disabled:
                        await create_persistent_notification(
                            self.hass,
                            message=f"AlphaESS could not fully reset {self._serial}: {err}",
                            title=f"{self._serial} Reset failed")
                    raise
                completed = time_mod.monotonic()
                last_discharge_update[self._serial] = completed
                last_charge_update[self._serial] = completed
                if not self._notifications_disabled:
                    await create_persistent_notification(self.hass,
                                                         message=f"Charge and discharge configuration reset for {self._serial}.",
                                                         title=f"{self._serial} Reset")
            else:
                # Reset button is throttled - show the wait and fail the press
                last_update = last_charge_update.get(self._serial) or last_discharge_update.get(self._serial)
                wait_message = "Please wait before resetting again."
                if last_update:
                    remaining = rate_limit - (now - last_update)
                    minutes, seconds = divmod(remaining, 60)
                    wait_message = (
                        f"Please wait {int(minutes)} minutes and {int(seconds)} seconds."
                    )
                    if not self._notifications_disabled:
                        await create_persistent_notification(self.hass,
                                                             message=wait_message,
                                                             title=f"{self._serial} cannot reset yet")
                raise HomeAssistantError(
                    f"The reset for {self._serial} is rate limited. {wait_message}"
                )
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
        serial_data = self._coordinator.data.get(self._serial)
        if serial_data is None:
            return False
        if self._ev_serial is not None and serial_data.get(
            AlphaESSNames.evchargersn
        ) != self._ev_serial:
            return False
        if self._key in (
            AlphaESSNames.ButtonApplySchedule,
            AlphaESSNames.ButtonDiscardSchedule,
        ):
            # In a self-consumption working mode nothing time-based can be
            # written, so a stranded draft cannot be applied (or discarded)
            # until the app returns the inverter to a time-based mode.
            return (
                self._coordinator.cloud_available
                and self._coordinator.has_schedule_draft(self._serial)
                and not self._coordinator.is_schedule_apply_in_progress(self._serial)
                and self._coordinator.is_time_based_control_active(self._serial)
                is not False
            )
        if self._key == AlphaESSNames.ButtonRechargeConfig:
            # The reset clears only the legacy backup stores; on a system the
            # periodic schedule governs it could only fail, so report it as
            # unavailable there instead. In a self-consumption mode it would
            # silently re-enable time-based control, so it locks there too.
            return (
                self._coordinator.cloud_available
                and self._coordinator.can_reset_schedule(self._serial)
                and self._coordinator.is_time_based_control_active(self._serial)
                is not False
            )
        if getattr(self, "_movement_state", None) in ("Charge", "Discharge"):
            # Duration buttons need a usable schedule store and active
            # time-based control; in a self-consumption mode a press would
            # silently re-enable timed control.
            return (
                self._coordinator.cloud_available
                and self._coordinator.can_modify_time_controls(self._serial)
            )
        return self._coordinator.cloud_available

    @property
    def unique_id(self):
        return f"{self._config.entry_id}_{self._serial} - {self._name}"

    @property
    def entity_category(self):
        return self._entity_category

    @property
    def name(self):
        return f"{self._name}"

    @property
    def suggested_object_id(self):
        """Return suggested object id."""
        return f"{self._serial} {self._name}"

    @property
    def icon(self):
        return self._icon
