"""Tests for the data parsing helpers in the AlphaESS coordinator."""

import pytest

from custom_components.alphaess.coordinator import (
    DataProcessor,
    InverterDataParser,
    TimeHelper,
)
from custom_components.alphaess.enums import AlphaESSNames


@pytest.fixture
def parser() -> InverterDataParser:
    """Return a parser instance."""
    return InverterDataParser(DataProcessor())


class TestDataProcessor:
    def test_process_value_passthrough(self):
        assert DataProcessor.process_value(5) == 5
        assert DataProcessor.process_value(0) == 0
        assert DataProcessor.process_value("x") == "x"
        assert DataProcessor.process_value(False) is False

    def test_process_value_empty(self):
        assert DataProcessor.process_value(None) is None
        assert DataProcessor.process_value("") is None
        assert DataProcessor.process_value("   ") is None
        assert DataProcessor.process_value(None, default=7) == 7
        assert DataProcessor.process_value("", default="d") == "d"

    def test_safe_get(self):
        assert DataProcessor.safe_get({"a": 1}, "a") == 1
        assert DataProcessor.safe_get({"a": ""}, "a", "d") == "d"
        assert DataProcessor.safe_get(None, "a", "d") == "d"
        assert DataProcessor.safe_get({}, "missing") is None

    def test_safe_calculate(self):
        assert DataProcessor.safe_calculate(10.0, 4.0) == 6.0
        assert DataProcessor.safe_calculate(None, 4.0) is None
        assert DataProcessor.safe_calculate(10.0, None) is None


class TestTimeHelper:
    def test_get_rounded_time_format(self):
        value = TimeHelper.get_rounded_time()
        hours, minutes = value.split(":")
        assert 0 <= int(hours) <= 23
        assert int(minutes) in (0, 15, 30, 45)

    def test_get_rounded_time_past_45_rolls_to_next_hour(self, freezer):
        freezer.move_to("2026-06-11 10:50:00")
        assert TimeHelper.get_rounded_time() == "11:00"

    def test_get_rounded_time_mid_hour(self, freezer):
        freezer.move_to("2026-06-11 10:20:00")
        assert TimeHelper.get_rounded_time() == "10:30"

    def test_calculate_time_window(self):
        start, end = TimeHelper.calculate_time_window(60)
        start_h, start_m = (int(x) for x in start.split(":"))
        end_h, end_m = (int(x) for x in end.split(":"))
        start_total = start_h * 60 + start_m
        end_total = end_h * 60 + end_m
        # End is start + 60 minutes, modulo a day
        assert (end_total - start_total) % (24 * 60) == 60


class TestParseEnergyData:
    def test_full_payload(self, parser):
        data = parser.parse_energy_data(
            {
                "epv": 10.0,
                "eOutput": 2.0,
                "eGridCharge": 1.0,
                "eCharge": 5.0,
                "eInput": 3.0,
                "eDischarge": 4.0,
                "eChargingPile": 1.5,
                "theDate": "2026-06-11",
            }
        )
        assert data[AlphaESSNames.SolarProduction] == 10.0
        assert data[AlphaESSNames.SolarToLoad] == 8.0  # epv - eOutput
        assert data[AlphaESSNames.SolarToBattery] == 4.0  # eCharge - eGridCharge
        assert data[AlphaESSNames.GridToBattery] == 1.0
        assert data[AlphaESSNames.EVCharger] == 1.5
        assert data[AlphaESSNames.DailyEnergyDate] == "2026-06-11"

    def test_missing_values_produce_none(self, parser):
        data = parser.parse_energy_data({})
        assert data[AlphaESSNames.SolarProduction] is None
        assert data[AlphaESSNames.SolarToLoad] is None
        assert data[AlphaESSNames.SolarToBattery] is None


class TestParseSummaryData:
    def test_currency_from_api(self, parser):
        data = parser.parse_summary_data({"moneyType": "GBP", "eload": 5})
        assert data[AlphaESSNames.CurrencyCode] == "GBP"
        assert data["Currency"] == "GBP"
        assert data[AlphaESSNames.TotalLoad] == 5

    def test_currency_fallback(self, parser):
        data = parser.parse_summary_data({}, fallback_currency="EUR")
        assert data[AlphaESSNames.CurrencyCode] == "EUR"

    def test_currency_unknown(self, parser):
        data = parser.parse_summary_data({})
        assert data[AlphaESSNames.CurrencyCode] == "Unknown"

    def test_a_symbol_is_reported_as_a_code(self, parser):
        """AlphaESS switched moneyType from "EUR" to "€"; the sensor is named
        Currency Code and Home Assistant's monetary class needs one."""
        data = parser.parse_summary_data({"moneyType": "€", "eload": 5})
        assert data[AlphaESSNames.CurrencyCode] == "EUR"
        assert data["Currency"] == "EUR"

    def test_an_ambiguous_symbol_uses_the_configured_currency(self, parser):
        data = parser.parse_summary_data(
            {"moneyType": "$"}, fallback_currency="AUD",
        )
        assert data[AlphaESSNames.CurrencyCode] == "AUD"

    def test_self_consumption_scaling(self, parser):
        data = parser.parse_summary_data(
            {"eselfConsumption": 0.5, "eselfSufficiency": 0.25}
        )
        assert data[AlphaESSNames.SelfConsumption] == 50
        assert data[AlphaESSNames.SelfSufficiency] == 25

    def test_self_consumption_null(self, parser):
        data = parser.parse_summary_data({})
        assert data[AlphaESSNames.SelfConsumption] is None
        assert data[AlphaESSNames.SelfSufficiency] is None


class TestBackupScheduleParsers:
    """The legacy two-slot parsers feed entity data in backup mode only."""

    def test_charge_config(self, parser):
        data = parser.parse_charge_config(
            {
                "gridCharge": 1,
                "batHighCap": 95,
                "timeChaf1": "01:00",
                "timeChae1": "05:00",
            }
        )
        assert data["gridCharge"] == 1
        assert data[AlphaESSNames.batHighCap] == 95
        assert data[AlphaESSNames.ChargeTime1] == "01:00 - 05:00"
        # An unused slot (start == end, or missing) reads as "Not set"
        # instead of a zero-length midnight window.
        assert data[AlphaESSNames.ChargeTime2] == "Not set"
        assert data["charge_timeChaf1"] == "01:00"

    def test_discharge_config(self, parser):
        data = parser.parse_discharge_config(
            {
                "ctrDis": 0,
                "batUseCap": 15,
                "timeDisf2": "20:00",
                "timeDise2": "23:00",
            }
        )
        assert data["ctrDis"] == 0
        assert data[AlphaESSNames.batUseCap] == 15
        assert data[AlphaESSNames.DischargeTime1] == "Not set"
        assert data[AlphaESSNames.DischargeTime2] == "20:00 - 23:00"
        assert data["discharge_timeDise2"] == "23:00"


class TestParsePowerData:
    def test_basic_power(self, parser):
        data = parser.parse_power_data(
            {
                "soc": 88,
                "pbat": -500,
                "pload": 1200,
                "ppv": 3000,
                "pgrid": -1800,
                "ppvDetail": {"ppv1": 1500, "ppv2": 1500},
                "pgridDetail": {"pmeterL1": -600, "pmeterL2": -600, "pmeterL3": -600},
            },
            None,
        )
        assert data[AlphaESSNames.BatterySOC] == 88
        assert data[AlphaESSNames.Load] == 1200
        assert data[AlphaESSNames.PPV1] == 1500
        assert data[AlphaESSNames.GridIOL3] == -600

    def test_ev_connectors_only_when_present(self, parser):
        data = parser.parse_power_data(
            {"pevDetail": {"ev1Power": 7000}}, None
        )
        assert data[AlphaESSNames.ElectricVehiclePowerOne] == 7000
        assert AlphaESSNames.ElectricVehiclePowerTwo not in data

    def test_soc_fallback_from_one_day_power(self, parser):
        data = parser.parse_power_data(
            {"soc": 0}, [{"cbat": 42.5}]
        )
        assert data[AlphaESSNames.StateOfCharge] == 42.5


class TestParseEvData:
    def test_list_payload(self, parser):
        data = parser.parse_ev_data(
            [{"evchargerSn": "EV123", "evchargerModel": "SMILE-EVCT11"}],
            {"EVStatus": {"evchargerStatus": 3}, "EVCurrent": {"currentsetting": 16}},
        )
        assert data[AlphaESSNames.evchargersn] == "EV123"
        assert data[AlphaESSNames.evchargerstatus] == 3
        assert data[AlphaESSNames.evcurrentsetting] == 16

    def test_empty(self, parser):
        assert parser.parse_ev_data(None, {}) == {}
        assert parser.parse_ev_data([], {}) == {}


class TestParseLocalIpData:
    def test_empty(self, parser):
        assert parser.parse_local_ip_data({}) == {}

    def test_payload(self, parser):
        data = parser.parse_local_ip_data(
            {
                "ip": "192.168.1.50",
                "status": {"devstatus": 1, "wifistatus": 5},
                "device_info": {"sn": "AL1234", "sw": "1.2.3"},
            }
        )
        assert data[AlphaESSNames.localIP] == "192.168.1.50"
        assert data[AlphaESSNames.deviceStatus] == 1
        assert data[AlphaESSNames.wifiStatus] == 5
        assert data[AlphaESSNames.deviceSerialNumber] == "AL1234"
        assert data[AlphaESSNames.softwareVersion] == "1.2.3"
