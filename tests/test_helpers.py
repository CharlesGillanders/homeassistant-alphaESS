"""Tests for small helpers in sensor.py and __init__.py."""
from types import SimpleNamespace

from custom_components.alphaess import _build_inverter_model_list, _build_ip_address_map
from custom_components.alphaess.const import (
    CONF_INVERTER_MODEL,
    CONF_IP_ADDRESS,
    CONF_SERIAL_NUMBER,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from custom_components.alphaess.currency import normalize_currency_unit


def _entry_with_subentries(subentries):
    return SimpleNamespace(subentries={str(i): s for i, s in enumerate(subentries)})


def _inverter_subentry(serial, ip="", model=""):
    return SimpleNamespace(
        subentry_type=SUBENTRY_TYPE_INVERTER,
        data={
            CONF_SERIAL_NUMBER: serial,
            CONF_IP_ADDRESS: ip,
            CONF_INVERTER_MODEL: model,
        },
    )


class TestNormalizeCurrencyUnit:
    def test_none_falls_back(self):
        assert normalize_currency_unit(None, "EUR") == "EUR"
        assert normalize_currency_unit("", "EUR") == "EUR"
        assert normalize_currency_unit("  ", "EUR") == "EUR"

    def test_iso_code_passthrough(self):
        assert normalize_currency_unit("gbp", "EUR") == "GBP"
        assert normalize_currency_unit("USD", "EUR") == "USD"

    def test_symbol_mapping(self):
        assert normalize_currency_unit("€", "USD") == "EUR"
        assert normalize_currency_unit("£", "USD") == "GBP"
        assert normalize_currency_unit("$", "EUR") == "USD"

    def test_unknown_symbol_falls_back(self):
        assert normalize_currency_unit("☃", "AUD") == "AUD"

    def test_an_ambiguous_symbol_prefers_the_configured_currency(self):
        """AlphaESS sells into plenty of dollar and yen markets, and the symbol
        alone cannot tell them apart. What Home Assistant is set to can."""
        assert normalize_currency_unit("$", "AUD") == "AUD"
        assert normalize_currency_unit("$", "nzd") == "NZD"
        assert normalize_currency_unit("¥", "CNY") == "CNY"
        assert normalize_currency_unit("kr", "NOK") == "NOK"

    def test_an_ambiguous_symbol_falls_to_the_common_one(self):
        assert normalize_currency_unit("$", "GBP") == "USD"
        assert normalize_currency_unit("$", None) == "USD"
        assert normalize_currency_unit("¥", "GBP") == "JPY"

    def test_an_unambiguous_symbol_ignores_the_configured_currency(self):
        assert normalize_currency_unit("€", "AUD") == "EUR"
        assert normalize_currency_unit("A$", "USD") == "AUD"


class TestBuildIpAddressMap:
    def test_valid_ip(self):
        entry = _entry_with_subentries([_inverter_subentry("AL1", ip="192.168.1.10")])
        assert _build_ip_address_map(entry) == {"AL1": "192.168.1.10"}

    def test_invalid_ip_maps_to_none(self):
        entry = _entry_with_subentries([_inverter_subentry("AL1", ip="not-an-ip")])
        assert _build_ip_address_map(entry) == {"AL1": None}

    def test_empty_and_zero_ip(self):
        entry = _entry_with_subentries(
            [_inverter_subentry("AL1", ip=""), _inverter_subentry("AL2", ip="0")]
        )
        assert _build_ip_address_map(entry) == {"AL1": None, "AL2": None}

    def test_non_inverter_subentries_skipped(self):
        ev = SimpleNamespace(
            subentry_type=SUBENTRY_TYPE_EV_CHARGER,
            data={CONF_SERIAL_NUMBER: "EV1"},
        )
        entry = _entry_with_subentries([ev])
        assert _build_ip_address_map(entry) == {}


class TestBuildInverterModelList:
    def test_models_collected(self):
        entry = _entry_with_subentries(
            [
                _inverter_subentry("AL1", model="SMILE5-INV"),
                _inverter_subentry("AL2", model="Storion-S5"),
                _inverter_subentry("AL3", model=""),
            ]
        )
        assert _build_inverter_model_list(entry) == ["SMILE5-INV", "Storion-S5"]
