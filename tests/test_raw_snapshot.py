"""The export_raw_snapshot service: what the API said, with identities removed.

Every question on #267 needed a response body that a tester had to sign a
request for by hand. The coordinator now keeps the last answer from every
endpoint it calls, and the service hands them over with every serial,
credential and address gone, so the export can go straight onto an issue.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest
import voluptuous as vol
from alphaess.alphaess import AlphaESSApiError
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError

import custom_components.alphaess as init_mod
from custom_components.alphaess.const import (
    CONF_ASSUME_SCHEDULE_FLAGS,
    CONF_EV_CHARGER_MODEL,
    CONF_INVERTER_MODEL,
    CONF_PARENT_INVERTER,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    SUBENTRY_TYPE_EV_CHARGER,
    SUBENTRY_TYPE_INVERTER,
)
from custom_components.alphaess.coordinator import RAW_ACCOUNT_SCOPE
from custom_components.alphaess.enums import AlphaESSNames
from custom_components.alphaess.snapshot import SNAPSHOT_FORMAT, build_raw_snapshot

from .conftest import FakeEntry

SERIAL = "ALF021025080318"
OTHER = "AL7011023030623"
EV_SERIAL = "EVC0123456789"
SECOND_EV = "EVC9999999999"


def _api_error(code, description=None):
    return AlphaESSApiError(code=code, description=description)


class TestTheGateRetainsEveryAnswer:
    """One record per endpoint per system, replaced on every call."""

    async def test_a_successful_read_is_kept_under_its_system(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        mock_api.getSumDataForCustomer.return_value = {"eload": 5}

        await coordinator._call_cloud_api(mock_api.getSumDataForCustomer, SERIAL)

        record = coordinator.raw_responses()[SERIAL]["getSumDataForCustomer"]
        assert record["response"] == {"eload": 5}
        assert record["request"] == {"args": [SERIAL], "kwargs": {}}
        assert record["captured_at"]

    async def test_a_write_is_keyed_by_its_sysSn_keyword(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        periods = [{"beginTime": "01:00", "endTime": "02:00", "chargeLimit": 90}]

        await coordinator._call_cloud_api(
            mock_api.setTimeChargeBySn,
            sysSn=SERIAL, executeCycleType=0,
            chargeTimeList=periods, dischargeTimeList=periods,
            gridChargeCycle=1, ctrDisCycle=1,
        )

        record = coordinator.raw_responses()[SERIAL]["setTimeChargeBySn"]
        assert record["request"]["kwargs"]["chargeTimeList"] == periods
        assert record["request"]["kwargs"]["gridChargeCycle"] == 1
        assert record["response"] is None

    async def test_a_call_naming_no_system_lands_in_the_account_scope(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        mock_api.getESSList.return_value = [{"sysSn": SERIAL}]

        await coordinator._call_cloud_api(mock_api.getESSList)

        record = coordinator.raw_responses()[RAW_ACCOUNT_SCOPE]["getESSList"]
        assert record["response"] == [{"sysSn": SERIAL}]
        assert SERIAL not in coordinator.raw_responses()

    async def test_a_rejection_is_kept_as_its_return_code(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        mock_api.getTimeChargeBySn.side_effect = _api_error(6017, "No operation permissions")

        with pytest.raises(AlphaESSApiError):
            await coordinator._call_cloud_api(mock_api.getTimeChargeBySn, SERIAL)

        record = coordinator.raw_responses()[SERIAL]["getTimeChargeBySn"]
        assert record["error"]["code"] == 6017
        assert "No operation permissions" in record["error"]["message"]
        assert "response" not in record

    async def test_a_transport_failure_is_kept_as_the_exception(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        mock_api.getLastPowerData.side_effect = TimeoutError("socket closed")

        with pytest.raises(TimeoutError):
            await coordinator._call_cloud_api(mock_api.getLastPowerData, SERIAL)

        record = coordinator.raw_responses()[SERIAL]["getLastPowerData"]
        assert record["exception"] == "TimeoutError: socket closed"
        assert "response" not in record

    async def test_a_6053_retry_that_succeeds_keeps_the_retried_answer(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.throttle_multiplier = 0.0
        mock_api.getSumDataForCustomer.side_effect = [_api_error(6053), {"eload": 7}]

        await coordinator._call_cloud_api(mock_api.getSumDataForCustomer, SERIAL)

        record = coordinator.raw_responses()[SERIAL]["getSumDataForCustomer"]
        assert record["response"] == {"eload": 7}
        assert "error" not in record

    async def test_a_6053_retry_rejected_again_keeps_that_rejection(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        coordinator.throttle_multiplier = 0.0
        mock_api.getSumDataForCustomer.side_effect = [_api_error(6053), _api_error(6053)]

        with pytest.raises(AlphaESSApiError):
            await coordinator._call_cloud_api(mock_api.getSumDataForCustomer, SERIAL)

        record = coordinator.raw_responses()[SERIAL]["getSumDataForCustomer"]
        assert record["error"]["code"] == 6053
        assert coordinator._fast_api_lane is False

    async def test_bind_arguments_are_never_retained(
        self, make_coordinator, mock_api
    ):
        """A check code or verification code is not evidence anyone needs."""
        coordinator = make_coordinator()

        await coordinator.async_request_verification_code(SERIAL, "CHECK-CODE")
        await coordinator.async_bind_system(SERIAL, "123456")
        await coordinator.async_unbind_system(SERIAL)

        records = coordinator.raw_responses()[SERIAL]
        for endpoint in ("getVerificationCode", "bindSn", "unBindSn"):
            assert "request" not in records[endpoint]
            assert "response" in records[endpoint]
        assert "CHECK-CODE" not in json.dumps(records)

    async def test_the_latest_answer_replaces_the_previous_one(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        mock_api.getSumDataForCustomer.side_effect = [{"eload": 1}, {"eload": 2}]

        await coordinator._call_cloud_api(mock_api.getSumDataForCustomer, SERIAL)
        await coordinator._call_cloud_api(mock_api.getSumDataForCustomer, SERIAL)

        records = coordinator.raw_responses()[SERIAL]
        assert list(records) == ["getSumDataForCustomer"]
        assert records["getSumDataForCustomer"]["response"] == {"eload": 2}

    async def test_the_export_copy_is_detached_from_the_cache(
        self, make_coordinator, mock_api
    ):
        coordinator = make_coordinator()
        payload = {"chargeTimeList": [{"beginTime": "01:00"}]}
        mock_api.getTimeChargeBySn.return_value = payload

        await coordinator._call_cloud_api(mock_api.getTimeChargeBySn, SERIAL)
        payload["chargeTimeList"].clear()
        exported = coordinator.raw_responses()
        exported[SERIAL]["getTimeChargeBySn"]["response"]["chargeTimeList"].append("x")

        kept = coordinator.raw_responses()[SERIAL]["getTimeChargeBySn"]["response"]
        assert kept == {"chargeTimeList": [{"beginTime": "01:00"}]}

    async def test_a_method_without_a_name_is_still_recorded(self, make_coordinator):
        coordinator = make_coordinator()
        anonymous = AsyncMock(return_value={"ok": True})
        del anonymous.__name__

        await coordinator._call_cloud_api(anonymous, SERIAL)

        (endpoint,) = coordinator.raw_responses()[SERIAL]
        assert endpoint == str(anonymous)

    async def test_local_dongle_answers_are_kept_from_every_path(
        self, make_coordinator, mock_api
    ):
        """getIPData bypasses the cloud gate, so each call site records it."""
        coordinator = make_coordinator(ip_map={SERIAL: "192.168.1.9"})
        coordinator.data = {SERIAL: {"Model": "SMILE5-INV"}}
        dongle = {"status": {"connssid": "Home"}, "device_info": {"sw": "1.2"}}
        mock_api.getIPData.return_value = dongle

        await coordinator._fetch_per_inverter_local_data()
        record = coordinator.raw_responses()[SERIAL]["getIPData"]
        assert record == {
            "captured_at": record["captured_at"],
            "request": {"ip": "192.168.1.9"},
            "response": dongle,
        }

        coordinator._raw_responses.clear()
        await coordinator._fallback_to_local_data(RuntimeError("cloud down"))
        assert coordinator.raw_responses()[SERIAL]["getIPData"]["response"] == dongle

        coordinator._raw_responses.clear()
        mock_api.ipaddress = "192.168.1.9"
        await coordinator._fetch_inverter_data(
            SERIAL, {"sysSn": SERIAL, "minv": "SMILE5-INV"}, 0.0,
            include_local_ip=True,
        )
        assert coordinator.raw_responses()[SERIAL]["getIPData"]["request"] == {
            "ip": "192.168.1.9",
        }


def _subentry(subentry_type, data, unique_id):
    return ConfigSubentry(
        data=data, subentry_type=subentry_type, title="t", unique_id=unique_id,
    )


def _loaded(coordinator):
    """A coordinator holding one published system, one pruned one, and an
    account-level answer, every one of them naming a serial somewhere."""
    coordinator.data = {
        SERIAL: {
            "Model": "SMILE-G3-T20-INV",
            AlphaESSNames.TotalLoad: 5.0,
            AlphaESSNames.evchargersn: EV_SERIAL,
            AlphaESSNames.registerKey: "reg-key-value",
            AlphaESSNames.password: "wifi-pass-value",
            AlphaESSNames.PollMode: "normal",
            "entity_hint": f"switch.{SERIAL.lower()}_scheduled_charging",
            "enum_value": AlphaESSNames.PollMode,
            "set_value": {"only"},
        },
    }
    coordinator._periodic_readable[SERIAL] = True
    coordinator._periodic_schedules[SERIAL] = {
        "executeCycleType": 1, "chargeTimeList": [], "dischargeTimeList": [],
    }
    coordinator._raw_responses = {
        RAW_ACCOUNT_SCOPE: {
            "getESSList": {
                "captured_at": "t",
                "request": {"args": [], "kwargs": {}},
                "response": [
                    {"sysSn": SERIAL, "minv": "SMILE-G3-T20-INV"},
                    {"sysSn": OTHER, "minv": "SMILE5-INV"},
                    {"sysSn": SERIAL, "minv": "duplicate"},
                    "not-a-dict",
                ],
            },
        },
        SERIAL: {
            "getTimeChargeBySn": {
                "captured_at": "t",
                "request": {"args": [SERIAL], "kwargs": {}},
                "response": {
                    "sysSn": SERIAL, "executeCycleType": 1,
                    "gridChargeCycle": 0, "ctrDisCycle": 0,
                    "chargeTimeList": [
                        {"beginTime": "09:45", "endTime": "10:00",
                         "weeks": (6, 5, 4, 3, 2, 1, 7), "chargePower": 12501},
                    ],
                    "dischargeTimeList": [],
                },
            },
            "getEvChargerConfigList": {
                "captured_at": "t",
                "request": {"args": [SERIAL], "kwargs": {}},
                "response": [
                    {"evchargerSn": EV_SERIAL, "evchargerModel": "SMILE-EVCT11"},
                    {"evchargerSn": EV_SERIAL, "evchargerModel": "again"},
                    {"evchargerSn": SECOND_EV, "evchargerModel": "only-in-the-raw"},
                ],
            },
            "getIPData": {
                "captured_at": "t",
                "request": {"ip": "192.168.1.9"},
                "response": {
                    "status": {"connssid": "MyWifi", "wifiip": "192.168.1.50"},
                    "device_info": {
                        "sn": "DONGLE123", "key": "regkey", "password": "pw",
                        "sw": "1.2.3",
                    },
                },
            },
            "getSumDataForCustomer": {
                "captured_at": "t",
                "request": {"args": [SERIAL], "kwargs": {}},
                "error": {"code": 6017, "message": f"6017 when calling {SERIAL}"},
            },
        },
        OTHER: {
            "getIPData": {
                "captured_at": "t",
                "request": {"ip": "192.168.1.10"},
                "response": {"status": {}, "device_info": {"sn": "DONGLE999"}},
            },
        },
    }
    inverter = _subentry(
        SUBENTRY_TYPE_INVERTER,
        {CONF_SERIAL_NUMBER: SERIAL, CONF_INVERTER_MODEL: "SMILE-G3-T20-INV"},
        f"inverter_{SERIAL}",
    )
    blank = _subentry(
        SUBENTRY_TYPE_INVERTER, {CONF_SERIAL_NUMBER: "", CONF_INVERTER_MODEL: "x"},
        "inverter_blank",
    )
    charger = _subentry(
        SUBENTRY_TYPE_EV_CHARGER,
        {
            CONF_SERIAL_NUMBER: EV_SERIAL,
            CONF_EV_CHARGER_MODEL: "SMILE-EVCT11",
            CONF_PARENT_INVERTER: SERIAL,
        },
        f"ev_charger_{EV_SERIAL}",
    )
    entry = FakeEntry(
        data={"AppID": "app-id", "AppSecret": "app-secret"},
        options={CONF_ASSUME_SCHEDULE_FLAGS: True},
        subentries={
            inverter.subentry_id: inverter,
            blank.subentry_id: blank,
            charger.subentry_id: charger,
        },
    )
    entry.runtime_data = coordinator
    return entry


class TestTheExportRemovesEveryIdentity:
    def test_nothing_that_names_a_system_or_a_person_survives(self, make_coordinator):
        coordinator = make_coordinator(models=["SMILE-G3-T20-INV"])
        coordinator.assume_schedule_flags_enabled = True
        entry = _loaded(coordinator)

        result = build_raw_snapshot(entry, coordinator, version="0.9.0")

        text = json.dumps(result).lower()
        for secret in (
            SERIAL, OTHER, EV_SERIAL, SECOND_EV, "reg-key-value", "wifi-pass-value",
            "mywifi", "192.168.1.50", "192.168.1.9", "192.168.1.10",
            "dongle123", "dongle999", "regkey", "app-id", "app-secret",
        ):
            assert secret.lower() not in text, secret
        # HA serialises service responses with orjson: plain str keys only.
        orjson.dumps(result)

    def test_placeholders_keep_the_records_readable(self, make_coordinator):
        coordinator = make_coordinator(models=["SMILE-G3-T20-INV"])
        coordinator.assume_schedule_flags_enabled = True
        entry = _loaded(coordinator)

        result = build_raw_snapshot(entry, coordinator, version="0.9.0")

        assert result["format"] == SNAPSHOT_FORMAT
        assert result["integration_version"] == "0.9.0"
        assert result["redaction"] == {
            **result["redaction"], "inverters": 2, "ev_chargers": 2,
        }
        assert set(result["inverters"]) == {"inverter_1", "inverter_2"}

        first = result["inverters"]["inverter_1"]
        assert first["model"] == "SMILE-G3-T20-INV"
        assert first["endpoints_captured"] == [
            "getEvChargerConfigList", "getIPData", "getSumDataForCustomer",
            "getTimeChargeBySn",
        ]
        periodic = first["raw"]["getTimeChargeBySn"]
        assert periodic["response"]["sysSn"] == "inverter_1"
        assert periodic["request"]["args"] == ["inverter_1"]
        assert periodic["response"]["chargeTimeList"][0]["weeks"] == [6, 5, 4, 3, 2, 1, 7]
        chargers = first["raw"]["getEvChargerConfigList"]["response"]
        assert chargers[0]["evchargerSn"] == "ev_charger_1"
        assert chargers[2]["evchargerSn"] == "ev_charger_2"
        assert first["raw"]["getSumDataForCustomer"]["error"] == {
            "code": 6017, "message": "6017 when calling inverter_1",
        }
        # The parsed view and schedule state travel with the raw answers.
        assert first["data"]["Total Load"] == 5.0
        assert first["data"]["Register Key"] == "**REDACTED**"
        # The parsed view redacts the charger serial by key, as the
        # diagnostics download does; the placeholder is for the raw bodies.
        assert first["data"]["EV Charger S/N"] == "**REDACTED**"
        assert first["data"]["entity_hint"] == "switch.inverter_1_scheduled_charging"
        assert first["data"]["enum_value"] == "Poll Mode"
        assert first["data"]["set_value"] == ["only"]
        assert first["schedule"]["governing_store"] == "periodic"
        assert first["schedule"]["capabilities"]["assume_schedule_flags_enabled"] is True

        # The local dongle answer keeps its shape, minus every identifier.
        local = first["raw"]["getIPData"]
        assert local["request"]["ip"] == "**REDACTED**"
        assert local["response"]["status"]["connssid"] == "**REDACTED**"
        assert local["response"]["device_info"]["sn"] == "**REDACTED**"
        assert local["response"]["device_info"]["sw"] == "1.2.3"

        # A system the coordinator no longer publishes still shows its answers.
        pruned = result["inverters"]["inverter_2"]
        assert pruned["model"] is None
        assert pruned["schedule"] is None
        assert pruned["data"] is None
        assert pruned["raw"]["getIPData"]["response"]["device_info"]["sn"] == "**REDACTED**"

        account = result["account"]["getESSList"]["response"]
        assert [unit.get("sysSn") if isinstance(unit, dict) else unit for unit in account] == [
            "inverter_1", "inverter_2", "inverter_1", "not-a-dict",
        ]
        assert result["entry"]["options"] == {CONF_ASSUME_SCHEDULE_FLAGS: True}
        assert result["entry"]["subentries"] == [
            {"type": "inverter", "serial": "inverter_1", "parent": None,
             "model": "SMILE-G3-T20-INV"},
            {"type": "inverter", "serial": "", "parent": None, "model": "x"},
            {"type": "ev_charger", "serial": "ev_charger_1", "parent": "inverter_1",
             "model": "SMILE-EVCT11"},
        ]
        assert result["coordinator"]["assume_schedule_flags_enabled"] is True
        assert result["coordinator"]["api"]["fast_api_lane"] is True

    def test_an_empty_coordinator_exports_an_empty_snapshot(self, make_coordinator):
        coordinator = make_coordinator()
        entry = FakeEntry()
        entry.runtime_data = coordinator

        result = build_raw_snapshot(entry, coordinator, version="unknown")

        assert result["inverters"] == {}
        assert result["account"] == {}
        assert result["redaction"]["inverters"] == 0


def _entry_with(coordinator, entry_id="one"):
    entry = FakeEntry(entry_id=entry_id)
    entry.runtime_data = coordinator
    return entry


class TestTheServicePicksTheRightEntry:
    def test_a_serial_selects_the_entry_that_manages_it(self, mock_hass, make_coordinator):
        first, second = make_coordinator(), make_coordinator()
        first.data, second.data = {OTHER: {}}, {SERIAL: {}}
        entries = [_entry_with(first, "one"), _entry_with(second, "two")]
        mock_hass.config_entries.async_entries.return_value = entries

        assert init_mod._resolve_entry_for_export(mock_hass, {"serial": SERIAL}) is entries[1]

    def test_an_unknown_serial_is_a_validation_error(self, mock_hass, make_coordinator):
        coordinator = make_coordinator()
        coordinator.data = {OTHER: {}}
        mock_hass.config_entries.async_entries.return_value = [_entry_with(coordinator)]

        with pytest.raises(ServiceValidationError, match=SERIAL):
            init_mod._resolve_entry_for_export(mock_hass, {"serial": SERIAL})

    def test_a_config_entry_id_is_looked_up_directly(self, mock_hass, make_coordinator):
        entry = _entry_with(make_coordinator())
        mock_hass.config_entries.async_entries.return_value = [entry]
        mock_hass.config_entries.async_get_entry.return_value = entry

        assert init_mod._resolve_entry_for_export(
            mock_hass, {"config_entry_id": "one"}
        ) is entry
        mock_hass.config_entries.async_get_entry.assert_called_once_with("one")

    @pytest.mark.parametrize("found", ["missing", "other-domain", "not-loaded"])
    def test_a_config_entry_that_is_not_a_loaded_alphaess_entry_is_refused(
        self, mock_hass, make_coordinator, found
    ):
        loaded = _entry_with(make_coordinator())
        mock_hass.config_entries.async_entries.return_value = [loaded]
        if found == "missing":
            candidate = None
        else:
            candidate = FakeEntry(entry_id="two")
            if found == "other-domain":
                candidate.domain = "not_alphaess"
        mock_hass.config_entries.async_get_entry.return_value = candidate

        with pytest.raises(ServiceValidationError, match="two"):
            init_mod._resolve_entry_for_export(mock_hass, {"config_entry_id": "two"})

    def test_a_device_selects_the_entry_it_belongs_to(
        self, mock_hass, make_coordinator, monkeypatch
    ):
        entry = _entry_with(make_coordinator())
        mock_hass.config_entries.async_entries.return_value = [entry]
        registry = MagicMock()
        registry.async_get.return_value = SimpleNamespace(config_entries={"one"})
        monkeypatch.setattr(init_mod.dr, "async_get", MagicMock(return_value=registry))

        assert init_mod._resolve_entry_for_export(mock_hass, {"device_id": "dev"}) is entry

    def test_an_unknown_device_is_a_validation_error(
        self, mock_hass, make_coordinator, monkeypatch
    ):
        mock_hass.config_entries.async_entries.return_value = [_entry_with(make_coordinator())]
        registry = MagicMock()
        registry.async_get.return_value = None
        monkeypatch.setattr(init_mod.dr, "async_get", MagicMock(return_value=registry))

        with pytest.raises(ServiceValidationError, match="No device has id dev"):
            init_mod._resolve_entry_for_export(mock_hass, {"device_id": "dev"})

    def test_a_device_of_another_integration_is_a_validation_error(
        self, mock_hass, make_coordinator, monkeypatch
    ):
        mock_hass.config_entries.async_entries.return_value = [_entry_with(make_coordinator())]
        registry = MagicMock()
        registry.async_get.return_value = SimpleNamespace(config_entries={"elsewhere"})
        monkeypatch.setattr(init_mod.dr, "async_get", MagicMock(return_value=registry))

        with pytest.raises(ServiceValidationError, match="does not belong"):
            init_mod._resolve_entry_for_export(mock_hass, {"device_id": "dev"})

    def test_the_sole_loaded_entry_needs_no_argument(self, mock_hass, make_coordinator):
        entry = _entry_with(make_coordinator())
        # An entry without a coordinator (still loading) does not count.
        mock_hass.config_entries.async_entries.return_value = [FakeEntry(entry_id="x"), entry]

        assert init_mod._resolve_entry_for_export(mock_hass, {}) is entry

    def test_no_loaded_entry_is_a_validation_error(self, mock_hass):
        mock_hass.config_entries.async_entries.return_value = []

        with pytest.raises(ServiceValidationError, match="No AlphaESS config entry"):
            init_mod._resolve_entry_for_export(mock_hass, {})

    def test_several_loaded_entries_must_be_told_apart(self, mock_hass, make_coordinator):
        mock_hass.config_entries.async_entries.return_value = [
            _entry_with(make_coordinator(), "one"), _entry_with(make_coordinator(), "two"),
        ]

        with pytest.raises(ServiceValidationError, match="More than one"):
            init_mod._resolve_entry_for_export(mock_hass, {})


class TestTheServiceHandler:
    async def test_it_returns_the_snapshot_with_the_manifest_version(
        self, mock_hass, make_coordinator, monkeypatch
    ):
        coordinator = make_coordinator()
        entry = _loaded(coordinator)
        mock_hass.config_entries.async_entries.return_value = [entry]
        monkeypatch.setattr(
            init_mod, "async_get_integration",
            AsyncMock(return_value=SimpleNamespace(version="0.9.0")),
        )
        call = SimpleNamespace(hass=mock_hass, data={})

        result = await init_mod._async_service_export_raw_snapshot(call)

        assert result["integration_version"] == "0.9.0"
        assert "inverter_1" in result["inverters"]
        assert SERIAL not in json.dumps(result)

    async def test_a_loader_failure_does_not_lose_the_export(
        self, mock_hass, make_coordinator, monkeypatch
    ):
        entry = _loaded(make_coordinator())
        mock_hass.config_entries.async_entries.return_value = [entry]
        monkeypatch.setattr(
            init_mod, "async_get_integration",
            AsyncMock(side_effect=RuntimeError("no loader")),
        )
        call = SimpleNamespace(hass=mock_hass, data={})

        result = await init_mod._async_service_export_raw_snapshot(call)

        assert result["integration_version"] == "unknown"

    def test_the_schema_takes_only_the_three_selectors(self):
        schema = init_mod.SERVICE_EXPORT_RAW_SNAPSHOT_SCHEMA
        assert schema({}) == {}
        assert schema({"serial": SERIAL}) == {"serial": SERIAL}
        assert schema({"config_entry_id": "one", "device_id": "dev"}) == {
            "config_entry_id": "one", "device_id": "dev",
        }
        with pytest.raises(vol.Invalid):
            schema({"unexpected": True})

    def test_it_is_registered_as_a_response_only_service(self):
        assert init_mod.SERVICE_EXPORT_RAW_SNAPSHOT == "export_raw_snapshot"
        assert SupportsResponse.ONLY.value == "only"
        assert DOMAIN == "alphaess"
