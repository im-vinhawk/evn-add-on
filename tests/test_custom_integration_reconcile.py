"""Bill reconciliation: shifted window, statuses, billing periods and the new-bill event plan (HA-free)."""

from __future__ import annotations

from datetime import date, timedelta
import importlib.util
from pathlib import Path
import sys
import types

import pytest


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "evn_vietnam"
PACKAGE = "evn_vietnam_reconcile_test"
CODE = "PB000001"
OTHER_CODE = "PB000002"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", INTEGRATION_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rec():
    sys.modules[PACKAGE] = types.ModuleType(PACKAGE)
    sys.modules[PACKAGE].__path__ = [str(INTEGRATION_DIR)]
    _load_module("const")
    _load_module("tariff")
    _load_module("calculation")
    _load_module("models")
    return _load_module("reconcile")


def _span(first: date, last: date, kwh: float) -> dict[str, float]:
    return {(first + timedelta(days=i)).isoformat(): kwh for i in range((last - first).days + 1)}


def _bill(year, month, kwh, *, ky=1, amount=1000, start=None, end=None, with_dates=True):
    """A bill row as attach_readings leaves it: a whole calendar month unless stated."""
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    return {
        "period": f"Tháng {month}/{year}", "total_kwh": kwh, "total_amount": amount, "is_paid": True,
        "issue_date": "", "KY": ky, "THANG": month, "NAM": year,
        "period_start": (start or date(year, month, 1)).isoformat() if with_dates else "",
        "period_end": (end or last).isoformat() if with_dates else "",
        "calculated_amount": None,
    }


def _results(rec, bills, days, threshold=1.0):
    periods = rec.group_periods(bills)
    return periods, rec.reconcile_all(periods, days, threshold)


# ------------------------------------------------------------------ window and statuses

def test_the_window_is_the_period_shifted_one_day_earlier(rec) -> None:
    """Each daily row EVN dates d holds the consumption of d-1: only the shifted window matches the bill."""
    days = _span(date(2026, 7, 31), date(2026, 8, 30), 3.0)
    period = rec.group_periods([_bill(2026, 8, 93.0)])[0]
    result = rec.reconcile_period(period, days, 1.0)
    assert (result["window_start"], result["window_end"]) == ("2026-07-31", "2026-08-30")
    assert (result["collected_kwh"], result["diff_kwh"], result["missing_days"], result["status"]) == (93.0, 0.0, 0, "match")
    unshifted = sum(days.get(d, 0.0) for d in _span(date(2026, 8, 1), date(2026, 8, 31), 0))
    assert abs(unshifted - 93.0) > 1.0, "the unshifted window would have been reported as a mismatch"


def test_no_kwh_when_the_bill_has_no_kwh_or_no_dates(rec) -> None:
    days = _span(date(2026, 7, 31), date(2026, 8, 30), 3.0)
    for bill in (_bill(2026, 8, None), _bill(2026, 8, 93.0, with_dates=False)):
        result = rec.reconcile_period(rec.group_periods([bill])[0], days, 1.0)
        assert result["status"] == "no_kwh" and result["diff_kwh"] is None
    undated = rec.reconcile_period(rec.group_periods([_bill(2026, 8, 93.0, with_dates=False)])[0], days, 1.0)
    assert (undated["window_start"], undated["collected_kwh"], undated["missing_days"]) == (None, None, None)
    dated = rec.reconcile_period(rec.group_periods([_bill(2026, 8, None)])[0], days, 1.0)
    assert dated["collected_kwh"] == 93.0 and dated["missing_days"] == 0, "what can be computed still is"


def test_match_is_inclusive_of_the_threshold(rec) -> None:
    days = _span(date(2026, 7, 31), date(2026, 8, 30), 0.5)  # collects 15.5
    at_limit = rec.reconcile_period(rec.group_periods([_bill(2026, 8, 14.5)])[0], days, 1.0)
    assert (at_limit["diff_kwh"], at_limit["status"]) == (1.0, "match")
    over = rec.reconcile_period(rec.group_periods([_bill(2026, 8, 14.4)])[0], days, 1.0)
    assert (over["diff_kwh"], over["status"]) == (1.1, "mismatch")


def test_one_or_two_missing_days_can_still_match_the_bill(rec) -> None:
    days = _span(date(2026, 7, 31), date(2026, 8, 30), 0.5)
    del days["2026-08-10"], days["2026-08-11"]
    result = rec.reconcile_period(rec.group_periods([_bill(2026, 8, 14.5)])[0], days, 1.0)
    assert (result["collected_kwh"], result["diff_kwh"], result["missing_days"], result["status"]) == (14.5, 0.0, 2, "match")


def test_mismatch_when_the_window_is_complete_and_the_totals_disagree(rec) -> None:
    days = _span(date(2026, 7, 31), date(2026, 8, 30), 3.0)
    result = rec.reconcile_period(rec.group_periods([_bill(2026, 8, 98.0)])[0], days, 1.0)
    assert (result["diff_kwh"], result["missing_days"], result["status"]) == (-5.0, 0, "mismatch")


def test_incomplete_when_days_are_missing_and_the_totals_disagree(rec) -> None:
    days = _span(date(2026, 7, 31), date(2026, 8, 30), 3.0)
    for gone in ("2026-08-10", "2026-08-11"):
        del days[gone]
    result = rec.reconcile_period(rec.group_periods([_bill(2026, 8, 93.0)])[0], days, 1.0)
    assert (result["diff_kwh"], result["missing_days"], result["status"]) == (-6.0, 2, "incomplete")


def test_the_threshold_is_an_option(rec) -> None:
    days = _span(date(2026, 7, 31), date(2026, 8, 30), 3.0)
    period = rec.group_periods([_bill(2026, 8, 95.5)])[0]
    assert rec.reconcile_period(period, days, 1.0)["status"] == "mismatch"
    assert rec.reconcile_period(period, days, 2.5)["status"] == "match"
    assert rec.reconcile_period(period, days, 2.5)["threshold_kwh"] == 2.5


# ------------------------------------------------------------------ boundary pairs

def _december_january(rec, *, december_kwh, january_kwh, missing=(), skip_january=False):
    days = _span(date(2025, 11, 30), date(2026, 1, 30), 3.0)  # collects 93.0 for December and 93.0 for January
    for gone in missing:
        del days[gone]
    bills = [_bill(2025, 12, december_kwh)]
    if not skip_january:
        bills.append(_bill(2026, 1, january_kwh))
    return _results(rec, bills, days)


def test_an_adjacent_cancelling_pair_is_a_boundary_on_both_rows(rec) -> None:
    periods, results = _december_january(rec, december_kwh=87.0, january_kwh=99.0)
    december, january = results["2025-12-1"], results["2026-01-1"]
    assert (december["diff_kwh"], january["diff_kwh"]) == (6.0, -6.0)
    assert december["status"] == "boundary" and december["paired_with"] == "2026-01-1"
    assert january["status"] == "boundary" and january["paired_with"] == "2025-12-1"
    assert january["compensates_previous"] is True and december["compensates_previous"] is False


def test_periods_that_cancel_but_are_not_adjacent_are_not_a_boundary(rec) -> None:
    days = _span(date(2025, 11, 30), date(2026, 2, 27), 3.0)
    # December collects 93.0 (bill 87.0: +6.0); February collects 84.0 (bill 90.0: -6.0); January has no bill.
    periods, results = _results(rec, [_bill(2025, 12, 87.0), _bill(2026, 2, 90.0)], days)
    assert results["2025-12-1"]["diff_kwh"] == 6.0 and results["2026-02-1"]["diff_kwh"] == -6.0
    assert results["2025-12-1"]["status"] == results["2026-02-1"]["status"] == "mismatch"


def test_a_cancelling_pair_with_missing_days_is_incomplete_not_a_boundary(rec) -> None:
    days = _span(date(2025, 11, 30), date(2026, 1, 30), 3.0)
    del days["2026-01-15"]  # January collects 90.0
    periods, results = _results(rec, [_bill(2025, 12, 87.0), _bill(2026, 1, 96.0)], days)
    assert (results["2025-12-1"]["diff_kwh"], results["2026-01-1"]["diff_kwh"]) == (6.0, -6.0)
    assert results["2026-01-1"]["missing_days"] == 1
    assert results["2026-01-1"]["status"] == "incomplete"
    assert results["2025-12-1"]["status"] == "mismatch", "December keeps its own result: nothing pairs with it"


def test_a_chain_of_three_periods_pairs_only_the_first_two(rec) -> None:
    days = _span(date(2025, 11, 30), date(2026, 2, 27), 3.0)  # collects 93.0, 93.0 and 84.0
    bills = [_bill(2025, 12, 87.0), _bill(2026, 1, 99.0), _bill(2026, 2, 78.0)]  # diffs +6.0, -6.0, +6.0
    periods, results = _results(rec, bills, days)
    assert (results["2025-12-1"]["status"], results["2025-12-1"]["paired_with"]) == ("boundary", "2026-01-1")
    assert (results["2026-01-1"]["status"], results["2026-01-1"]["paired_with"]) == ("boundary", "2025-12-1")
    assert results["2026-02-1"]["status"] == "mismatch", "a period already explained by its neighbour is not paired again"


def test_a_single_month_without_its_next_bill_stays_a_mismatch(rec) -> None:
    periods, results = _december_january(rec, december_kwh=87.0, january_kwh=None, skip_january=True)
    assert results["2025-12-1"]["status"] == "mismatch" and results["2025-12-1"]["paired_with"] is None


def test_a_pair_that_does_not_cancel_within_the_threshold_is_not_a_boundary(rec) -> None:
    periods, results = _december_january(rec, december_kwh=87.0, january_kwh=96.0)
    assert (results["2025-12-1"]["status"], results["2026-01-1"]["status"]) == ("mismatch", "mismatch")


# ------------------------------------------------------------------ periods and annotation

def test_invoices_of_one_period_are_combined_and_only_the_first_row_carries_the_result(rec) -> None:
    days = _span(date(2026, 7, 31), date(2026, 8, 30), 3.0)
    first = _bill(2026, 8, 93.0, amount=700)
    second = {**_bill(2026, 8, None, amount=300), "period_start": "", "period_end": ""}
    periods = rec.group_periods([first, second])
    assert len(periods) == 1 and periods[0]["total_amount"] == 1000 and periods[0]["bill_kwh"] == 93.0
    rows, _, results = rec.annotate_bills([first, second], days, 1.0)
    assert rows[0]["reconcile_status"] == "match" and rows[0]["collected_kwh"] == 93.0
    assert rows[1]["reconcile_status"] is None and rows[1]["collected_kwh"] is None and rows[1]["diff_kwh"] is None
    assert [(r["year"], r["month"], r["ky"]) for r in rows] == [(2026, 8, 1), (2026, 8, 1)]
    assert "reconcile_status" not in first, "the input rows are not modified"


def test_two_periods_in_one_month_are_two_units(rec) -> None:
    bills = [
        _bill(2026, 8, 40.0, ky=1, start=date(2026, 8, 1), end=date(2026, 8, 15)),
        _bill(2026, 8, 50.0, ky=2, start=date(2026, 8, 16), end=date(2026, 8, 31)),
    ]
    assert [p["key"] for p in rec.group_periods(bills)] == ["2026-08-1", "2026-08-2"]


def test_without_a_history_the_rows_get_the_canonical_period_and_no_reconciliation(rec) -> None:
    rows, periods, results = rec.annotate_bills([_bill(2026, 8, 93.0)], None, 1.0)
    assert (rows[0]["year"], rows[0]["month"], rows[0]["ky"], rows[0]["reconcile_status"]) == (2026, 8, 1, None)
    assert results == {}


def test_a_row_without_a_period_key_is_left_alone(rec) -> None:
    broken = {**_bill(2026, 8, 93.0), "KY": None}
    rows, periods, results = rec.annotate_bills([broken], {}, 1.0)
    assert rows[0]["ky"] is None and rows[0]["reconcile_status"] is None and periods == []


# ------------------------------------------------------------------ identity and label

def test_bill_id_is_opaque_stable_and_per_code(rec) -> None:
    one = rec.bill_id("entry-1", CODE, "2026-08-1")
    assert len(one) == 12 and int(one, 16) >= 0
    assert one == rec.bill_id("entry-1", CODE, "2026-08-1")
    assert one != rec.bill_id("entry-1", OTHER_CODE, "2026-08-1")
    assert one != rec.bill_id("entry-2", CODE, "2026-08-1")
    assert CODE.lower() not in one


@pytest.mark.parametrize("nickname", [
    "pb000001", "Nhà PB000099 cũ", "kho-pc000012", "xHN0000123y", "PB000001-copy",
])
def test_a_nickname_that_holds_a_code_falls_back_to_the_masked_last_four(rec, nickname) -> None:
    assert rec.safe_label(CODE, nickname) == "…0001"


def test_a_clean_nickname_is_used_and_a_missing_one_falls_back(rec) -> None:
    assert rec.safe_label(CODE, "  Nhà chính ") == "Nhà chính"
    assert rec.safe_label(CODE, "") == "…0001" and rec.safe_label(CODE, None) == "…0001"
    assert rec.safe_label(CODE, "Cửa hàng 12345") == "Cửa hàng 12345", "digits alone are not a customer code"


# ------------------------------------------------------------------ event plan

def _plan(rec, bills, days, state, today, *, entry="entry-1", label="Nhà", threshold=1.0):
    periods, results = _results(rec, bills, days, threshold)
    return rec.plan_events(entry, CODE, label, periods, results, state, today, threshold)


def _old_months(count):
    """`count` whole months ending in 08/2026, oldest first."""
    months = []
    year, month = 2026, 8
    for _ in range(count):
        months.append((year, month))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return list(reversed(months))


def test_seeding_records_old_periods_silently_and_announces_the_previous_month(rec) -> None:
    bills = [_bill(y, m, 93.0) for y, m in _old_months(20)] + [_bill(2026, 9, 90.0)]
    days = _span(date(2024, 1, 1), date(2026, 9, 29), 3.0)
    events, state = _plan(rec, bills, days, None, date(2026, 10, 3))
    assert [e["period"] for e in events] == ["09/2026"] and events[0]["reason"] == "new"
    assert len(state) == 21
    again, same = _plan(rec, bills, days, state, date(2026, 10, 3))
    assert again == [] and same == state, "a second poll announces nothing"


def test_a_period_older_than_last_month_at_seeding_never_fires_even_when_it_is_the_only_one(rec) -> None:
    events, state = _plan(rec, [_bill(2026, 7, 93.0)], _span(date(2026, 6, 30), date(2026, 7, 30), 3.0), None, date(2026, 10, 3))
    assert events == [] and set(state) == {"2026-07-1"}


def test_an_empty_bill_list_still_marks_the_code_as_seeded(rec) -> None:
    events, state = rec.plan_events("entry-1", CODE, "Nhà", [], {}, None, date(2026, 10, 3), 1.0)
    assert events == [] and state == {}
    events, state = _plan(rec, [_bill(2026, 9, 90.0)], _span(date(2026, 8, 31), date(2026, 9, 29), 3.0), state, date(2026, 10, 3))
    assert [e["reason"] for e in events] == ["new"], "once seeded, any unseen period is new"


def test_a_new_period_fires_one_event_with_the_whole_payload(rec) -> None:
    days = _span(date(2026, 8, 31), date(2026, 9, 29), 3.0)
    events, state = _plan(rec, [_bill(2026, 9, 90.0, amount=250000)], days, {}, date(2026, 10, 2), label="Nhà chính")
    assert len(events) == 1
    event = events[0]
    assert set(event) == {
        "bill_id", "entry_id", "label", "period", "ky", "period_start", "period_end", "window_start", "window_end",
        "bill_kwh", "collected_kwh", "diff_kwh", "missing_days", "status", "previous_status", "reason",
        "compensates_previous", "total_amount", "calculated_amount", "threshold_kwh",
    }
    assert (event["period"], event["ky"], event["reason"], event["previous_status"]) == ("09/2026", 1, "new", None)
    assert (event["window_start"], event["window_end"]) == ("2026-08-31", "2026-09-29")
    assert (event["bill_kwh"], event["collected_kwh"], event["diff_kwh"], event["status"]) == (90.0, 90.0, 0.0, "match")
    assert (event["total_amount"], event["entry_id"], event["label"], event["compensates_previous"]) == (250000, "entry-1", "Nhà chính", False)
    assert state["2026-09-1"] == {
        "bill_id": event["bill_id"], "first_seen": "2026-10-02", "status": "match", "amount": 250000,
    }


def test_a_change_of_status_within_ten_days_fires_one_update_and_a_later_change_does_nothing(rec) -> None:
    gone = _span(date(2026, 8, 31), date(2026, 9, 29), 3.0)
    partial = {d: v for d, v in gone.items() if d not in ("2026-09-10", "2026-09-11")}
    bill = _bill(2026, 9, 90.0)
    events, state = _plan(rec, [bill], partial, {}, date(2026, 10, 1))
    assert [(e["reason"], e["status"]) for e in events] == [("new", "incomplete")]
    events, state = _plan(rec, [bill], gone, state, date(2026, 10, 3))
    assert [(e["reason"], e["status"], e["previous_status"]) for e in events] == [("update", "match", "incomplete")]
    events, state = _plan(rec, [bill], gone, state, date(2026, 10, 4))
    assert events == []
    later, frozen = _plan(rec, [bill], partial, state, date(2026, 10, 13))
    assert later == [] and frozen == state, "after ten days the entry is frozen"


def test_a_second_invoice_for_an_announced_period_is_one_update_with_the_summed_amount(rec) -> None:
    days = _span(date(2026, 8, 31), date(2026, 9, 29), 3.0)
    first = _bill(2026, 9, 90.0, amount=700)
    events, state = _plan(rec, [first], days, {}, date(2026, 10, 1))
    second = {**_bill(2026, 9, None, amount=300), "period_start": "", "period_end": ""}
    events, state = _plan(rec, [first, second], days, state, date(2026, 10, 2))
    assert [(e["reason"], e["total_amount"]) for e in events] == [("update", 1000)]
    assert state["2026-09-1"]["amount"] == 1000
    assert _plan(rec, [first, second], days, state, date(2026, 10, 2))[0] == []


def test_a_boundary_pair_fires_for_the_later_period_only_and_says_it_compensates(rec) -> None:
    days = _span(date(2025, 11, 30), date(2026, 1, 30), 3.0)
    bills = [_bill(2025, 12, 87.0), _bill(2026, 1, 99.0)]
    december_state = {"2025-12-1": {"bill_id": rec.bill_id("entry-1", CODE, "2025-12-1"), "first_seen": "2026-01-02",
                                    "status": "mismatch", "amount": 1000}}
    events, state = _plan(rec, bills, days, december_state, date(2026, 2, 2))
    assert [(e["period"], e["status"], e["compensates_previous"]) for e in events] == [("01/2026", "boundary", True)]
    assert state["2025-12-1"]["status"] == "mismatch", "the earlier period's record is left as it was"


def test_the_state_keeps_at_most_36_periods_dropping_the_oldest(rec) -> None:
    bills = [_bill(y, m, 90.0) for y, m in _old_months(40)]
    days = _span(date(2023, 1, 1), date(2026, 9, 29), 3.0)
    events, state = _plan(rec, bills, days, {}, date(2026, 9, 5))
    assert len(state) == 36 and "2026-08-1" in state and "2023-05-1" not in state


def test_the_event_never_carries_the_customer_code(rec) -> None:
    days = _span(date(2026, 8, 31), date(2026, 9, 29), 3.0)
    label = rec.safe_label(CODE, "Kho PB000001")
    events, state = _plan(rec, [_bill(2026, 9, 90.0)], days, {}, date(2026, 10, 2), label=label)
    text = repr(events[0]) + repr(state)
    assert CODE not in text and CODE.lower() not in text and OTHER_CODE not in text


def test_two_codes_with_the_same_nickname_get_different_ids_and_a_rename_keeps_the_id(rec) -> None:
    days = _span(date(2026, 8, 31), date(2026, 9, 29), 3.0)
    periods, results = _results(rec, [_bill(2026, 9, 90.0)], days)
    first, _ = rec.plan_events("entry-1", CODE, "Nhà", periods, results, {}, date(2026, 10, 2), 1.0)
    second, _ = rec.plan_events("entry-1", OTHER_CODE, "Nhà", periods, results, {}, date(2026, 10, 2), 1.0)
    renamed, _ = rec.plan_events("entry-1", CODE, "Căn hộ", periods, results, {}, date(2026, 10, 2), 1.0)
    assert first[0]["bill_id"] != second[0]["bill_id"]
    assert first[0]["bill_id"] == renamed[0]["bill_id"] and renamed[0]["label"] == "Căn hộ"
