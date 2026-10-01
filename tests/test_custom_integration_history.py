"""Daily store, statistics series and the backfill walk stay HA-free and deterministic."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_history_test"
ICT = timezone(timedelta(hours=7))


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", INTEGRATION_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_DEFAULT_MODEL: dict = {}


@pytest.fixture(scope="module")
def modules():
    sys.modules[PACKAGE] = types.ModuleType(PACKAGE)
    sys.modules[PACKAGE].__path__ = [str(INTEGRATION_DIR)]
    sys.modules.setdefault("aiohttp", types.SimpleNamespace(
        ClientSession=object, ClientError=Exception, ContentTypeError=ValueError,
    ))
    sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    sys.modules.setdefault("homeassistant.util", types.ModuleType("homeassistant.util"))
    dt = types.ModuleType("homeassistant.util.dt")
    dt.now = datetime.now
    sys.modules.setdefault("homeassistant.util.dt", dt)
    const = _load_module("const")
    _load_module("tariff")
    calculation = _load_module("calculation")
    _load_module("models")
    api = _load_module("api")
    pricing = _load_module("pricing")
    store = _load_module("daily_store")
    statistics = _load_module("statistics_import")
    history = _load_module("history")
    _DEFAULT_MODEL["model"] = pricing.PriceModel(True, "tiered", None)
    return types.SimpleNamespace(
        const=const, calculation=calculation, api=api, pricing=pricing, store=store, statistics=statistics,
        history=history,
    )


def _rows(*pairs):
    return [{"date": day, "consumption": kwh} for day, kwh in pairs]


# ---------------------------------------------------------------- daily store

def test_merge_overwrites_a_date_and_adds_new_ones(modules) -> None:
    store = modules.store.empty_store()
    assert modules.store.merge_daily(store, "PB000001", _rows(("2026-03-01", 5.0), ("2026-03-02", 6.0))) is True
    assert modules.store.merge_daily(store, "PB000001", _rows(("2026-03-02", 7.5), ("2026-03-03", 1.0))) is True
    assert modules.store.code_days(store, "PB000001") == {"2026-03-01": 5.0, "2026-03-02": 7.5, "2026-03-03": 1.0}


def test_merge_reports_no_change_for_identical_rows(modules) -> None:
    store = modules.store.empty_store()
    modules.store.merge_daily(store, "PB000001", _rows(("2026-03-01", 5.0)))
    assert modules.store.merge_daily(store, "PB000001", _rows(("2026-03-01", 5.0))) is False


def test_merge_keeps_codes_apart_and_ignores_rows_without_a_date(modules) -> None:
    store = modules.store.empty_store()
    modules.store.merge_daily(store, "PB000001", [{"date": "", "consumption": 3.0}, {"consumption": 1.0}])
    modules.store.merge_daily(store, "PB000002", _rows(("2026-03-01", 2.0)))
    assert modules.store.code_days(store, "PB000001") == {}
    assert modules.store.code_days(store, "PB000002") == {"2026-03-01": 2.0}


def test_zero_rows_that_are_not_reported_yet_are_not_stored(modules) -> None:
    store = modules.store.empty_store()
    modules.store.merge_daily(
        store, "PB000001", _rows(("2026-03-01", 0.0), ("2026-03-09", 0.0), ("2026-03-10", 0.0), ("2026-03-10", 0.0)),
        unreported_from="2026-03-09",
    )
    assert modules.store.code_days(store, "PB000001") == {"2026-03-01": 0.0}


def test_store_round_trips_through_json_and_survives_garbage(modules) -> None:
    store = modules.store.empty_store()
    modules.store.merge_daily(store, "PB000001", _rows(("2026-03-01", 5.0)))
    modules.store.backfill_meta(store, "PB000001")["empty"] = 1
    again = modules.store.normalize_store(json.loads(json.dumps(store)))
    assert again == store
    for garbage in (None, [], "x", {"daily": "x", "meta": 3}, {"daily": {"PB000001": {"bad": "x", "2026-03-01": "y"}}}):
        cleaned = modules.store.normalize_store(garbage)
        assert set(cleaned) >= {"daily", "meta", "series"}
        assert modules.store.code_days(cleaned, "PB000001") == {}


def test_non_finite_kwh_is_never_stored_or_loaded(modules) -> None:
    store = modules.store.empty_store()
    modules.store.merge_daily(store, "PB000001", _rows(("2026-03-01", float("nan")), ("2026-03-02", float("inf")), ("2026-03-03", 2.0)))
    assert modules.store.code_days(store, "PB000001") == {"2026-03-03": 2.0}
    loaded = modules.store.normalize_store({"daily": {"PB000001": {"2026-03-01": float("nan"), "2026-03-02": float("-inf"), "2026-03-03": 2.0}}})
    assert modules.store.code_days(loaded, "PB000001") == {"2026-03-03": 2.0}


def test_earliest_day_is_the_oldest_stored_date(modules) -> None:
    store = modules.store.empty_store()
    assert modules.store.earliest_day(store, "PB000001") is None
    modules.store.merge_daily(store, "PB000001", _rows(("2026-03-05", 1.0), ("2025-12-31", 2.0)))
    assert modules.store.earliest_day(store, "PB000001") == "2025-12-31"


# ------------------------------------------------------------ backfill planner

def test_backfill_walks_back_one_month_at_a_time_from_last_month(modules) -> None:
    s = modules.store
    meta = s.backfill_meta(s.empty_store(), "PB000001")
    today = date(2026, 3, 15)
    seen = []
    for _ in range(4):
        month = s.next_backfill_month(today, meta, cap=36)
        seen.append(month)
        s.record_backfill_month(meta, month, row_count=30)
    assert seen == [date(2026, 2, 1), date(2026, 1, 1), date(2025, 12, 1), date(2025, 11, 1)]


def test_backfill_stops_after_two_consecutive_empty_months(modules) -> None:
    s = modules.store
    meta = s.backfill_meta(s.empty_store(), "PB000001")
    today = date(2026, 3, 15)
    for count in (30, 0, 30, 0, 0):
        month = s.next_backfill_month(today, meta, cap=36)
        assert month is not None
        s.record_backfill_month(meta, month, row_count=count)
    assert meta["done"] is True
    assert s.next_backfill_month(today, meta, cap=36) is None


def test_backfill_respects_the_month_cap(modules) -> None:
    s = modules.store
    meta = s.backfill_meta(s.empty_store(), "PB000001")
    today = date(2026, 3, 15)
    months = []
    while (month := s.next_backfill_month(today, meta, cap=36)) is not None:
        months.append(month)
        s.record_backfill_month(meta, month, row_count=30)
    assert len(months) == 36
    assert months[0] == date(2026, 2, 1) and months[-1] == date(2023, 3, 1)


def test_backfill_does_not_advance_until_a_month_is_recorded(modules) -> None:
    s = modules.store
    meta = s.backfill_meta(s.empty_store(), "PB000001")
    today = date(2026, 3, 15)
    assert s.next_backfill_month(today, meta, 36) == s.next_backfill_month(today, meta, 36) == date(2026, 2, 1)


def test_backfill_crosses_a_year_boundary(modules) -> None:
    s = modules.store
    meta = s.backfill_meta(s.empty_store(), "PB000001")
    month = s.next_backfill_month(date(2026, 1, 20), meta, 36)
    assert month == date(2025, 12, 1)


def test_previous_month_is_refreshed_once_a_day_in_the_first_five_days(modules) -> None:
    s = modules.store
    meta = s.backfill_meta(s.empty_store(), "PB000001")
    assert s.needs_previous_month_refresh(date(2026, 3, 5), meta) is True
    assert s.needs_previous_month_refresh(date(2026, 3, 6), meta) is False
    s.mark_previous_month_refreshed(meta, date(2026, 3, 5))
    assert s.needs_previous_month_refresh(date(2026, 3, 5), meta) is False
    assert s.needs_previous_month_refresh(date(2026, 3, 1), meta) is True


# ------------------------------------------------------------ statistics series

def test_cumulative_series_sums_and_starts_at_local_midnight(modules) -> None:
    series = modules.statistics.cumulative_series({"2026-09-01": 2.5, "2026-09-02": 3.0}, ICT)
    assert [row["sum"] for row in series] == [2.5, 5.5]
    assert [row["state"] for row in series] == [2.5, 5.5]
    assert series[0]["start"].isoformat() == "2026-09-01T00:00:00+07:00"
    assert series[0]["start"].astimezone(timezone.utc).isoformat() == "2026-08-31T17:00:00+00:00"


def test_series_start_is_local_midnight_in_ho_chi_minh(modules) -> None:
    series = modules.statistics.cumulative_series({"2026-09-01": 1.0}, ZoneInfo("Asia/Ho_Chi_Minh"))
    assert series[0]["start"].isoformat() == "2026-09-01T00:00:00+07:00"


def test_a_missing_day_has_no_row_and_the_sum_carries(modules) -> None:
    series = modules.statistics.cumulative_series({"2026-03-01": 4.0, "2026-03-04": 6.0}, ICT)
    assert [row["start"].day for row in series] == [1, 4]
    assert [row["sum"] for row in series] == [4.0, 10.0]


def test_sums_do_not_drift_with_fractional_kwh(modules) -> None:
    days = {f"2026-03-{d:02d}": 0.1 for d in range(1, 31)}
    assert modules.statistics.cumulative_series(days, ICT)[-1]["sum"] == 3.0


def test_older_days_prepended_shift_every_later_sum_by_their_total(modules) -> None:
    stats = modules.statistics
    before = stats.cumulative_series({"2026-03-01": 4.0, "2026-03-02": 6.0}, ICT)
    after = stats.cumulative_series({"2026-02-27": 1.0, "2026-02-28": 2.0, "2026-03-01": 4.0, "2026-03-02": 6.0}, ICT)
    assert [row["sum"] for row in after[2:]] == [row["sum"] + 3.0 for row in before]


def _tiered(modules, kwh_by_day):
    model = modules.pricing.PriceModel(True, "tiered", None)
    return modules.statistics.daily_cost_rows(
        kwh_by_day, lambda kwh, start, end: modules.pricing.month_amount(kwh, start, end, model)
    )


def _month_total(modules, days, model=None):
    model = model or modules.pricing.PriceModel(True, "tiered", None)
    total = float(sum(Decimal(str(v)) for v in days.values()))
    first = date.fromisoformat(min(days))
    last = date(first.year, first.month, 28) + timedelta(days=4)
    last = last - timedelta(days=last.day)
    return modules.pricing.month_amount(total, first.replace(day=1), last, model)


@pytest.mark.parametrize("year_month", [(2026, 3), (2025, 5), (2024, 2)])
def test_daily_costs_of_a_month_sum_exactly_to_the_month_amount(modules, year_month) -> None:
    year, month = year_month
    days = {date(year, month, d).isoformat(): 3.5 + (d % 5) * 1.25 for d in range(1, 29)}
    costs = _tiered(modules, days)
    assert len(costs) == 28
    assert sum(costs.values()) == _month_total(modules, days)


def test_daily_costs_telescope_with_gaps_and_across_months(modules) -> None:
    days = {"2026-03-02": 40.0, "2026-03-09": 90.25, "2026-03-31": 120.5, "2026-04-01": 10.0, "2026-04-15": 210.0}
    costs = _tiered(modules, days)
    march = {k: v for k, v in days.items() if k.startswith("2026-03")}
    april = {k: v for k, v in days.items() if k.startswith("2026-04")}
    assert sum(v for k, v in costs.items() if k.startswith("2026-03")) == _month_total(modules, march)
    assert sum(v for k, v in costs.items() if k.startswith("2026-04")) == _month_total(modules, april)
    # The month resets: the first day of April is priced from zero, not from March's total.
    assert costs["2026-04-01"] == _month_total(modules, {"2026-04-01": 10.0})


def test_daily_costs_under_the_effective_price_also_telescope(modules) -> None:
    model = modules.pricing.PriceModel(False, "effective_price", Decimal("2500.5"))
    days = {f"2026-03-{d:02d}": 7.0 + d * 0.5 for d in range(1, 31)}
    costs = modules.statistics.daily_cost_rows(
        days, lambda kwh, start, end: modules.pricing.month_amount(kwh, start, end, model)
    )
    assert sum(costs.values()) == _month_total(modules, days, model)
    assert all(isinstance(cost, int) for cost in costs.values())


def test_a_month_that_cannot_be_priced_gets_no_cost_rows(modules) -> None:
    days = {"2023-10-30": 5.0, "2023-10-31": 6.0, "2023-12-01": 7.0}
    costs = _tiered(modules, days)
    assert list(costs) == ["2023-12-01"]
    series = modules.statistics.cumulative_series(costs, ICT)
    assert [row["start"].date().isoformat() for row in series] == ["2023-12-01"]


def test_aggregate_energy_outer_joins_days_and_sums_the_parts(modules) -> None:
    summed = modules.statistics.sum_by_day([
        {"2026-03-01": 1.5, "2026-03-02": 2.0}, {"2026-03-02": 0.25, "2026-03-03": 4.0},
    ])
    assert summed == {"2026-03-01": 1.5, "2026-03-02": 2.25, "2026-03-03": 4.0}


def test_statistic_ids_are_stable_and_follow_the_entity_naming(modules) -> None:
    stats = modules.statistics
    assert stats.energy_statistic_id("PB000001") == "evn_vietnam:pb000001_daily_energy"
    assert stats.cost_statistic_id("PB000001") == "evn_vietnam:pb000001_daily_cost"
    assert stats.TOTAL_ENERGY_ID == "evn_vietnam:total_daily_energy"
    assert stats.TOTAL_COST_ID == "evn_vietnam:total_daily_cost"


def _days(code_days):
    return {code: dict(days) for code, days in code_days.items()}


def _build(modules, days_by_code, *, selected=None, aliases=None, models=None):
    codes = list(days_by_code)
    models = models if models is not None else {code: modules.pricing.PriceModel(True, "tiered", None) for code in codes}
    return {
        spec.statistic_id: spec
        for spec in modules.statistics.build_series(days_by_code, models, selected or codes, aliases or {}, ICT)
    }


def test_build_series_makes_energy_and_cost_per_code_and_for_the_selection(modules) -> None:
    days = _days({
        "PB000001": {"2026-03-01": 10.0, "2026-03-02": 20.0},
        "PB000002": {"2026-03-02": 5.0, "2026-03-03": 7.0},
    })
    specs = _build(modules, days)
    assert set(specs) == {
        "evn_vietnam:pb000001_daily_energy", "evn_vietnam:pb000001_daily_cost",
        "evn_vietnam:pb000002_daily_energy", "evn_vietnam:pb000002_daily_cost",
        "evn_vietnam:total_daily_energy", "evn_vietnam:total_daily_cost",
    }
    total = specs["evn_vietnam:total_daily_energy"]
    assert [row["sum"] for row in total.rows] == [10.0, 35.0, 42.0]
    assert (total.unit, total.unit_class) == ("kWh", "energy")
    cost = specs["evn_vietnam:total_daily_cost"]
    parts = [specs[f"evn_vietnam:{c}_daily_cost"].rows[-1]["sum"] for c in ("pb000001", "pb000002")]
    assert cost.rows[-1]["sum"] == sum(parts)
    assert (cost.unit, cost.unit_class) == ("VND", None)


def test_aggregate_follows_the_selected_codes_only(modules) -> None:
    days = _days({"PB000001": {"2026-03-01": 10.0}, "PB000002": {"2026-03-01": 5.0}, "PB000003": {"2026-03-01": 1.0}})
    specs = _build(modules, days, selected=["PB000001", "PB000002"])
    assert specs["evn_vietnam:total_daily_energy"].rows[-1]["sum"] == 15.0


def test_no_aggregate_rows_for_a_single_selected_code(modules) -> None:
    specs = _build(modules, _days({"PB000001": {"2026-03-01": 10.0}}))
    assert specs["evn_vietnam:total_daily_energy"].rows == []
    assert specs["evn_vietnam:total_daily_cost"].rows == []


def test_cost_uses_the_price_model_of_each_code(modules) -> None:
    days = _days({"PB000001": {"2026-03-01": 100.0}})
    tiered = _build(modules, days, models={"PB000001": modules.pricing.PriceModel(True, "tiered", None)})
    effective = _build(modules, days, models={"PB000001": modules.pricing.PriceModel(False, "effective_price", Decimal(3000))})
    key = "evn_vietnam:pb000001_daily_cost"
    assert tiered[key].rows[0]["sum"] == modules.calculation.calculate_bill_amount(100, date(2026, 3, 1), date(2026, 3, 31))
    assert effective[key].rows[0]["sum"] == 300000


def test_names_use_the_nickname_or_the_masked_last_four_never_the_full_code(modules) -> None:
    days = _days({"PB012345678": {"2026-03-01": 1.0}, "PB098765432": {"2026-03-01": 1.0}})
    specs = _build(modules, days, aliases={"PB012345678": "Nhà chính"})
    assert specs["evn_vietnam:pb012345678_daily_energy"].name == "EVN Nhà chính daily energy"
    assert specs["evn_vietnam:pb098765432_daily_cost"].name == "EVN …5432 daily cost (estimate)"
    assert specs["evn_vietnam:total_daily_energy"].name == "EVN total daily energy"
    for spec in specs.values():
        assert "PB0" not in spec.name and "pb0" not in spec.name.lower()


def _spec(modules, name, days, scope=""):
    stats = modules.statistics
    return stats.SeriesSpec(f"evn_vietnam:{name}", name, "kWh", "energy", stats.cumulative_series(days, ICT), scope)


def test_series_to_clear_lists_series_that_start_later_lose_rows_change_scope_or_vanished(modules) -> None:
    stats = modules.statistics

    def old(start, count, scope=""):
        return {"start": start, "count": count, "scope": scope}

    specs = [
        _spec(modules, "later", {"2026-03-05": 1.0}),
        _spec(modules, "same", {"2026-03-01": 1.0}),
        stats.SeriesSpec("evn_vietnam:gone", "gone", "kWh", "energy", []),
        stats.SeriesSpec("evn_vietnam:never", "never", "kWh", "energy", []),
        _spec(modules, "new", {"2026-03-01": 1.0}),
        _spec(modules, "lost_middle", {"2026-03-01": 1.0, "2026-03-03": 1.0}),
        _spec(modules, "grown", {"2026-03-01": 1.0, "2026-03-02": 1.0}),
        _spec(modules, "rescoped", {"2026-03-01": 1.0}, scope="a,c"),
    ]
    imported = {
        "evn_vietnam:later": old("2026-03-01", 1), "evn_vietnam:same": old("2026-03-01", 1),
        "evn_vietnam:gone": old("2026-03-01", 1), "evn_vietnam:lost_middle": old("2026-03-01", 3),
        "evn_vietnam:grown": old("2026-03-01", 1), "evn_vietnam:rescoped": old("2026-03-01", 1, "a,b"),
    }
    assert stats.series_to_clear(specs, imported) == [
        "evn_vietnam:later", "evn_vietnam:gone", "evn_vietnam:lost_middle", "evn_vietnam:rescoped",
    ]


def test_a_total_that_loses_a_day_when_the_selection_shrinks_is_cleared(modules) -> None:
    """Deselecting the only code with a given day must not leave that day's old row behind."""
    stats = modules.statistics
    days = {
        "PB000001": {"2026-03-01": 1.0, "2026-03-03": 3.0}, "PB000002": {"2026-03-02": 5.0},
        "PB000003": {"2026-03-01": 2.0, "2026-03-03": 1.0},
    }
    models = {code: modules.pricing.PriceModel(True, "tiered", None) for code in days}
    wide = {s.statistic_id: s for s in stats.build_series(days, models, list(days), {}, ICT)}
    narrow = stats.build_series(days, models, ["PB000001", "PB000003"], {}, ICT)
    recorded = {sid: {"start": spec.rows[0]["start"].date().isoformat(), "count": len(spec.rows), "scope": spec.scope}
                for sid, spec in wide.items() if spec.rows}
    assert "evn_vietnam:total_daily_energy" in stats.series_to_clear(narrow, recorded)
    assert "evn_vietnam:pb000001_daily_energy" not in stats.series_to_clear(narrow, recorded)


def test_a_code_without_a_known_price_model_gets_no_cost_series_and_no_total_cost(modules) -> None:
    days = _days({"PB000001": {"2026-03-01": 10.0}, "PB000002": {"2026-03-01": 5.0}})
    specs = _build(modules, days, models={"PB000001": modules.pricing.PriceModel(True, "tiered", None)})
    assert "evn_vietnam:pb000002_daily_cost" not in specs
    assert "evn_vietnam:pb000001_daily_cost" in specs
    assert "evn_vietnam:total_daily_cost" not in specs, "a partial total would look complete"
    assert specs["evn_vietnam:total_daily_energy"].rows[-1]["sum"] == 15.0


# --------------------------------------------------------------- DailyHistory

class _FakeStore:
    def __init__(self, loaded=None):
        self.loaded, self.saved, self.delayed = loaded, [], 0

    async def async_load(self):
        return self.loaded

    async def async_save(self, data):
        self.saved.append(json.loads(json.dumps(data)))

    def async_delay_save(self, data_func, _delay):
        self.delayed += 1
        self.saved.append(json.loads(json.dumps(data_func())))


class _FakeClient:
    def __init__(self, api, months=None, fail_months=(), fail_codes=()):
        self.api, self.months, self.fail_months, self.calls = api, months or {}, set(fail_months), []
        self.fail_codes = set(fail_codes)

    async def async_daily(self, code, start, end):
        self.calls.append((code, start, end))
        if code in self.fail_codes or (start.year, start.month) in self.fail_months:
            raise self.api.EvnApiError("HTTP 500", status=500)
        return list(self.months.get((code, start.year, start.month), []))


def _history(modules, *, client=None, store=None, today=date(2026, 3, 15), now=None, importer=None, **kwargs):
    client = client or _FakeClient(modules.api)
    store = store or _FakeStore()
    imported: list = []
    sleeps: list[float] = []
    clock = {"now": now or datetime.combine(today, time(12, 0), tzinfo=ICT)}

    async def default_importer(specs, to_clear):
        imported.append(([spec.statistic_id for spec in specs], list(to_clear), {spec.statistic_id: spec for spec in specs}))

    async def sleep(seconds):
        sleeps.append(seconds)

    history = modules.history.DailyHistory(
        client=client, store=store, importer=importer or default_importer, tz_provider=lambda: ICT,
        now_provider=lambda: clock["now"], sleep=sleep, **kwargs,
    )
    return history, types.SimpleNamespace(client=client, store=store, imported=imported, sleeps=sleeps, clock=clock)


def _meter(*pairs, model=None):
    return {"daily_history": _rows(*pairs), "price_model": model or _DEFAULT_MODEL["model"]}


def _update(history, meters, codes=None, selected=None, aliases=None, allow_backfill=False):
    codes = codes or list(meters)
    return asyncio.run(history.async_update(
        meters=meters, codes=codes, selected=selected or codes, aliases=aliases or {}, allow_backfill=allow_backfill,
    ))


def test_update_merges_the_live_rows_and_imports_every_series(modules) -> None:
    history, seen = _history(modules)
    _update(history, {"PB000001": _meter(("2026-03-01", 10.0), ("2026-03-02", 20.0))})
    ids, to_clear, specs = seen.imported[0]
    assert "evn_vietnam:pb000001_daily_energy" in ids and to_clear == []
    assert [row["sum"] for row in specs["evn_vietnam:pb000001_daily_energy"].rows] == [10.0, 30.0]
    assert seen.store.saved, "a changed store is saved"


def test_unchanged_data_is_not_imported_twice(modules) -> None:
    history, seen = _history(modules)
    meters = {"PB000001": _meter(("2026-03-01", 10.0))}
    _update(history, meters)
    _update(history, meters)
    assert len(seen.imported) == 1
    meters["PB000001"] = _meter(("2026-03-01", 10.0), ("2026-03-02", 4.0))
    _update(history, meters)
    assert len(seen.imported) == 2
    assert seen.imported[1][0] == ["evn_vietnam:pb000001_daily_energy", "evn_vietnam:pb000001_daily_cost"]


def test_zero_rows_of_the_last_two_days_are_treated_as_not_reported(modules) -> None:
    history, seen = _history(modules, today=date(2026, 3, 15))
    _update(history, {"PB000001": _meter(("2026-03-12", 5.0), ("2026-03-14", 0.0), ("2026-03-15", 0.0))})
    days = history.days("PB000001")
    assert days == {"2026-03-12": 5.0}


def test_a_storage_or_statistics_failure_never_breaks_the_update(modules) -> None:
    async def failing_importer(specs, to_clear):
        raise RuntimeError("recorder unavailable")

    history, _ = _history(modules, importer=failing_importer)
    _update(history, {"PB000001": _meter(("2026-03-01", 10.0))})  # does not raise
    # The failed import is retried next cycle.
    retried: list = []

    async def working(specs, to_clear):
        retried.append(len(specs))

    history._importer = working
    _update(history, {"PB000001": _meter(("2026-03-01", 10.0))})
    assert retried


def test_series_that_start_later_than_before_are_cleared_before_the_import(modules) -> None:
    store = _FakeStore(loaded={
        "daily": {"PB000001": {"2026-03-05": 1.0}}, "meta": {},
        "series": {"evn_vietnam:pb000001_daily_energy": {"start": "2026-03-01", "count": 1, "scope": ""}},
    })
    history, seen = _history(modules, store=store)
    _update(history, {"PB000001": _meter(("2026-03-06", 1.0))})
    assert "evn_vietnam:pb000001_daily_energy" in seen.imported[0][1]


def test_backfill_handles_one_code_per_cycle_and_a_bounded_number_of_months(modules) -> None:
    client = _FakeClient(modules.api, months={
        ("PB000001", 2026, 2): _rows(("2026-02-10", 3.0)),
        ("PB000001", 2026, 1): _rows(("2026-01-10", 3.0)),
    })
    history, seen = _history(modules, client=client, months_per_cycle=2)
    meters = {"PB000001": _meter(("2026-03-01", 1.0)), "PB000002": _meter(("2026-03-01", 1.0))}
    _update(history, meters, allow_backfill=True)
    assert [(code, start.isoformat(), end.isoformat()) for code, start, end in client.calls] == [
        ("PB000001", "2026-02-01", "2026-02-28"), ("PB000001", "2026-01-01", "2026-01-31"),
    ]
    assert seen.sleeps == [modules.history.BACKFILL_PAUSE_SECONDS] * 2
    assert modules.history.BACKFILL_PAUSE_SECONDS >= 2
    assert history.days("PB000001")["2026-02-10"] == 3.0


def test_backfill_moves_to_the_next_code_once_one_is_done(modules) -> None:
    client = _FakeClient(modules.api)   # every month empty
    history, _ = _history(modules, client=client, months_per_cycle=6)
    meters = {"PB000001": _meter(("2026-03-01", 1.0)), "PB000002": _meter(("2026-03-01", 1.0))}
    _update(history, meters, allow_backfill=True)
    assert {call[0] for call in client.calls} == {"PB000001"}
    assert history.backfill_status()["PB000001"]["done"] is True
    client.calls.clear()
    _update(history, meters, allow_backfill=True)
    assert {call[0] for call in client.calls} == {"PB000002"}


def test_backfill_stops_on_an_api_error_and_resumes_at_the_same_month(modules) -> None:
    client = _FakeClient(modules.api, months={("PB000001", 2026, 2): _rows(("2026-02-10", 3.0))}, fail_months=[(2026, 1)])
    history, _ = _history(modules, client=client, months_per_cycle=6)
    meters = {"PB000001": _meter(("2026-03-01", 1.0))}
    _update(history, meters, allow_backfill=True)
    assert [c[1].month for c in client.calls] == [2, 1]
    assert history.backfill_status()["PB000001"]["done"] is False
    client.fail_months.clear()
    client.calls.clear()
    _update(history, meters, allow_backfill=True)
    assert client.calls[0][1] == date(2026, 1, 1)


def test_backfill_is_skipped_unless_allowed(modules) -> None:
    history, seen = _history(modules)
    _update(history, {"PB000001": _meter(("2026-03-01", 1.0))}, allow_backfill=False)
    assert seen.client.calls == []


def test_backfill_status_reports_the_earliest_stored_day(modules) -> None:
    client = _FakeClient(modules.api, months={("PB000001", 2026, 2): _rows(("2026-02-03", 3.0))})
    history, _ = _history(modules, client=client, months_per_cycle=1)
    _update(history, {"PB000001": _meter(("2026-03-01", 1.0))}, allow_backfill=True)
    assert history.backfill_status()["PB000001"]["earliest"] == "2026-02-03"


def test_previous_month_is_refetched_once_a_day_early_in_the_month(modules) -> None:
    client = _FakeClient(modules.api, months={("PB000001", 2026, 2): _rows(("2026-02-28", 9.0))})
    history, _ = _history(modules, client=client, today=date(2026, 3, 2), months_per_cycle=0)
    meters = {"PB000001": _meter(("2026-03-01", 1.0))}
    _update(history, meters)
    assert client.calls == [], "the first refresh after a restart makes no extra requests"
    _update(history, meters, allow_backfill=True)
    assert [(c[1], c[2]) for c in client.calls] == [(date(2026, 2, 1), date(2026, 2, 28))]
    _update(history, meters, allow_backfill=True)
    assert len(client.calls) == 1
    assert history.days("PB000001")["2026-02-28"] == 9.0


def test_no_previous_month_refetch_later_in_the_month(modules) -> None:
    history, seen = _history(modules, today=date(2026, 3, 6))
    _update(history, {"PB000001": _meter(("2026-03-01", 1.0))})
    assert seen.client.calls == []


def test_flush_saves_the_store(modules) -> None:
    history, seen = _history(modules)
    _update(history, {"PB000001": _meter(("2026-03-01", 1.0))})
    seen.store.saved.clear()
    asyncio.run(history.async_flush())
    assert seen.store.saved and seen.store.saved[-1]["daily"]["PB000001"] == {"2026-03-01": 1.0}


def test_the_previous_price_model_is_kept_for_a_code_that_failed_this_cycle(modules) -> None:
    history, seen = _history(modules)
    model = modules.pricing.PriceModel(False, "effective_price", Decimal(3000))
    _update(history, {"PB000001": _meter(("2026-03-01", 100.0), model=model)})
    _update(history, {"PB000001": _meter(("2026-03-01", 100.0), model=model)}, codes=["PB000001"])
    # Next cycle the live call failed: no live rows, no model, but the stored days keep their price.
    _update(history, {}, codes=["PB000001"])
    assert len(seen.imported) == 1


def test_aliases_rename_a_series_without_touching_its_rows(modules) -> None:
    history, seen = _history(modules)
    meters = {"PB000001": _meter(("2026-03-01", 100.0))}
    _update(history, meters)
    _update(history, meters, aliases={"PB000001": "Nhà chính"})
    assert len(seen.imported) == 2
    assert seen.imported[1][2]["evn_vietnam:pb000001_daily_energy"].name == "EVN Nhà chính daily energy"


# ------------------------------------------------------- Home Assistant glue (stubbed)

def _recorder_stubs(monkeypatch, *, with_mean_type, with_unit_class=True):
    added, cleared = [], []

    class Instance:
        def async_clear_statistics(self, ids):
            cleared.append(list(ids))

    recorder = types.ModuleType("homeassistant.components.recorder")
    recorder.get_instance = lambda _hass: Instance()
    statistics = types.ModuleType("homeassistant.components.recorder.statistics")
    statistics.async_add_external_statistics = lambda _hass, metadata, rows: added.append((metadata, rows))
    models = types.ModuleType("homeassistant.components.recorder.models")
    fields = {"has_sum": bool, "name": str, "source": str, "statistic_id": str, "unit_of_measurement": str}
    fields["mean_type" if with_mean_type else "has_mean"] = object
    if with_unit_class:
        fields["unit_class"] = object
    models.StatisticMetaData = type("StatisticMetaData", (), {"__annotations__": fields})
    if with_mean_type:
        models.StatisticMeanType = types.SimpleNamespace(NONE="none")
    for name, module in (
        ("homeassistant.components", types.ModuleType("homeassistant.components")),
        ("homeassistant.components.recorder", recorder),
        ("homeassistant.components.recorder.statistics", statistics),
        ("homeassistant.components.recorder.models", models),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return added, cleared


def _one_spec(modules):
    return modules.statistics.SeriesSpec(
        "evn_vietnam:pb000001_daily_energy", "EVN …0001 daily energy", "kWh", "energy",
        modules.statistics.cumulative_series({"2026-03-01": 2.0, "2026-03-02": 3.0}, ICT),
    )


def test_import_passes_external_statistics_metadata_and_clears_first(modules, monkeypatch) -> None:
    added, cleared = _recorder_stubs(monkeypatch, with_mean_type=True)
    asyncio.run(modules.statistics.async_import_series(object(), [_one_spec(modules)], ["evn_vietnam:old"]))
    metadata, rows = added[0]
    assert cleared == [["evn_vietnam:old"]]
    assert metadata == {
        "mean_type": "none", "has_sum": True, "name": "EVN …0001 daily energy", "source": "evn_vietnam",
        "statistic_id": "evn_vietnam:pb000001_daily_energy", "unit_of_measurement": "kWh", "unit_class": "energy",
    }
    assert [(row["sum"], row["state"]) for row in rows] == [(2.0, 2.0), (5.0, 5.0)]
    assert all(row["start"].tzinfo is not None and row["start"].minute == 0 for row in rows)


def test_import_sends_only_the_metadata_keys_this_home_assistant_knows(modules, monkeypatch) -> None:
    """HA before 2025.11 has no unit_class column and before 2025.4 no mean_type: sending them breaks the import."""
    added, _ = _recorder_stubs(monkeypatch, with_mean_type=False, with_unit_class=False)
    asyncio.run(modules.statistics.async_import_series(object(), [_one_spec(modules)], []))
    metadata = added[0][0]
    assert metadata["has_mean"] is False
    assert "mean_type" not in metadata and "unit_class" not in metadata


def test_import_without_the_metadata_type_sends_the_oldest_safe_keys(modules, monkeypatch) -> None:
    added, _ = _recorder_stubs(monkeypatch, with_mean_type=False, with_unit_class=False)
    del sys.modules["homeassistant.components.recorder.models"].StatisticMetaData
    asyncio.run(modules.statistics.async_import_series(object(), [_one_spec(modules)], []))
    assert "unit_class" not in added[0][0] and "mean_type" not in added[0][0]


def test_create_uses_a_per_entry_store_key_and_the_ha_time_zone(modules, monkeypatch) -> None:
    created = []

    class Store:
        def __init__(self, hass, version, key):
            created.append((version, key))

    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = Store
    dt = types.ModuleType("homeassistant.util.dt")
    dt.DEFAULT_TIME_ZONE = ICT
    dt.now = lambda: datetime(2026, 3, 15, 9, 0, tzinfo=ICT)
    util = types.ModuleType("homeassistant.util")
    util.dt = dt
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.storage", storage)
    monkeypatch.setitem(sys.modules, "homeassistant.util", util)
    monkeypatch.setitem(sys.modules, "homeassistant.util.dt", dt)
    history = modules.history.create_daily_history(object(), "entry-1", object())
    assert created == [(1, "evn_vietnam.daily.entry-1")]
    assert history._tz() is ICT and history.today() == date(2026, 3, 15)


def test_a_code_that_keeps_failing_does_not_starve_the_others(modules) -> None:
    client = _FakeClient(modules.api, fail_codes=["PB000001"])
    history, _ = _history(modules, client=client, months_per_cycle=6)
    meters = {"PB000001": _meter(("2026-03-01", 1.0)), "PB000002": _meter(("2026-03-01", 1.0))}
    _update(history, meters, allow_backfill=True)
    assert [c[0] for c in client.calls] == ["PB000001"], "one request, then the cycle stops to protect EVN"
    client.calls.clear()
    _update(history, meters, allow_backfill=True)
    assert client.calls[0][0] == "PB000002", "the code that failed goes behind the others next cycle"
    assert history.backfill_status()["PB000002"]["done"] is True
    client.calls.clear()
    _update(history, meters, allow_backfill=True)
    assert [c[0] for c in client.calls] == ["PB000001"], "the failing code is still retried when nothing else is left"


def test_a_success_resets_the_failure_count(modules) -> None:
    client = _FakeClient(modules.api, months={("PB000001", 2026, 2): _rows(("2026-02-10", 3.0))}, fail_months=[(2026, 1)])
    history, _ = _history(modules, client=client, months_per_cycle=6)
    meters = {"PB000001": _meter(("2026-03-01", 1.0))}
    _update(history, meters, allow_backfill=True)
    assert history._data["meta"]["PB000001"]["failures"] == 1
    client.fail_months.clear()
    _update(history, meters, allow_backfill=True)
    assert history._data["meta"]["PB000001"]["failures"] == 0


def test_previous_month_refresh_does_not_starve_later_codes_either(modules) -> None:
    client = _FakeClient(modules.api, months={("PB000002", 2026, 2): _rows(("2026-02-28", 9.0))}, fail_codes=["PB000001"])
    history, _ = _history(modules, client=client, today=date(2026, 3, 2))
    meters = {"PB000001": _meter(("2026-03-01", 1.0)), "PB000002": _meter(("2026-03-01", 1.0))}
    _update(history, meters, allow_backfill=True)
    _update(history, meters, allow_backfill=True)
    assert history.days("PB000002").get("2026-02-28") == 9.0


def test_a_series_that_was_cleared_and_comes_back_is_imported_again(modules) -> None:
    history, seen = _history(modules)
    meters = {"PB000001": _meter(("2026-03-01", 1.0)), "PB000002": _meter(("2026-03-01", 2.0))}
    total = "evn_vietnam:total_daily_energy"
    _update(history, meters, selected=["PB000001", "PB000002"])
    assert total in seen.imported[-1][0]
    _update(history, meters, selected=["PB000001"])
    assert total in seen.imported[-1][1]
    _update(history, meters, selected=["PB000001", "PB000002"])
    assert total in seen.imported[-1][0], "the same rows must be imported again after a clear"


def test_a_total_losing_a_day_on_a_smaller_selection_is_cleared_before_the_import(modules) -> None:
    history, seen = _history(modules)
    meters = {
        "PB000001": _meter(("2026-03-01", 1.0), ("2026-03-03", 3.0)),
        "PB000002": _meter(("2026-03-02", 5.0)),
        "PB000003": _meter(("2026-03-01", 2.0), ("2026-03-03", 1.0)),
    }
    _update(history, meters, selected=list(meters))
    _update(history, meters, selected=["PB000001", "PB000003"])
    _, cleared, specs = seen.imported[-1]
    assert "evn_vietnam:total_daily_energy" in cleared
    assert [row["sum"] for row in specs["evn_vietnam:total_daily_energy"].rows] == [3.0, 7.0]


def test_malformed_backfill_meta_is_repaired_on_load(modules) -> None:
    raw = {"meta": {"PB000001": {"cursor": "bad", "empty": "x", "done": "yes", "prev_refresh": "no", "failures": -3}}}
    meta = modules.store.normalize_store(raw)["meta"]["PB000001"]
    assert meta == {"cursor": None, "empty": 0, "done": True, "prev_refresh": "", "failures": 0, "tail_try": ""}
    assert modules.store.next_backfill_month(date(2026, 3, 15), {**meta, "done": False}, 36) == date(2026, 2, 1)
    good = {"meta": {"PB000001": {"cursor": "2025-12", "empty": 1, "done": False, "prev_refresh": "2026-03-02", "failures": 2}}}
    assert modules.store.normalize_store(good)["meta"]["PB000001"] == {**good["meta"]["PB000001"], "tail_try": ""}


# ------------------------------------------------- rolling window and month tail

def _september_store(*, last_day=None, prev_refresh=""):
    """A store holding 2026-09-01..29 (and optionally the 30th) for one placeholder code."""
    days = {f"2026-09-{day:02d}": 5.0 for day in range(1, 30)}
    if last_day is not None:
        days["2026-09-30"] = last_day
    meta = {"cursor": None, "empty": 0, "done": True, "prev_refresh": prev_refresh, "failures": 0}
    return _FakeStore({"daily": {"PB000001": days}, "meta": {"PB000001": meta}, "series": {}})


def test_tail_is_wanted_while_the_last_day_of_last_month_is_absent_or_zero(modules) -> None:
    s = modules.store
    meta = s.backfill_meta(s.empty_store(), "PB000001")
    day1 = datetime(2026, 10, 1, 12, 0, tzinfo=ICT)
    assert s.needs_previous_month_tail(day1, meta, {}) is True
    assert s.needs_previous_month_tail(day1, meta, {"2026-09-30": 0.0}) is True, "a stored zero is provisional"
    assert s.needs_previous_month_tail(day1, meta, {"2026-09-30": 4.0}) is False
    assert s.needs_previous_month_tail(datetime(2026, 10, 10, 23, 59, tzinfo=ICT), meta, {}) is True
    assert s.needs_previous_month_tail(datetime(2026, 10, 11, 0, 5, tzinfo=ICT), meta, {}) is False
    assert s.needs_previous_month_tail(datetime(2026, 10, 11, 0, 5, tzinfo=ICT), meta, {"2026-09-30": 0.0}) is False


def test_tail_waits_three_hours_between_attempts(modules) -> None:
    s = modules.store
    meta = s.backfill_meta(s.empty_store(), "PB000001")
    now = datetime(2026, 10, 2, 12, 0, tzinfo=ICT)
    s.mark_tail_tried(meta, now)
    assert s.needs_previous_month_tail(now + timedelta(hours=1), meta, {}) is False
    assert s.needs_previous_month_tail(now + timedelta(hours=2, minutes=59), meta, {}) is False
    assert s.needs_previous_month_tail(now + timedelta(hours=3), meta, {}) is True


def test_a_bad_tail_try_is_dropped_on_load(modules) -> None:
    s = modules.store
    for bad in ("yesterday", 5, "2026-10-01T12:00:00", None):
        loaded = s.normalize_store({"meta": {"PB000001": {"tail_try": bad}}})
        assert loaded["meta"]["PB000001"]["tail_try"] == ""
    good = "2026-10-01T12:00:00+07:00"
    assert s.normalize_store({"meta": {"PB000001": {"tail_try": good}}})["meta"]["PB000001"]["tail_try"] == good


def test_window_rows_cover_the_last_days_in_ascending_order(modules) -> None:
    rows = modules.store.window_rows({"2026-09-02": 1.5, "2026-09-30": 2.0, "2026-08-31": 9.0}, date(2026, 10, 1), 31)
    assert [row["date"] for row in rows] == ["2026-09-02", "2026-09-30"], "2026-08-31 is 31 days back: outside"
    assert rows[0] == {
        "date": "2026-09-02", "day": "2026-09-02", "consumption": 1.5, "kwh": 1.5,
        "start_index": "-", "end_index": "-", "meter_point": "", "meter_number": "",
    }


def test_day_one_window_crosses_the_month_and_yesterday_is_unknown_until_it_is_published(modules) -> None:
    client = _FakeClient(modules.api, months={("PB000001", 2026, 9): _rows(("2026-09-29", 5.0), ("2026-09-30", 0.0))})
    history, seen = _history(modules, client=client, store=_september_store(), today=date(2026, 10, 1), months_per_cycle=0)
    live = _rows(("2026-10-01", 0.0))
    _update(history, {"PB000001": _meter(*[(r["date"], r["consumption"]) for r in live])}, allow_backfill=True)
    rows = history.compose("PB000001", live)
    assert rows[0]["date"] == "2026-09-01" and rows[-1]["date"] == "2026-10-01" and len(rows) == 30
    today_kwh, yesterday_kwh = modules.store.day_values(rows, date(2026, 10, 1))
    assert (today_kwh, yesterday_kwh) == (0.0, None), "never a fake 0 for a day EVN has not published"
    # EVN publishes 30/09 a few hours later; the tail retry fetches it.
    client.months[("PB000001", 2026, 9)] = _rows(("2026-09-29", 5.0), ("2026-09-30", 4.25))
    seen.clock["now"] += timedelta(hours=3)
    _update(history, {"PB000001": _meter(("2026-10-01", 0.0))}, allow_backfill=True)
    assert modules.store.day_values(history.compose("PB000001", live), date(2026, 10, 1))[1] == 4.25


def test_tail_retry_cadence_and_stop(modules) -> None:
    client = _FakeClient(modules.api, months={("PB000001", 2026, 9): _rows(("2026-09-29", 5.0))})
    history, seen = _history(
        modules, client=client, store=_september_store(prev_refresh="2026-10-04"), today=date(2026, 10, 4),
        months_per_cycle=0,
    )
    meters = {"PB000001": _meter(("2026-10-01", 1.0))}
    _update(history, meters, allow_backfill=True)
    assert len(client.calls) == 1, "absent last day on day 4: fetch"
    seen.clock["now"] += timedelta(hours=1)
    _update(history, meters, allow_backfill=True)
    assert len(client.calls) == 1, "+1 h: no fetch"
    seen.clock["now"] += timedelta(hours=2)
    _update(history, meters, allow_backfill=True)
    assert len(client.calls) == 2, "+3 h: fetch"
    assert [(c[1], c[2]) for c in client.calls] == [(date(2026, 9, 1), date(2026, 9, 30))] * 2


def test_a_stored_zero_is_retried_until_a_non_zero_arrives_and_a_genuine_zero_stops_on_day_eleven(modules) -> None:
    client = _FakeClient(modules.api, months={("PB000001", 2026, 9): _rows(("2026-09-30", 0.0))})
    history, seen = _history(
        modules, client=client, store=_september_store(last_day=0.0, prev_refresh="2026-10-04"),
        today=date(2026, 10, 4), months_per_cycle=0,
    )
    meters = {"PB000001": _meter(("2026-10-01", 1.0))}
    _update(history, meters, allow_backfill=True)
    assert len(client.calls) == 1, "stored 0 on day 4: fetch"
    client.months[("PB000001", 2026, 9)] = _rows(("2026-09-30", 3.5))
    seen.clock["now"] += timedelta(hours=3)
    _update(history, meters, allow_backfill=True)
    assert history.days("PB000001")["2026-09-30"] == 3.5, "a later non-zero overwrites the provisional zero"
    seen.clock["now"] += timedelta(hours=3)
    _update(history, meters, allow_backfill=True)
    assert len(client.calls) == 2, "non-zero present: no fetch"

    quiet_client = _FakeClient(modules.api)
    history, seen = _history(
        modules, client=quiet_client, store=_september_store(last_day=0.0, prev_refresh="2026-10-11"),
        today=date(2026, 10, 11), months_per_cycle=0,
    )
    _update(history, meters, allow_backfill=True)
    assert quiet_client.calls == [], "genuine zero still 0 on day 11: no fetch"


def test_tail_retry_is_not_made_on_the_first_refresh_after_a_restart(modules) -> None:
    history, seen = _history(modules, store=_september_store(), today=date(2026, 10, 1), months_per_cycle=0)
    _update(history, {"PB000001": _meter(("2026-10-01", 0.0))})
    assert seen.client.calls == []


def test_the_once_a_day_pass_on_days_one_to_five_is_unchanged(modules) -> None:
    client = _FakeClient(modules.api, months={("PB000001", 2026, 9): _rows(("2026-09-30", 4.0))})
    history, seen = _history(modules, client=client, store=_september_store(), today=date(2026, 10, 2), months_per_cycle=0)
    meters = {"PB000001": _meter(("2026-10-01", 1.0))}
    _update(history, meters, allow_backfill=True)
    _update(history, meters, allow_backfill=True)
    assert len(client.calls) == 1
    seen.clock["now"] += timedelta(days=1)
    _update(history, meters, allow_backfill=True)
    assert len(client.calls) == 2, "a new day: one more pass even though 30/09 is already stored"


@pytest.mark.parametrize("now, month_start, month_end, absent_day", [
    (datetime(2027, 1, 1, 0, 30, tzinfo=ICT), date(2026, 12, 1), date(2026, 12, 31), "2026-12-31"),
    (datetime(2028, 3, 1, 9, 0, tzinfo=ICT), date(2028, 2, 1), date(2028, 2, 29), "2028-02-29"),
    (datetime(2026, 10, 1, 0, 30, tzinfo=ICT), date(2026, 9, 1), date(2026, 9, 30), "2026-09-30"),
])
def test_the_ha_local_clock_picks_the_month_across_year_end_leap_day_and_a_utc_host(
    modules, now, month_start, month_end, absent_day,
) -> None:
    """00:30 in Ho Chi Minh is still the previous day in UTC; the window follows the HA-local date."""
    client = _FakeClient(modules.api)
    history, _ = _history(modules, client=client, now=now, months_per_cycle=0)
    _update(history, {"PB000001": _meter((absent_day[:8] + "01", 1.0))}, allow_backfill=True)
    assert [(c[1], c[2]) for c in client.calls] == [(month_start, month_end)]


def test_year_change_between_two_polls(modules) -> None:
    client = _FakeClient(modules.api)
    history, seen = _history(modules, client=client, now=datetime(2026, 12, 31, 23, 30, tzinfo=ICT), months_per_cycle=0)
    meters = {"PB000001": _meter(("2026-12-31", 1.0))}
    _update(history, meters, allow_backfill=True)
    assert client.calls == [], "31/12 is not early in a month"
    seen.clock["now"] = datetime(2027, 1, 1, 0, 30, tzinfo=ICT)
    _update(history, meters, allow_backfill=True)
    assert [(c[1], c[2]) for c in client.calls] == [(date(2026, 12, 1), date(2026, 12, 31))]


def test_live_rows_override_the_store_for_the_same_date(modules) -> None:
    history, _ = _history(modules, store=_september_store(last_day=2.0), today=date(2026, 10, 1))
    _update(history, {"PB000001": _meter(("2026-10-01", 0.0))})
    rows = history.compose("PB000001", _rows(("2026-09-30", 7.5), ("2026-10-01", 1.0)))
    by_date = {row["date"]: row["consumption"] for row in rows}
    assert by_date["2026-09-30"] == 7.5 and by_date["2026-10-01"] == 1.0 and by_date["2026-09-29"] == 5.0
    assert [row["date"] for row in rows] == sorted(by_date)


def test_compose_is_unavailable_until_the_store_is_loaded(modules) -> None:
    history, _ = _history(modules)
    assert history.available is False
    _update(history, {"PB000001": _meter(("2026-03-01", 1.0))})
    assert history.available is True


def test_aggregate_daily_history_across_the_boundary_is_the_per_date_sum(modules) -> None:
    first, second = "PB000001", "PB000002"
    meters = {}
    for code, offset in ((first, 0.0), (second, 1.0)):
        days = {"2026-09-29": 5.0 + offset, "2026-09-30": 4.0 + offset}
        rows = modules.store.window_rows(days, date(2026, 10, 1), 31)
        meters[code] = {"daily_history": rows, "bills": [], "today_consumption": 0.0}
    aggregate = modules.calculation.aggregate_selected_overviews(meters, [first, second, "PB000003"], {})
    assert [(r["date"], r["consumption"]) for r in aggregate["daily_history"]] == [
        ("2026-09-29", 11.0), ("2026-09-30", 9.0),
    ]
    assert aggregate["successful_customer_codes"] == [first, second], "a code missing from meters follows the partial rule"


def test_aggregate_yesterday_is_unknown_when_a_contributing_code_has_none(modules) -> None:
    calc = modules.calculation
    known = {"yesterday_consumption": 4.0, "today_consumption": 1.0}
    unknown = {"yesterday_consumption": None, "today_consumption": 2.0}
    assert calc.aggregate_overviews([known, unknown], ["a", "b"])["yesterday_consumption"] is None
    assert calc.aggregate_overviews([known, known], ["a", "b"])["yesterday_consumption"] == 8.0
    assert calc.aggregate_overviews([{"today_consumption": 1}], ["a"])["yesterday_consumption"] == 0.0


# ------------------------------------------------------------- seen bills in the store

def _valid_bill_entry(**overrides):
    return {"bill_id": "0123456789ab", "first_seen": "2026-10-02", "status": "match", "amount": 1000, **overrides}


def test_a_store_without_bills_loads_with_an_empty_bills_state(modules) -> None:
    s = modules.store
    assert s.empty_store()["bills"] == {}
    old = {"daily": {"PB000001": {"2026-03-01": 5.0}}, "meta": {}, "series": {}}
    assert s.normalize_store(old)["bills"] == {}
    for garbage in (None, [], {"bills": "x"}, {"bills": [1]}):
        assert s.normalize_store(garbage)["bills"] == {}


def test_bills_state_round_trips_and_a_seeded_code_without_periods_is_kept(modules) -> None:
    s = modules.store
    raw = {"bills": {"PB000001": {"2026-09-1": _valid_bill_entry()}, "PB000002": {}}}
    again = s.normalize_store(json.loads(json.dumps(raw)))
    assert again["bills"] == raw["bills"]


@pytest.mark.parametrize("entry", [
    _valid_bill_entry(bill_id="PB000001"), _valid_bill_entry(bill_id="0123456789AB"), _valid_bill_entry(bill_id="short"),
    _valid_bill_entry(first_seen="yesterday"), _valid_bill_entry(first_seen=None), _valid_bill_entry(status="odd"),
    _valid_bill_entry(amount="1000"), _valid_bill_entry(amount=True), _valid_bill_entry(amount=None), "x", None, [],
])
def test_a_malformed_seen_period_is_dropped(modules, entry) -> None:
    loaded = modules.store.normalize_store({"bills": {"PB000001": {"2026-09-1": entry, "2026-08-1": _valid_bill_entry()}}})
    assert loaded["bills"] == {"PB000001": {"2026-08-1": _valid_bill_entry()}}


@pytest.mark.parametrize("key", ["2026-9-1", "2026-13-1", "2026-09", "2026-09-x", "Sept", "2026-09-1-2"])
def test_a_malformed_period_key_is_dropped(modules, key) -> None:
    loaded = modules.store.normalize_store({"bills": {"PB000001": {key: _valid_bill_entry()}}})
    assert loaded["bills"] == {"PB000001": {}}


# --------------------------------------------------------- bill events in the cycle

class _FailingStore(_FakeStore):
    def __init__(self, loaded=None):
        super().__init__(loaded)
        self.fail = False

    async def async_save(self, data):
        if self.fail:
            raise OSError("disk full")
        await super().async_save(data)


def _bill_row(year, month, kwh, *, amount=1000, ky=1):
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return {
        "period": f"Tháng {month}/{year}", "total_kwh": kwh, "total_amount": amount, "is_paid": True, "issue_date": "",
        "KY": ky, "THANG": month, "NAM": year, "period_start": date(year, month, 1).isoformat(),
        "period_end": last.isoformat(), "calculated_amount": None,
    }


def _bill_store(**extra):
    """Daily rows covering 08/2026 and 09/2026 bills (shifted windows), 3.0 kWh a day."""
    days = {}
    day = date(2026, 7, 31)
    while day <= date(2026, 9, 29):
        days[day.isoformat()] = 3.0
        day += timedelta(days=1)
    meta = {"cursor": None, "empty": 0, "done": True, "prev_refresh": "2026-10-02", "failures": 0, "tail_try": ""}
    return {"daily": {"PB000001": days}, "meta": {"PB000001": meta}, "series": {}, **extra}


def _bill_meter(bills, *, fresh=True):
    return {"daily_history": [], "bills": bills, "bills_fresh": fresh, "price_model": _DEFAULT_MODEL["model"]}


def _bill_update(history, meter, **kwargs):
    return asyncio.run(history.async_update(
        meters={"PB000001": meter}, codes=["PB000001"], selected=["PB000001"], aliases=kwargs.pop("aliases", {}),
        allow_backfill=False, **kwargs,
    ))


_SEPT = _bill_row(2026, 9, 90.0, amount=250000)
_AUG = _bill_row(2026, 8, 93.0, amount=240000)


def test_the_first_fresh_poll_announces_only_last_month_and_saves_before_returning(modules) -> None:
    history, seen = _history(modules, store=_FakeStore(_bill_store()), today=date(2026, 10, 2))
    events = _bill_update(history, _bill_meter([_AUG, _SEPT]), aliases={"PB000001": "Nhà chính"})
    assert [(e["period"], e["reason"], e["label"]) for e in events] == [("09/2026", "new", "Nhà chính")]
    assert set(seen.store.saved[-1]["bills"]["PB000001"]) == {"2026-08-1", "2026-09-1"}, "saved before the events are returned"
    assert _bill_update(history, _bill_meter([_AUG, _SEPT])) == []


def test_cached_bills_never_seed_or_fire(modules) -> None:
    history, seen = _history(modules, store=_FakeStore(_bill_store()), today=date(2026, 10, 2))
    assert _bill_update(history, _bill_meter([_AUG, _SEPT], fresh=False)) == []
    assert history._data["bills"] == {}
    assert all("PB000001" not in saved.get("bills", {}) for saved in seen.store.saved)
    assert [e["period"] for e in _bill_update(history, _bill_meter([_AUG, _SEPT]))] == ["09/2026"]


def test_a_failed_save_fires_nothing_and_the_next_poll_retries(modules) -> None:
    store = _FailingStore(_bill_store())
    history, seen = _history(modules, store=store, today=date(2026, 10, 2))
    store.fail = True
    assert _bill_update(history, _bill_meter([_AUG, _SEPT])) == []
    assert history._data["bills"] == {}, "the in-memory state is rolled back so nothing is lost"
    store.fail = False
    assert [e["period"] for e in _bill_update(history, _bill_meter([_AUG, _SEPT]))] == ["09/2026"]


def test_a_restart_after_a_fire_does_not_announce_again(modules) -> None:
    store = _FakeStore(_bill_store())
    history, _ = _history(modules, store=store, today=date(2026, 10, 2))
    assert len(_bill_update(history, _bill_meter([_AUG, _SEPT]))) == 1
    restarted, _ = _history(modules, store=_FakeStore(store.saved[-1]), today=date(2026, 10, 2))
    assert _bill_update(restarted, _bill_meter([_AUG, _SEPT])) == []


def test_a_downgrade_and_re_upgrade_announces_at_most_the_previous_month(modules) -> None:
    store = _FakeStore(_bill_store())
    history, _ = _history(modules, store=store, today=date(2026, 10, 2))
    _bill_update(history, _bill_meter([_AUG, _SEPT]))
    old_version_saved = {key: value for key, value in store.saved[-1].items() if key != "bills"}
    again, _ = _history(modules, store=_FakeStore(old_version_saved), today=date(2026, 10, 4))
    assert [e["period"] for e in _bill_update(again, _bill_meter([_AUG, _SEPT]))] == ["09/2026"]


def test_a_status_change_within_ten_days_is_one_update_through_the_cycle(modules) -> None:
    store = _bill_store()
    del store["daily"]["PB000001"]["2026-09-10"], store["daily"]["PB000001"]["2026-09-11"]
    history, seen = _history(modules, store=_FakeStore(store), today=date(2026, 10, 2))
    first = _bill_update(history, _bill_meter([_SEPT]))
    assert [(e["reason"], e["status"]) for e in first] == [("new", "incomplete")]
    history._data["daily"]["PB000001"]["2026-09-10"] = 3.0
    history._data["daily"]["PB000001"]["2026-09-11"] = 3.0
    second = _bill_update(history, _bill_meter([_SEPT]))
    assert [(e["reason"], e["status"], e["previous_status"]) for e in second] == [("update", "match", "incomplete")]


def test_the_threshold_comes_from_the_caller(modules) -> None:
    history, _ = _history(modules, store=_FakeStore(_bill_store()), today=date(2026, 10, 2))
    off_by_two = _bill_row(2026, 9, 92.0)
    events = _bill_update(history, _bill_meter([off_by_two]), threshold_kwh=2.5)
    assert events[0]["status"] == "match" and events[0]["threshold_kwh"] == 2.5


def test_the_events_and_the_stored_state_never_hold_the_code(modules) -> None:
    history, seen = _history(modules, store=_FakeStore(_bill_store()), today=date(2026, 10, 2))
    events = _bill_update(history, _bill_meter([_AUG, _SEPT]), aliases={"PB000001": "Kho PB000001"})
    assert events[0]["label"] == "…0001"
    assert "PB000001" not in repr(events) and "PB000001" not in repr(seen.store.saved[-1]["bills"].get("PB000001", {}))


def test_a_broken_bill_row_never_breaks_the_cycle(modules) -> None:
    history, _ = _history(modules, store=_FakeStore(_bill_store()), today=date(2026, 10, 2))
    assert _bill_update(history, _bill_meter([None, "x"])) == []


def test_annotate_bills_uses_the_stored_days(modules) -> None:
    history, _ = _history(modules, store=_FakeStore(_bill_store()), today=date(2026, 10, 2))
    _bill_update(history, _bill_meter([_SEPT]))
    rows = history.annotate_bills("PB000001", [_SEPT], 1.0)
    assert (rows[0]["reconcile_status"], rows[0]["collected_kwh"], rows[0]["diff_kwh"]) == ("match", 90.0, 0.0)
    assert history.annotate_bills("PB000009", [_SEPT], 1.0)[0]["reconcile_status"] == "incomplete", "no days stored: data is missing"


def test_annotate_bills_gives_only_the_canonical_period_while_the_store_is_unavailable(modules) -> None:
    history, _ = _history(modules)
    rows = history.annotate_bills("PB000001", [_SEPT], 1.0)
    assert (rows[0]["year"], rows[0]["month"], rows[0]["reconcile_status"]) == (2026, 9, None)


def test_a_code_with_a_broken_bill_row_does_not_stop_the_events_of_the_others(modules) -> None:
    data = _bill_store()
    data["daily"]["PB000002"] = dict(data["daily"]["PB000001"])
    history, _ = _history(modules, store=_FakeStore(data), today=date(2026, 10, 2))
    meters = {"PB000001": _bill_meter([None, "x"]), "PB000002": _bill_meter([_AUG, _SEPT])}
    events = asyncio.run(history.async_update(
        meters=meters, codes=list(meters), selected=list(meters), aliases={}, allow_backfill=False,
    ))
    assert [e["period"] for e in events] == ["09/2026"]


def test_an_empty_fresh_bill_list_leaves_the_store_untouched(modules) -> None:
    history, seen = _history(modules, store=_FakeStore(_bill_store()), today=date(2026, 10, 2))
    assert _bill_update(history, _bill_meter([])) == []
    assert history._data["bills"] == {}
    assert [e["period"] for e in _bill_update(history, _bill_meter([_AUG, _SEPT]))] == ["09/2026"]


# --------------------------------------------------------- unpaid bills in the cycle

def _unpaid_bill_row(year, month, kwh, *, amount=250000, owed=250000, due="2026-10-15"):
    return {
        **_bill_row(year, month, kwh, amount=amount), "payment_status": "unpaid", "is_paid": False, "due_date": due,
        "amount_owed": owed, "bill_source": "unpaid", "payment_checked": True,
    }


def _paid_bill_row(year, month, kwh, *, amount=240000):
    return {**_bill_row(year, month, kwh, amount=amount), "payment_status": "paid", "bill_source": "history", "payment_checked": True}


def _unpaid_meter(bills, *, history_fresh=True, unpaid_fresh=True):
    return {**_bill_meter(bills, fresh=history_fresh), "unpaid_fresh": unpaid_fresh}


def _upgrade_store():
    """A store written by the version that only knew the paid history: its periods are recorded, no unpaid flag."""
    seen = {
        f"2026-{month:02d}-1": {"bill_id": f"{month:012x}", "first_seen": f"2026-{month:02d}-28", "status": "match", "amount": 1000}
        for month in (4, 5, 6)
    }
    return _bill_store(bills={"PB000001": seen})


_PAID_HISTORY = [_paid_bill_row(2026, 4, 90.0), _paid_bill_row(2026, 5, 93.0), _paid_bill_row(2026, 6, 90.0)]
_ARREARS = [_unpaid_bill_row(2026, 7, 93.0), _unpaid_bill_row(2026, 8, 93.0), _unpaid_bill_row(2026, 9, 90.0)]


def test_a_store_without_the_unpaid_flag_loads_it_as_not_seeded_and_the_flag_round_trips(modules) -> None:
    s = modules.store
    assert s.empty_store()["unpaid_seeded"] == {}
    assert s.STORE_VERSION == 1, "a higher version would need a migration function in Home Assistant's Store"
    assert s.normalize_store({"daily": {}, "bills": {}})["unpaid_seeded"] == {}
    again = s.normalize_store(json.loads(json.dumps({"unpaid_seeded": {"PB000001": True, "PB000002": False}})))
    assert again["unpaid_seeded"] == {"PB000001": True}
    for garbage in (None, [], {"unpaid_seeded": "x"}, {"unpaid_seeded": [1]}, {"unpaid_seeded": {"PB000001": "yes"}}):
        assert s.normalize_store(garbage)["unpaid_seeded"] == {}


def test_upgrading_with_three_unpaid_periods_announces_only_the_previous_month_once(modules) -> None:
    store = _FakeStore(_upgrade_store())
    history, seen = _history(modules, store=store, today=date(2026, 10, 1))
    events = _bill_update(history, _unpaid_meter(_PAID_HISTORY + _ARREARS))
    assert [(e["period"], e["reason"], e["payment_status"]) for e in events] == [("09/2026", "new", "unpaid")]
    saved = seen.store.saved[-1]
    assert {"2026-07-1", "2026-08-1", "2026-09-1"} <= set(saved["bills"]["PB000001"]), "old arrears are remembered silently"
    assert saved["unpaid_seeded"] == {"PB000001": True}, "the flag is saved together with the periods"
    assert _bill_update(history, _unpaid_meter(_PAID_HISTORY + _ARREARS)) == []


def test_the_unpaid_list_failing_on_the_first_poll_announces_nothing_and_leaves_the_flag_off(modules) -> None:
    history, seen = _history(modules, store=_FakeStore(_upgrade_store()), today=date(2026, 10, 1))
    assert _bill_update(history, _unpaid_meter(_PAID_HISTORY, unpaid_fresh=False)) == []
    assert history._data["unpaid_seeded"] == {}
    events = _bill_update(history, _unpaid_meter(_PAID_HISTORY + _ARREARS))
    assert [e["period"] for e in events] == ["09/2026"]


def test_a_cached_unpaid_list_never_announces_or_seeds(modules) -> None:
    history, _ = _history(modules, store=_FakeStore(_upgrade_store()), today=date(2026, 10, 1))
    assert _bill_update(history, _unpaid_meter(_PAID_HISTORY + _ARREARS, unpaid_fresh=False)) == []
    assert history._data["unpaid_seeded"] == {}
    assert set(history._data["bills"]["PB000001"]) == {"2026-04-1", "2026-05-1", "2026-06-1"}


def test_an_empty_fresh_unpaid_list_does_not_count_as_a_first_sighting(modules) -> None:
    history, _ = _history(modules, store=_FakeStore(_upgrade_store()), today=date(2026, 10, 1))
    assert _bill_update(history, _unpaid_meter(_PAID_HISTORY)) == []
    assert history._data["unpaid_seeded"] == {}


def test_a_bill_is_announced_when_issued_and_paying_it_later_fires_nothing(modules) -> None:
    store = _FakeStore(_upgrade_store())
    history, _ = _history(modules, store=store, today=date(2026, 10, 1))
    _bill_update(history, _unpaid_meter(_PAID_HISTORY + _ARREARS))
    # a new month's bill is issued
    issued = _unpaid_bill_row(2026, 10, 90.0, due="2026-11-15")
    days = history._data["daily"]["PB000001"]
    for offset in range(0, 31):
        days[(date(2026, 9, 30) + timedelta(days=offset)).isoformat()] = 3.0
    first = _bill_update(history, _unpaid_meter(_PAID_HISTORY + _ARREARS + [issued]))
    assert [(e["period"], e["reason"], e["due_date"]) for e in first] == [("10/2026", "new", "2026-11-15")]
    paid = _paid_bill_row(2026, 10, 90.0, amount=250000)
    assert _bill_update(history, _unpaid_meter(_PAID_HISTORY + [paid], unpaid_fresh=True)) == []


def test_payment_between_polls_with_the_unpaid_fetch_failing_is_not_news_and_not_unpaid(modules) -> None:
    history, _ = _history(modules, store=_FakeStore(_upgrade_store()), today=date(2026, 10, 1))
    _bill_update(history, _unpaid_meter(_PAID_HISTORY + _ARREARS))
    paid_sept = _paid_bill_row(2026, 9, 90.0, amount=250000)
    assert _bill_update(history, _unpaid_meter(_PAID_HISTORY + [paid_sept], unpaid_fresh=False)) == []


def test_a_failed_save_leaves_the_unpaid_flag_off_so_the_next_poll_retries(modules) -> None:
    store = _FailingStore(_upgrade_store())
    history, _ = _history(modules, store=store, today=date(2026, 10, 1))
    store.fail = True
    assert _bill_update(history, _unpaid_meter(_PAID_HISTORY + _ARREARS)) == []
    assert history._data["unpaid_seeded"] == {} and set(history._data["bills"]["PB000001"]) == {"2026-04-1", "2026-05-1", "2026-06-1"}
    store.fail = False
    assert [e["period"] for e in _bill_update(history, _unpaid_meter(_PAID_HISTORY + _ARREARS))] == ["09/2026"]


def test_the_unpaid_events_and_state_never_hold_the_code(modules) -> None:
    history, seen = _history(modules, store=_FakeStore(_upgrade_store()), today=date(2026, 10, 1))
    events = _bill_update(history, _unpaid_meter(_PAID_HISTORY + _ARREARS), aliases={"PB000001": "Nhà PB000001"})
    assert "PB000001" not in repr(events) and "PB000001" not in repr(seen.store.saved[-1]["bills"]["PB000001"])


def test_a_fresh_install_with_three_unpaid_periods_announces_only_the_previous_month(modules) -> None:
    """No earlier state at all: the store has days but no seen periods, and the paid history is empty."""
    history, seen = _history(modules, store=_FakeStore(_bill_store()), today=date(2026, 10, 1))
    events = _bill_update(history, _unpaid_meter(_ARREARS))
    assert [e["period"] for e in events] == ["09/2026"]
    assert set(seen.store.saved[-1]["bills"]["PB000001"]) == {"2026-07-1", "2026-08-1", "2026-09-1"}
    assert seen.store.saved[-1]["unpaid_seeded"] == {"PB000001": True}


def test_a_code_with_no_seen_periods_is_not_seeded_from_the_unpaid_list_alone(modules) -> None:
    """Cached paid history with a fresh unpaid list: seeding now would let the history announce every old period next poll."""
    history, seen = _history(modules, store=_FakeStore(_bill_store()), today=date(2026, 10, 1))
    old_paid = [_paid_bill_row(2025, month, 90.0) for month in range(1, 13)]
    assert _bill_update(history, _unpaid_meter(old_paid + _ARREARS, history_fresh=False)) == []
    assert history._data["bills"] == {} and history._data["unpaid_seeded"] == {}
    events = _bill_update(history, _unpaid_meter(old_paid + _ARREARS))
    assert [e["period"] for e in events] == ["09/2026"], "once the history is fresh too, only the previous month is announced"
