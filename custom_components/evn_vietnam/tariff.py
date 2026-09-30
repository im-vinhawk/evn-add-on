"""EVN residential retail tariff by effective date. Data only.

Kept as a Python constant rather than a JSON file so nothing reads a file inside
Home Assistant's event loop.

To add a future price change, append one TariffRow with the date the new prices
take effect. A month in which ``calculated_amount`` and the real ``total_amount``
of a bill start to differ is the signal that a row is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Width in kWh of tiers 1-5; the sixth tier (from 401 kWh) has no upper limit.
TIER_WIDTHS: tuple[int | None, ...] = (50, 50, 100, 100, 100, None)


@dataclass(frozen=True)
class TariffRow:
    """Prices in VND/kWh before VAT for the six residential tiers."""

    effective_from: date
    prices: tuple[int, int, int, int, int, int]
    vat_rate: Decimal
    source: str


# Ascending by effective_from.
TARIFF_ROWS: tuple[TariffRow, ...] = (
    TariffRow(
        date(2023, 11, 9), (1806, 1866, 2167, 2729, 3050, 3151), Decimal("0.08"),
        "EVN residential table; reproduces issued bills",
    ),
    TariffRow(
        date(2024, 10, 11), (1893, 1956, 2271, 2860, 3197, 3302), Decimal("0.08"),
        "EVN residential table; reproduces issued bills",
    ),
    TariffRow(
        date(2025, 5, 10), (1984, 2050, 2380, 2998, 3350, 3460), Decimal("0.08"),
        "evn.com.vn residential table (QĐ 1279/QĐ-BCT); reproduces issued bills",
    ),
)
