"""Switch platform for AlphaESS integration."""
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_SERIAL_NUMBER,
    INVERTER_SETTING_BLACKLIST,
    SUBENTRY_TYPE_INVERTER,
)
from .coordinator import AlphaESSDataUpdateCoordinator
from .device import build_inverter_device_info
from .sensorlist import CHARGE_DISCHARGE_SWITCHES

_LOGGER = logging.getLogger(__name__)

# Serialize switch writes; the AlphaESS API rate-limits config writes.
PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up AlphaESS switch entities."""
    coordinator: AlphaESSDataUpdateCoordinator = entry.runtime_data

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
        inverter_device_info = build_inverter_device_info(serial, data)

        switch_entities: list[SwitchEntity] = []

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


class AlphaSwitch(CoordinatorEntity, RestoreEntity, SwitchEntity):
    """Switch entity for grid charge / discharge time control.

    On the periodic store these two switches are the only record of whether
    scheduled charging and discharging should be on: getTimeChargeBySn answers
    0 for both however the inverter is set, so the state published here is
    restored on startup and handed back to the coordinator, which needs it to
    build any write at all.
    """

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
        self._optimistic_state: bool | None = None

        if device_info:
            self._attr_device_info = device_info

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        if self._optimistic_state is not None:
            return self._optimistic_state
        data = self._coordinator.data.get(self._serial, {})
        value = data.get(self._coordinator_key)
        if value is None:
            return None
        return int(value) == 1

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when coordinator provides fresh data."""
        self._optimistic_state = None
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Hand the last published state back as the recorded answer."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state not in ("on", "off"):
            return
        value = 1 if last_state.state == "on" else 0
        if self._coordinator_key == "gridCharge":
            self._coordinator.set_periodic_enable_intent(
                self._serial, grid_charge=value,
            )
        else:
            self._coordinator.set_periodic_enable_intent(self._serial, ctr_dis=value)
        _LOGGER.debug(
            "Restored %s=%s for %s from the last published state",
            self._coordinator_key, value, self._serial,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on (enable) the setting."""
        await self._set_value(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off (disable) the setting."""
        await self._set_value(0)

    async def _set_value(self, value: int) -> None:
        """Stage the updated flag for the atomic Apply Schedule action."""
        if self._coordinator.can_unlock_time_controls(self._serial):
            await self._async_write_unlock(value)
            return

        previous_state = self._optimistic_state
        self._optimistic_state = bool(value)
        self.async_write_ha_state()

        try:
            if self._coordinator_key == "gridCharge":
                self._coordinator.stage_schedule_change(
                    self._serial, charge={"gridCharge": value},
                )
            elif self._coordinator_key == "ctrDis":
                self._coordinator.stage_schedule_change(
                    self._serial, discharge={"ctrDis": value},
                )
        except Exception:
            # Nothing was written, so don't leave the UI claiming otherwise.
            _LOGGER.exception("Failed to update %s for %s, reverting",
                              self._coordinator_key, self._serial)
            self._optimistic_state = previous_state
            self.async_write_ha_state()
            raise

        # The coordinator overlays the draft on subsequent polls so the value
        # stays visible until it is applied or discarded.

    async def _async_write_unlock(self, value: int) -> None:
        """Send an enable immediately while the schedule surface is locked.

        Nothing else can be staged or applied in that state, so a draft here
        would only wait behind an Apply button that is itself unavailable.
        """
        if value != 1:
            raise HomeAssistantError(
                f"Both timers for {self._serial} are already off. Switching one "
                "back on is the only schedule change Home Assistant can make "
                "while the inverter reports no timed control; the working mode "
                "can only be changed in the AlphaESS app"
            )

        previous_state = self._optimistic_state
        self._optimistic_state = True
        self.async_write_ha_state()
        try:
            if self._coordinator_key == "gridCharge":
                await self._coordinator.async_write_charge_config(
                    self._serial, grid_charge=1,
                )
            else:
                await self._coordinator.async_write_discharge_config(
                    self._serial, ctr_dis=1,
                )
        except Exception:
            _LOGGER.exception(
                "Failed to re-enable %s for %s, reverting",
                self._coordinator_key, self._serial,
            )
            self._optimistic_state = previous_state
            self.async_write_ha_state()
            raise

    @property
    def available(self) -> bool:
        """Switch controls require the cloud API, a usable schedule store,
        and an active time-based working mode.

        In a self-consumption mode the enable flags are accepted but ignored
        by the inverter, and writing one would make the mode inference lie —
        the working mode can only be changed in the AlphaESS app, so the
        switches lock together with the rest of the schedule surface.
        """
        if (
            not self.coordinator.last_update_success
            or self._serial not in self._coordinator.data
        ):
            return False
        return self._coordinator.cloud_available and (
            self._coordinator.can_modify_time_controls(self._serial)
            # The one exception to the lockout: with both timers off, these
            # switches are the only way back to a timed mode from here.
            or self._coordinator.can_unlock_time_controls(self._serial)
        )

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
