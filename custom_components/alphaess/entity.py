"""Parent class for AlphaESS devices."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntityDescription
from homeassistant.components.number import NumberEntityDescription
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.components.time import TimeEntityDescription


@dataclass(frozen=True)
class AlphaESSSensorDescription(SensorEntityDescription):
    """Class to describe an AlphaESS sensor."""

    native_value: Callable[
                      [str | int | float], str | int | float
                  ] | None = lambda val: val


@dataclass(frozen=True)
class AlphaESSButtonDescription(ButtonEntityDescription):
    """Class to describe an AlphaESS Button."""

    native_value: Callable[
                      [str | int | float], str | int | float
                  ] | None = lambda val: val


@dataclass(frozen=True)
class AlphaESSNumberDescription(NumberEntityDescription):
    """Class to describe an AlphaESS Number."""

    native_value: Callable[
                      [str | int | float], str | int | float
                  ] | None = lambda val: val


@dataclass(frozen=True)
class AlphaESSSwitchDescription(SwitchEntityDescription):
    """Class to describe an AlphaESS Switch."""

    coordinator_key: str | None = None


@dataclass(frozen=True)
class AlphaESSTimeDescription(TimeEntityDescription):
    """Class to describe an AlphaESS Time entity."""

    coordinator_key: str | None = None
