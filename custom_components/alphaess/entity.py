"""Entity descriptions and shared entity helpers for AlphaESS."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.button import ButtonEntityDescription
from homeassistant.components.number import NumberEntityDescription
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.components.time import TimeEntityDescription
from homeassistant.util import slugify


def default_object_id(serial: str, name: str) -> str:
    """Return the object ID of the documented entity ID, ``<serial>_<name>``.

    Left to itself, Core builds a new entity's ID from its area and device
    names too, which puts the serial in twice. Entities set
    ``<domain>.<serial>_<name>`` themselves instead; one that is already
    registered keeps the ID it has.
    """
    return slugify(f"{serial} {name}")


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
class AlphaESSBinarySensorDescription(BinarySensorEntityDescription):
    """Class to describe an AlphaESS Binary Sensor."""

    direction: int | None = None


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
