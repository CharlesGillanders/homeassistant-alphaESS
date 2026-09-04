"""Switch platform for AlphaESS integration."""
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
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

ATTR_LAST_CONFIRMED_STATE = "last_confirmed_state"


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

    On the periodic store these switches retain write intent because
    getTimeChargeBySn cannot report it reliably. A separate confirmed-state
    attribute is restored so an unapplied draft cannot become committed merely
    because Home Assistant restarted.
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

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Persist confirmed intent separately from the displayed draft."""
        value = self._coordinator.recorded_schedule_flag(
            self._serial, self._coordinator_key
        )
        state = "unknown" if value is None else ("on" if value else "off")
        return {ATTR_LAST_CONFIRMED_STATE: state}

    async def async_added_to_hass(self) -> None:
        """Hand the last published state back as the recorded answer."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        attributes = getattr(last_state, "attributes", {}) or {}
        if ATTR_LAST_CONFIRMED_STATE in attributes:
            restored = attributes[ATTR_LAST_CONFIRMED_STATE]
            source = ATTR_LAST_CONFIRMED_STATE
        else:
            # One-time compatibility for states written before the dedicated
            # attribute existed. New states always carry the attribute, even
            # when the confirmed answer is unknown.
            restored = last_state.state
            source = "legacy entity state"
        if restored not in ("on", "off"):
            return
        value = 1 if restored == "on" else 0
        if self._coordinator_key == "gridCharge":
            self._coordinator.set_periodic_enable_intent(
                self._serial, grid_charge=value,
            )
        else:
            self._coordinator.set_periodic_enable_intent(self._serial, ctr_dis=value)
        _LOGGER.debug(
            "Restored %s=%s for %s from %s",
            self._coordinator_key, value, self._serial, source,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on (enable) the setting."""
        await self._set_value(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off (disable) the setting."""
        await self._set_value(0)

    async def _set_value(self, value: int) -> None:
        """Stage the updated flag for the atomic Apply Schedule action."""
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

    @property
    def available(self) -> bool:
        """Switch controls require the cloud API and a usable schedule store."""
        if (
            not self.coordinator.last_update_success
            or self._serial not in self._coordinator.data
        ):
            return False
        return (
            self._coordinator.cloud_available
            and self._coordinator.can_modify_time_controls(self._serial)
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
