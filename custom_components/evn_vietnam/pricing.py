"""Per-code price model: the residential tier model, or the code's own effective price.

The tier model is only trusted where it reproduces that code's real bills.  A
code billed on another schedule (or after a price or VAT change that
``tariff.py`` does not list yet) gets the price it actually paid per kWh.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .calculation import as_float, calculate_bill_amount, round_half_up

# How many of the latest closed bills decide the verification and the effective price.
PRICE_CHECK_BILLS = 3

ESTIMATE_TIERED = "tiered"
ESTIMATE_EFFECTIVE_PRICE = "effective_price"


@dataclass(frozen=True)
class PriceModel:
    """How one customer code's month amount is estimated."""

    tariff_verified: bool | None
    estimate_method: str
    effective_price: Decimal | None


def _newest_first(bills: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(bills, key=lambda bill: (bill.get("NAM") or 0, bill.get("THANG") or 0, bill.get("KY") or 0), reverse=True)


def _tariff_verified(bills: list[Mapping[str, Any]]) -> bool | None:
    modelled = [
        bill for bill in bills
        if bill.get("calculated_amount") is not None and as_float(bill.get("total_amount")) > 0
    ][:PRICE_CHECK_BILLS]
    if not modelled:
        return None
    return all(int(bill["calculated_amount"]) == int(as_float(bill["total_amount"])) for bill in modelled)


def _effective_price(bills: list[Mapping[str, Any]]) -> Decimal | None:
    priced = [bill for bill in bills if bill.get("total_kwh") is not None and as_float(bill["total_kwh"]) > 0]
    priced = priced[:PRICE_CHECK_BILLS]
    if not priced:
        return None
    amount = sum((Decimal(str(int(as_float(bill.get("total_amount"))))) for bill in priced), Decimal(0))
    kwh = sum((Decimal(str(bill["total_kwh"])) for bill in priced), Decimal(0))
    return amount / kwh


def build_price_model(bills: Iterable[Mapping[str, Any]]) -> PriceModel:
    """Decide from one code's bills how its month is priced."""
    ordered = _newest_first(bills)
    verified = _tariff_verified(ordered)
    price = _effective_price(ordered)
    method = ESTIMATE_EFFECTIVE_PRICE if verified is False and price is not None else ESTIMATE_TIERED
    return PriceModel(verified, method, price)


def month_amount(kwh: float, month_start: date, month_end: date, model: PriceModel) -> int | None:
    """Amount in VND of a whole calendar month with this much kWh; None when it cannot be priced."""
    if model.estimate_method == ESTIMATE_EFFECTIVE_PRICE and model.effective_price is not None:
        return round_half_up(max(Decimal(str(kwh)), Decimal(0)) * model.effective_price)
    return calculate_bill_amount(kwh, month_start, month_end)


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def price_overview(overview: dict[str, Any]) -> None:
    """Set a code's current-month estimate from its bills and record how it was made."""
    model = build_price_model(overview.get("bills", []))
    overview["price_model"] = model
    overview["tariff_verified"] = model.tariff_verified
    overview["estimate_method"] = model.estimate_method
    start, end = _parse_day(overview.get("month_start")), _parse_day(overview.get("month_end"))
    if start is not None and end is not None:
        overview["current_month_amount"] = month_amount(
            as_float(overview.get("current_month_consumption")), start, end, model
        )
