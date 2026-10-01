"""Sensors: the unpaid-bill sensors, the role attributes automations select on, and what never reaches an attribute.

Every code, name and figure here is synthetic.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import enum
import importlib.util
import json
import logging
from pathlib import Path
import sys
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_sensor_test"
CODE, OTHER = "PB00000000001", "PB00000000002"
ENTRY_ID = "entry-one"

# Values EVN puts in a row that identify a person, a place or a device: none may be carried anywhere.
ADVERSARIAL = {
    "TEN_KHANG": "Nguyễn Văn Thử-Nghiệm", "DCHI_KHANG": "12 Phố Thử Nghiệm, Quận Giả",
    "MA_DVIQLY": "PBTESTDV", "SO_CTO": "METER-SECRET-77", "MA_DDO": "PB00000000001009",
    "KHUVUCMATDIEN": "Khu vực Giả Định", "TENPHANTU": "Trạm Giả Định 9", "LY_DO": "Sửa chữa nhà ông Thử",
    "ID_HDON": 987654321,
}


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", INTEGRATION_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _stub(name: str, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


class _Generic:
    @classmethod
    def __class_getitem__(cls, _item):
        return cls


@pytest.fixture(scope="module")
def modules():
    sys.modules[PACKAGE] = types.ModuleType(PACKAGE)
    sys.modules[PACKAGE].__path__ = [str(INTEGRATION_DIR)]

    class SensorDeviceClass(enum.Enum):
        ENERGY, MONETARY, DATE, TIMESTAMP = "energy", "monetary", "date", "timestamp"

    class SensorStateClass(enum.Enum):
        TOTAL, TOTAL_INCREASING = "total", "total_increasing"

    class SensorEntity:
        pass

    class CoordinatorEntity(_Generic):
        def __init__(self, coordinator):
            self.coordinator = coordinator

    _stub("aiohttp", ClientSession=object, ClientError=Exception, ContentTypeError=ValueError)
    _stub("homeassistant")
    _stub("homeassistant.components")
    _stub(
        "homeassistant.components.sensor", SensorDeviceClass=SensorDeviceClass, SensorEntity=SensorEntity,
        SensorStateClass=SensorStateClass,
    )
    _stub("homeassistant.config_entries", ConfigEntry=object)
    _stub("homeassistant.const", CONF_PASSWORD="password", CONF_SCAN_INTERVAL="scan_interval", UnitOfEnergy=types.SimpleNamespace(KILO_WATT_HOUR="kWh"))
    _stub("homeassistant.core", HomeAssistant=object)
    _stub("homeassistant.exceptions", ConfigEntryAuthFailed=RuntimeError)
    _stub("homeassistant.helpers")
    _stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda _hass: object())
    _stub("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    _stub(
        "homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity,
        DataUpdateCoordinator=_Generic, UpdateFailed=RuntimeError,
    )
    _stub("homeassistant.util")
    _stub("homeassistant.util.dt", now=datetime.now)
    const = _load_module("const")
    _load_module("tariff")
    calculation = _load_module("calculation")
    _load_module("models")
    api = _load_module("api")
    _load_module("pricing")
    _load_module("daily_store")
    reconcile = _load_module("reconcile")
    _load_module("statistics_import")
    _load_module("history")
    _load_module("coordinator")
    sensor = _load_module("sensor")
    return types.SimpleNamespace(const=const, calculation=calculation, api=api, reconcile=reconcile, sensor=sensor, Device=SensorDeviceClass)


def _entry(codes=(CODE, OTHER), options=None):
    return types.SimpleNamespace(
        entry_id=ENTRY_ID, data={"primary_customer_code": codes[0], "linked_customers": {code: code + "009" for code in codes}},
        options=options or {},
    )


def _entities(modules, entry, data=None):
    coordinator = types.SimpleNamespace(data=data, config_entry=entry)
    added: list = []
    asyncio.run(modules.sensor.async_setup_entry(
        types.SimpleNamespace(data={modules.const.DOMAIN: {entry.entry_id: coordinator}}), entry, added.extend,
    ))
    return added


def _meter_data(modules):
    calc = modules.calculation
    history = calc.normalize_bills([
        {"NAM": 2026, "THANG": 8, "KY": 1, "TONG_TIEN": 240000, "NGAY_TTOAN": "05/09/2026", **ADVERSARIAL, "MA_KHANG": CODE},
    ])
    unpaid = calc.normalize_bills([
        {"NAM": 2026, "THANG": 9, "KY": 1, "TONG_TIEN": 250000, "TONG_NO": 250000, "TTRANG_TTOAN": "CHUATT", "HAN_TTOAN": "15/10/2026",
         "DIEN_TTHU": 100, "NGAY_DKY": "01/09/2026", "NGAY_CKY": "30/09/2026", **ADVERSARIAL, "MA_KHANG": CODE},
    ], source="unpaid")
    bills = calc.merge_bill_sources(history, unpaid, unpaid_fresh=True)
    meter = {
        "customer_code": CODE, "current_month_consumption": 10.0, "current_month_amount": 100, "bills": bills,
        "monthly_history": bills, "daily_history": [], "unpaid_fresh": True, **calc.unpaid_summary(bills, loaded=True),
    }
    outages = calc.normalize_outages(
        [{"TGIAN_BDAU": "05/10/2026 08:00", "TGIAN_KTHUC": "05/10/2026 11:30", "TTHAI_HOAN": "D", **ADVERSARIAL, "MA_KHANG": CODE}],
        timezone(timedelta(hours=7)),
    )
    meter.update(calc.outage_summary(outages, loaded=True))
    other = {**meter, "customer_code": OTHER, "unpaid_count": 0, "unpaid_amount": 0, "next_due_date": None}
    meters = {CODE: meter, OTHER: other}
    aggregate = calc.aggregate_selected_overviews(meters, [CODE, OTHER], {})
    return {"meters": meters, "aggregate": aggregate, "partial_errors": {}}


def _by_key(entities, code=None):
    return {entity._metric: entity for entity in entities if code is None or entity._customer_code == code}


def test_each_code_and_the_total_get_the_unpaid_sensors(modules) -> None:
    entities = _entities(modules, _entry())
    per_code = _by_key(entities, CODE)
    assert {"unpaid_amount", "next_due_date", "latest_index"} <= set(per_code)
    total = _by_key(entities, "__aggregate__")
    assert {"unpaid_amount", "next_due_date"} <= set(total) and "latest_index" not in total
    assert len({entity._attr_unique_id for entity in entities}) == len(entities)


def test_the_unpaid_amount_is_money_without_a_state_class_and_the_due_date_is_a_date(modules) -> None:
    sensors = _by_key(_entities(modules, _entry()), CODE)
    amount, due = sensors["unpaid_amount"], sensors["next_due_date"]
    assert (amount._attr_device_class, amount._attr_native_unit_of_measurement, amount._attr_state_class) == (modules.Device.MONETARY, "VND", None)
    assert (due._attr_device_class, due._attr_state_class) == (modules.Device.DATE, None)


def test_states_follow_the_overview_and_the_due_date_is_a_date_object(modules) -> None:
    entities = _entities(modules, _entry(), _meter_data(modules))
    mine, other, total = _by_key(entities, CODE), _by_key(entities, OTHER), _by_key(entities, "__aggregate__")
    assert (mine["unpaid_amount"].native_value, mine["next_due_date"].native_value) == (250000, date(2026, 10, 15))
    assert (other["unpaid_amount"].native_value, other["next_due_date"].native_value) == (0, None)
    assert (total["unpaid_amount"].native_value, total["next_due_date"].native_value) == (250000, date(2026, 10, 15))
    assert mine["unpaid_amount"].extra_state_attributes["unpaid_count"] == 1
    assert mine["unpaid_amount"].extra_state_attributes["unpaid_fresh"] is True


def test_an_unusable_due_date_is_unknown(modules) -> None:
    data = _meter_data(modules)
    data["meters"][CODE]["next_due_date"] = "soon"
    assert _by_key(_entities(modules, _entry(), data), CODE)["next_due_date"].native_value is None


def test_the_unpaid_list_never_loaded_is_unknown_not_zero(modules) -> None:
    data = _meter_data(modules)
    data["meters"][CODE].update(modules.calculation.unpaid_summary([], loaded=False))
    assert _by_key(_entities(modules, _entry(), data), CODE)["unpaid_amount"].native_value is None


def test_every_sensor_says_whether_it_is_a_meter_or_the_total_and_which_entry_owns_it(modules) -> None:
    for data in (None, _meter_data(modules)):
        for entity in _entities(modules, _entry(), data):
            attrs = entity.extra_state_attributes
            assert attrs["evn_entry"] == ENTRY_ID
            assert attrs["evn_role"] == ("aggregate" if entity._customer_code == "__aggregate__" else "meter")


def test_a_second_entry_has_its_own_entry_id_so_one_meter_is_one_notice(modules) -> None:
    second = types.SimpleNamespace(entry_id="entry-two", data=_entry((OTHER,)).data, options={})
    roles = [
        (entity.extra_state_attributes["evn_entry"], entity.extra_state_attributes["evn_role"])
        for entry in (_entry(), second) for entity in _entities(modules, entry, None) if entity._metric == "unpaid_amount"
    ]
    assert roles.count(("entry-one", "meter")) == 2 and roles.count(("entry-two", "meter")) == 1
    assert roles.count(("entry-one", "aggregate")) == 1 and ("entry-two", "aggregate") not in roles


# ---------------------------------------------------------------- nothing identifying is carried

def _serialized_everything(modules) -> str:
    entities = _entities(modules, _entry(), _meter_data(modules))
    out = [json.dumps({"attrs": e.extra_state_attributes, "state": e.native_value}, default=str, ensure_ascii=False) for e in entities]
    data = _meter_data(modules)
    for code, meter in data["meters"].items():
        rows, periods, results = modules.reconcile.annotate_bills(meter["bills"], {}, 1.0)
        events, state = modules.reconcile.plan_events(ENTRY_ID, code, "Nhà", periods, results, {}, date(2026, 10, 1), 1.0)
        out.append(json.dumps([events, state, rows], default=str, ensure_ascii=False))
    return "\n".join(out)


def test_no_identity_field_reaches_an_attribute_state_event_or_stored_period(modules) -> None:
    dumped = _serialized_everything(modules)
    for name, value in ADVERSARIAL.items():
        assert str(value) not in dumped, f"{name} leaked"


def test_the_customer_code_is_only_where_the_earlier_release_already_put_it(modules) -> None:
    """The customer_code attribute is the existing contract; nothing new repeats the code."""
    entities = _entities(modules, _entry(), _meter_data(modules))
    for entity in entities:
        attrs = dict(entity.extra_state_attributes)
        attrs.pop("customer_code", None)
        attrs.pop("selected_customer_codes", None)
        attrs.pop("successful_customer_codes", None)
        assert CODE not in json.dumps(attrs, default=str), entity._metric
    data = _meter_data(modules)
    events = []
    for code, meter in data["meters"].items():
        _, periods, results = modules.reconcile.annotate_bills(meter["bills"], {}, 1.0)
        events += modules.reconcile.plan_events(ENTRY_ID, code, "Nhà", periods, results, {}, date(2026, 10, 1), 1.0)[0]
    assert CODE not in json.dumps(events)


def test_the_client_logs_and_records_no_identity_while_reading_both_lists(modules, caplog) -> None:
    state = modules.api.SessionState("user", "tok", "ref", "dev", CODE, CODE)
    client = modules.api.EvnClient(object(), state, {CODE: CODE + "009"})
    unpaid_row = {"NAM": 2026, "THANG": 9, "KY": 1, "TONG_NO": 5, "TTRANG_TTOAN": "CHUATT", "HAN_TTOAN": "15/10/2026", **ADVERSARIAL}
    answers = [{"data": [unpaid_row]}, {"data": [{**unpaid_row, "NGAY_TTOAN": "01/10/2026", "TTRANG_TTOAN": "DATT"}]}]

    async def switch(_code):
        return None

    async def request(method, url, body=None):
        return answers.pop(0)

    client._async_switch_customer, client._async_request = switch, request
    with caplog.at_level(logging.DEBUG):
        asyncio.run(client.async_unpaid_bills(CODE))
        asyncio.run(client._async_fetch_bills(CODE))
    blob = caplog.text + json.dumps(client.last_shapes, ensure_ascii=False)
    for name, value in ADVERSARIAL.items():
        assert str(value) not in blob, f"{name} leaked into a log line or the recorded shapes"
    assert CODE not in caplog.text


# ---------------------------------------------------------------- planned outage sensor

def _outage_overview(data):
    data["meters"][CODE].update({
        "next_planned_outage": "2026-10-05T08:00:00+07:00", "outage_end": "2026-10-05T11:30:00+07:00",
        "outage_status": "D", "upcoming_outage_count": 2,
        "outages": [
            {"start": "2026-10-05T08:00:00+07:00", "end": "2026-10-05T11:30:00+07:00", "status": "D"},
            {"start": "2026-10-09T08:00:00+07:00", "end": "2026-10-09T09:00:00+07:00", "status": "D"},
        ],
    })
    return data


def test_each_code_has_a_planned_outage_timestamp_sensor_and_the_total_does_not(modules) -> None:
    entities = _entities(modules, _entry())
    sensor = _by_key(entities, CODE)["next_planned_outage"]
    assert sensor._attr_device_class == modules.Device.TIMESTAMP and sensor._attr_state_class is None
    assert "next_planned_outage" not in _by_key(entities, "__aggregate__")


def test_the_state_is_an_aware_datetime_and_the_attributes_carry_only_the_times_and_the_status(modules) -> None:
    sensor = _by_key(_entities(modules, _entry(), _outage_overview(_meter_data(modules))), CODE)["next_planned_outage"]
    assert sensor.native_value == datetime(2026, 10, 5, 8, 0, tzinfo=timezone(timedelta(hours=7)))
    attrs = sensor.extra_state_attributes
    assert (attrs["end"], attrs["status"], attrs["upcoming_count"]) == ("2026-10-05T11:30:00+07:00", "D", 2)
    assert [item["status"] for item in attrs["outages"]] == ["D", "D"]
    assert (attrs["evn_role"], attrs["evn_entry"]) == ("meter", ENTRY_ID)
    assert set(attrs["outages"][0]) == {"start", "end", "status"}


def test_no_outage_is_an_unknown_state_and_the_long_list_stays_out_of_the_recorder(modules) -> None:
    data = _meter_data(modules)
    data["meters"][CODE].update(modules.calculation.outage_summary([], loaded=True))
    sensor = _by_key(_entities(modules, _entry(), data), CODE)["next_planned_outage"]
    assert sensor.native_value is None and sensor.extra_state_attributes["upcoming_count"] == 0
    assert "outages" in sensor._unrecorded_attributes
