import logging

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from alphaess.alphaess import AlphaESSApiError

from .const import (
    CONF_PARENT_INVERTER,
    CONF_SERIAL_NUMBER,
    INVERTER_SETTING_BLACKLIST,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from .coordinator import AlphaESSDataUpdateCoordinator, describe_api_error
from .device import build_ev_charger_device_info, build_inverter_device_info
from .enums import AlphaESSNames
from .sensorlist import DISCHARGE_AND_CHARGE_NUMBERS, EV_CHARGER_NUMBERS

_LOGGER = logging.getLogger(__name__)

_SCHEDULE_NUMBER_FIELDS = {
    AlphaESSNames.batHighCap: ("charge", "batHighCap"),
    AlphaESSNames.batUseCap: ("discharge", "batUseCap"),
    AlphaESSNames.ChargePower1: ("charge", "chargePower1"),
    AlphaESSNames.ChargePower2: ("charge", "chargePower2"),
    AlphaESSNames.DischargePower1: ("discharge", "chargePower1"),
    AlphaESSNames.DischargePower2: ("discharge", "chargePower2"),
}
_PERIODIC_POWER_KEYS = {
    AlphaESSNames.ChargePower1,
    AlphaESSNames.ChargePower2,
    AlphaESSNames.DischargePower1,
    AlphaESSNames.DischargePower2,
}
# Stable store keys for the coordinator's number settings. Deliberately NOT
# the display names, which are user-facing and allowed to change.
_NUMBER_SETTING_KEYS = {
    AlphaESSNames.batHighCap: "batHighCap",
    AlphaESSNames.batUseCap: "batUseCap",
}

# Serialize value writes; the AlphaESS API rate-limits config writes.
PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: AlphaESSDataUpdateCoordinator = entry.runtime_data

    full_number_supported_states = {
        description.key: description for description in DISCHARGE_AND_CHARGE_NUMBERS
    }

    ev_number_supported_states = {
        description.key: description for description in EV_CHARGER_NUMBERS
    }

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_INVERTER:
            serial = subentry.data.get(CONF_SERIAL_NUMBER)
            if not serial or serial not in coordinator.data:
                continue

            data = coordinator.data[serial]
            model = data.get("Model")
            inverter_device_info = build_inverter_device_info(serial, data)

            number_entities: list[NumberEntity] = []

            if model not in INVERTER_SETTING_BLACKLIST:
                for description in full_number_supported_states:
                    number_entities.append(
                        AlphaNumber(
                            coordinator, serial, entry,
                            full_number_supported_states[description],
                            device_info=inverter_device_info,
                        )
                    )

            # Auto-discovered EV charger numbers (no dedicated EV subentry)
            ev_charger = data.get(AlphaESSNames.evchargersn)
            ev_subentry_serials = {
                sub.data.get(CONF_SERIAL_NUMBER)
                for sub in entry.subentries.values()
                if sub.subentry_type == SUBENTRY_TYPE_EV_CHARGER
            }
            if ev_charger and ev_charger not in ev_subentry_serials:
                ev_device_info = build_ev_charger_device_info(data)
                for description in ev_number_supported_states:
                    number_entities.append(
                        AlphaEVNumber(
                            coordinator, serial, entry,
                            ev_number_supported_states[description],
                            ev_serial=ev_charger,
                            device_info=ev_device_info,
                        )
                    )

            if number_entities:
                async_add_entities(
                    number_entities,
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
            ev_entities: list[NumberEntity] = []
            for description in ev_number_supported_states:
                ev_entities.append(
                    AlphaEVNumber(
                        coordinator, parent_serial, entry,
                        ev_number_supported_states[description],
                        ev_serial=ev_charger,
                        device_info=ev_device_info,
                    )
                )

            if ev_entities:
                async_add_entities(
                    ev_entities,
                    config_subentry_id=subentry.subentry_id,
                )


class AlphaNumber(CoordinatorEntity, RestoreNumber):
    """Battery use capacity number entity."""

    def __init__(self, coordinator, serial, config, full_number_supported_states, device_info=None):
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._serial = serial
        self._config = config
        self.key = full_number_supported_states.key
        self._entity_category = full_number_supported_states.entity_category
        self._native_unit_of_measurement = full_number_supported_states.native_unit_of_measurement
        self._icon = full_number_supported_states.icon
        self._name = full_number_supported_states.name
        self._attr_native_min_value = full_number_supported_states.native_min_value
        self._attr_native_max_value = full_number_supported_states.native_max_value
        self._attr_native_step = full_number_supported_states.native_step

        if self.key is AlphaESSNames.batHighCap:
            self._def_initial_value = float(90)
        elif self.key is AlphaESSNames.batUseCap:
            self._def_initial_value = float(10)
        else:
            self._def_initial_value = None

        if device_info:
            self._attr_device_info = device_info

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        coordinator_value = self._coordinator.data.get(self._serial, {}).get(self.key)
        if coordinator_value is not None:
            self._attr_native_value = float(coordinator_value)
            self.save_value(self._attr_native_value)
            return

        # Cutoff values existed as local RestoreNumber settings before they
        # became schedule drafts, so retain that fallback for legacy-only
        # accounts. Power is never restored: presenting a stale rate as remote
        # state could make a later full replacement unsafe.
        if self._def_initial_value is None:
            self._attr_native_value = None
            return
        try:
            last_state = await self.async_get_last_number_data()
            last_value = last_state.native_value
            self._attr_native_value = (
                float(last_value) if last_value is not None else self._def_initial_value
            )
        except (AttributeError, TypeError, ValueError):
            self._attr_native_value = self._def_initial_value
            _LOGGER.info(
                "No saved state found for %s. Using initial value: %s",
                self._name, self._def_initial_value,
            )
        self.save_value(self._attr_native_value)
        self.async_write_ha_state()

    def save_value(self, value):
        """Persist the value on the coordinator for charge/discharge commands."""
        setting_key = _NUMBER_SETTING_KEYS.get(self.key)
        if setting_key is None:
            return
        self._coordinator.set_number_setting(self._serial, setting_key, value)
        _LOGGER.debug(
            "Saved %s=%s for %s on coordinator", setting_key, value, self._serial,
        )

    async def async_set_native_value(self, value: float) -> None:
        previous_value = self._attr_native_value
        self._attr_native_value = value
        self.save_value(value)
        self.async_write_ha_state()

        # Stage with the other schedule fields. The upstream endpoints replace
        # the whole resource, so writing this one field immediately could reset
        # times the user is still editing.
        try:
            side, field = _SCHEDULE_NUMBER_FIELDS[self.key]
            self._coordinator.stage_schedule_change(
                self._serial, **{side: {field: value}},
            )
        except Exception:
            # Nothing was written, so put the stored value back too -- it is
            # what the charge/discharge buttons will send next time. With no
            # previous value, drop the key entirely rather than storing None,
            # which would shadow the default those callers fall back to.
            _LOGGER.exception("Failed to set %s for %s, reverting", self._name, self._serial)
            self._attr_native_value = previous_value
            if previous_value is None:
                setting_key = _NUMBER_SETTING_KEYS.get(self.key)
                if setting_key is not None:
                    self._coordinator.clear_number_setting(self._serial, setting_key)
            else:
                self.save_value(previous_value)
            self.async_write_ha_state()
            raise

    def _handle_coordinator_update(self) -> None:
        """Follow the committed or draft schedule, including Discard."""
        data = self._coordinator.data.get(self._serial, {})
        if self.key in data:
            value = data[self.key]
            self._attr_native_value = float(value) if value is not None else None
            if value is not None:
                self.save_value(self._attr_native_value)
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Number controls require the cloud API and a usable schedule store.

        Per-period powers exist only in the periodic (primary) schedule API;
        in legacy backup mode those entities stay unavailable.
        """
        if (
            not self.coordinator.last_update_success
            or self._serial not in self._coordinator.data
        ):
            return False
        if self.key in _PERIODIC_POWER_KEYS:
            return (
                self._coordinator.cloud_available
                and self._coordinator.is_periodic_schedule_readable(self._serial)
                and self._coordinator.is_time_based_control_active(self._serial)
                is not False
            )
        return (
            self._coordinator.cloud_available
            and self._coordinator.can_modify_time_controls(self._serial)
        )

    @property
    def native_value(self):
        return self._attr_native_value

    @property
    def name(self):
        return f"{self._name}"

    @property
    def suggested_object_id(self):
        """Return suggested object id."""
        return f"{self._serial} {self._name}"

    @property
    def mode(self) -> NumberMode:
        return NumberMode.BOX

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


class AlphaEVNumber(CoordinatorEntity, NumberEntity):
    """EV charger current setting number entity."""

    def __init__(self, coordinator, serial, config, description, ev_serial=None, device_info=None):
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._serial = serial
        self._ev_serial = ev_serial
        self._config = config
        self._description = description
        self._name = description.name
        self._icon = description.icon
        self._entity_category = description.entity_category
        self._attr_native_min_value = description.native_min_value
        self._attr_native_max_value = description.native_max_value
        self._attr_native_step = description.native_step
        self._attr_mode = description.mode

        if device_info:
            self._attr_device_info = device_info

    @property
    def native_value(self) -> float | None:
        """Return the current value from coordinator data."""
        data = self._coordinator.data.get(self._serial, {})
        value = data.get(AlphaESSNames.evcurrentsetting)
        if value is not None:
            return float(value)
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set EV charger current via API."""
        try:
            await self._coordinator.set_ev_charger_current(self._serial, int(value))
        except AlphaESSApiError as err:
            # A raw library exception reaches the frontend as "Unknown error";
            # keep the API's return code and explanation visible.
            raise HomeAssistantError(
                f"AlphaESS rejected the EV charger current for {self._serial}: "
                f"{describe_api_error(err)}"
            ) from err
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                f"The EV charger current for {self._serial} could not be sent: {err}"
            ) from err

    @property
    def available(self) -> bool:
        """EV charger controls require cloud API to function."""
        serial_data = self._coordinator.data.get(self._serial)
        if not self.coordinator.last_update_success or serial_data is None:
            return False
        return (
            self._coordinator.cloud_available
            and serial_data.get(AlphaESSNames.evchargersn) == self._ev_serial
        )

    @property
    def name(self):
        return f"{self._name}"

    @property
    def suggested_object_id(self):
        """Return suggested object id."""
        return f"{self._serial} {self._name}"

    @property
    def native_unit_of_measurement(self):
        return "A"

    @property
    def unique_id(self):
        return f"{self._config.entry_id}_{self._serial} - {self._name}"

    @property
    def entity_category(self):
        return self._entity_category

    @property
    def icon(self):
        return self._icon
