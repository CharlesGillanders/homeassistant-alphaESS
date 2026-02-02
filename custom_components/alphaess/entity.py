"""Parent class for AlphaESS devices."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntityDescription
from homeassistant.components.number import NumberEntityDescription
from homeassistant.components.sensor import SensorEntityDescription


@dataclass(frozen=True, kw_only=True)
class AlphaESSSensorDescription(SensorEntityDescription):
    """Class to describe an AlphaESS sensor."""

    native_value: Callable[
                      [str | int | float], str | int | float
                  ] | None = lambda val: val


@dataclass(frozen=True, kw_only=True)
class AlphaESSButtonDescription(ButtonEntityDescription):
    """Class to describe an AlphaESS Button."""

    native_value: Callable[
                      [str | int | float], str | int | float
                  ] | None = lambda val: val


@dataclass(frozen=True, kw_only=True)
class AlphaESSNumberDescription(NumberEntityDescription):
    """Class to describe an AlphaESS Number."""

    native_value: Callable[
                      [str | int | float], str | int | float
                  ] | None = lambda val: val
