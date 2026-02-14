from typing import List
from homeassistant.components.number import NumberEntity
from homeassistant.helpers.device_registry import DeviceInfo, DeviceEntryType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.number import RestoreNumber
import logging

from .const import (
    DOMAIN, INVERTER_SETTING_BLACKLIST, CONF_SERIAL_NUMBER, SUBENTRY_TYPE_INVERTER,
    SUBENTRY_TYPE_EV_CHARGER, CONF_PARENT_INVERTER,
)
from .coordinator import AlphaESSDataUpdateCoordinator
from .enums import AlphaESSNames
from .sensorlist import DISCHARGE_AND_CHARGE_NUMBERS, EV_CHARGER_NUMBERS
from .sensor import _build_inverter_device_info, _build_ev_charger_device_info

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: AlphaESSDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

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
            inverter_device_info = _build_inverter_device_info(coordinator, serial, data)

            number_entities: List[NumberEntity] = []

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
            ev_charger = data.get("EV Charger S/N")
            ev_subentry_serials = {
                sub.data.get(CONF_SERIAL_NUMBER)
                for sub in entry.subentries.values()
                if sub.subentry_type == SUBENTRY_TYPE_EV_CHARGER
            }
            if ev_charger and ev_charger not in ev_subentry_serials:
                ev_device_info = _build_ev_charger_device_info(coordinator, data)
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
            ev_charger = data.get("EV Charger S/N")
            if not ev_charger:
                continue

            ev_device_info = _build_ev_charger_device_info(coordinator, data)
            ev_entities: List[NumberEntity] = []
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

        if self.key is AlphaESSNames.batHighCap:
            self._def_initial_value = float(90)
        else:
            self._def_initial_value = float(10)

        if device_info:
            self._attr_device_info = device_info

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

        # Push to API
        data = self._coordinator.data.get(self._serial, {})

        if self.key is AlphaESSNames.batHighCap:
            grid_charge = data.get("gridCharge", 1)
            result = await self._coordinator.api.updateChargeConfigInfo(
                self._serial,
                value,
                grid_charge,
                data.get("charge_timeChae1") or "00:00",
                data.get("charge_timeChae2") or "00:00",
                data.get("charge_timeChaf1") or "00:00",
                data.get("charge_timeChaf2") or "00:00",
            )
            _LOGGER.info("Updated batHighCap for %s to %s - Result: %s", self._serial, value, result)
        elif self.key is AlphaESSNames.batUseCap:
            ctr_dis = data.get("ctrDis", 1)
            result = await self._coordinator.api.updateDisChargeConfigInfo(
                self._serial,
                value,
                ctr_dis,
                data.get("discharge_timeDise1") or "00:00",
                data.get("discharge_timeDise2") or "00:00",
                data.get("discharge_timeDisf1") or "00:00",
                data.get("discharge_timeDisf2") or "00:00",
            )
            _LOGGER.info("Updated batUseCap for %s to %s - Result: %s", self._serial, value, result)

        await self._coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        """Number controls require cloud API to function."""
        if not self.coordinator.last_update_success:
            return False
        return self._coordinator.cloud_available

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
        await self._coordinator.set_ev_charger_current(self._ev_serial, int(value))

    @property
    def available(self) -> bool:
        """EV charger controls require cloud API to function."""
        if not self.coordinator.last_update_success:
            return False
        return self._coordinator.cloud_available

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
