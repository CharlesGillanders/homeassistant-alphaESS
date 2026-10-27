"""Renaming schedule entities must preserve their live coordinator state."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.alphaess.enums import AlphaESSNames
from custom_components.alphaess.number import AlphaNumber
from custom_components.alphaess.sensorlist import (
    CHARGE_DISCHARGE_SWITCHES,
    DISCHARGE_AND_CHARGE_NUMBERS,
)
from custom_components.alphaess.switch import AlphaSwitch

from .conftest import FakeEntry

SERIAL = "AL1000021000123"


@pytest.mark.parametrize("kind", ["number", "switch"])
def test_entity_id_changed_preserves_pending_schedule(kind, make_coordinator, monkeypatch):
    """Core 2026.11's rename callback forwards bookkeeping without restoring again."""
    coordinator = make_coordinator()
    coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
    coordinator._periodic_readable[SERIAL] = True
    coordinator._periodic_schedules[SERIAL] = coordinator._normalise_periodic_schedule({
        "executeCycleType": 0,
        "chargeTimeList": [{"beginTime": "01:00", "endTime": "05:00", "chargeLimit": 90}],
        "dischargeTimeList": [{"beginTime": "17:00", "endTime": "21:00", "chargeLimit": 20}],
    })
    coordinator.set_periodic_enable_intent(SERIAL, grid_charge=1, ctr_dis=1)
    coordinator.stage_schedule_change(SERIAL, charge={"gridCharge": 0}, discharge={"batUseCap": 35})

    if kind == "number":
        description = next(d for d in DISCHARGE_AND_CHARGE_NUMBERS if d.key == AlphaESSNames.batUseCap)
        entity = AlphaNumber(coordinator, SERIAL, FakeEntry(), description)
        entity._attr_native_value = 35
        coordinator.set_number_setting(SERIAL, "batUseCap", 35)
    else:
        entity = AlphaSwitch(coordinator, SERIAL, FakeEntry(), CHARGE_DISCHARGE_SWITCHES[0])
        entity._optimistic_state = False

    old_entity_id = f"{kind}.old_schedule_control"
    entity.entity_id = f"{kind}.renamed_schedule_control"
    entity.async_added_to_hass = AsyncMock()
    entity.async_will_remove_from_hass = AsyncMock()
    entity.async_get_last_state = AsyncMock()
    before = deepcopy((
        coordinator.data,
        coordinator._schedule_drafts,
        coordinator._periodic_enable_intent,
        coordinator.number_settings,
    ))

    # The Core callback is introduced in 2026.11. Earlier supported versions
    # never invoke it; provide the base callback to exercise that future path.
    base_callback = MagicMock()
    monkeypatch.setattr(CoordinatorEntity, "async_entity_id_changed", base_callback, raising=False)
    entity.async_entity_id_changed(old_entity_id)

    base_callback.assert_called_once_with(old_entity_id)
    assert entity.entity_id == f"{kind}.renamed_schedule_control"
    assert (
        coordinator.data,
        coordinator._schedule_drafts,
        coordinator._periodic_enable_intent,
        coordinator.number_settings,
    ) == before
    entity.async_added_to_hass.assert_not_awaited()
    entity.async_will_remove_from_hass.assert_not_awaited()
    entity.async_get_last_state.assert_not_awaited()
    if kind == "number":
        assert entity.native_value == 35
    else:
        assert entity.is_on is False
