"""Keeps each code's daily kWh over time and publishes it as long-term statistics.

Every step is guarded: a storage, backfill or statistics error is logged at debug
level and never fails the sensor update.  The Home Assistant pieces (Store,
recorder import, time zone) are injected, so this class runs without HA.
"""

from __future__ import annotations

import asyncio
import calendar
from datetime import date, timedelta, tzinfo
import logging
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .api import EvnApiError
from .const import BACKFILL_MONTHS_PER_CYCLE, BACKFILL_PAUSE_SECONDS, DOMAIN, MAX_BACKFILL_MONTHS
from .daily_store import (
    STORE_VERSION, backfill_meta, code_days, earliest_day, mark_previous_month_refreshed, merge_daily,
    needs_previous_month_refresh, next_backfill_month, normalize_store, record_backfill_month, record_failure,
)
from .pricing import PriceModel
from .statistics_import import SeriesSpec, async_import_series, build_series, series_to_clear

_LOGGER = logging.getLogger(__name__)

# Coalesces the writes of one refresh into a single disk write.
SAVE_DELAY_SECONDS = 30


class DailyHistory:
    """Merge live days into the store, walk the backfill, and import the statistics."""

    def __init__(
        self,
        *,
        client: Any,
        store: Any,
        importer: Callable[[Sequence[SeriesSpec], Sequence[str]], Awaitable[None]],
        tz_provider: Callable[[], tzinfo],
        today_provider: Callable[[], date],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backfill_cap: int = MAX_BACKFILL_MONTHS,
        months_per_cycle: int = BACKFILL_MONTHS_PER_CYCLE,
    ) -> None:
        self._client, self._store, self._importer = client, store, importer
        self._tz, self._today, self._sleep = tz_provider, today_provider, sleep
        self._cap, self._months_per_cycle = backfill_cap, months_per_cycle
        self._data: dict[str, Any] | None = None
        self._models: dict[str, PriceModel] = {}
        self._imported: dict[str, tuple] = {}
        self._dirty = False

    def days(self, code: str) -> dict[str, float]:
        return code_days(self._data, code) if self._data else {}

    def backfill_status(self) -> dict[str, dict[str, Any]]:
        """Per code: the oldest stored day (how deep EVN's history goes) and whether the backfill finished."""
        if not self._data:
            return {}
        codes = {*self._data["daily"], *self._data["meta"]}
        return {
            code: {
                "earliest": earliest_day(self._data, code),
                "done": bool(self._data["meta"].get(code, {}).get("done")),
            }
            for code in sorted(codes)
        }

    async def async_update(
        self,
        *,
        meters: Mapping[str, Mapping[str, Any]],
        codes: Sequence[str],
        selected: Sequence[str],
        aliases: Mapping[str, str],
        allow_backfill: bool,
    ) -> None:
        """One coordinator cycle: merge, refresh last month early in the month, backfill, import."""
        await self._step("load", self._async_load)
        if self._data is None:
            return
        await self._step("merge", self._async_merge, meters)
        live = [code for code in codes if code in meters]
        if allow_backfill:
            # The first refresh after a restart only merges and imports, so setup is not held up by requests.
            await self._step("previous month", self._async_refresh_previous_month, live)
            await self._step("backfill", self._async_backfill, live)
        await self._step("import", self._async_import, codes, selected, aliases)
        if self._dirty:
            self._dirty = False
            self._store.async_delay_save(lambda: self._data, SAVE_DELAY_SECONDS)

    async def async_flush(self) -> None:
        """Write the store now (called when the config entry unloads)."""
        if self._data is not None:
            try:
                await self._store.async_save(self._data)
            except Exception as err:  # noqa: BLE001 - shutdown must not fail on storage
                _LOGGER.debug("EVN daily history could not be saved (%s)", type(err).__name__)

    async def _step(self, name: str, step: Callable[..., Awaitable[None]], *args: Any) -> None:
        try:
            await step(*args)
        except Exception as err:  # noqa: BLE001 - a history problem must never fail the sensor update
            _LOGGER.debug("EVN daily history step '%s' skipped (%s)", name, type(err).__name__)

    async def _async_load(self) -> None:
        if self._data is None:
            self._data = normalize_store(await self._store.async_load())

    def _unreported_from(self) -> str:
        """Zero rows from yesterday on are EVN not having reported those days yet."""
        return (self._today() - timedelta(days=1)).isoformat()

    async def _async_merge(self, meters: Mapping[str, Mapping[str, Any]]) -> None:
        for code, item in meters.items():
            if isinstance(item.get("price_model"), PriceModel):
                self._models[code] = item["price_model"]
            if merge_daily(self._data, code, item.get("daily_history", []), self._unreported_from()):
                self._dirty = True

    async def _async_fetch_month(self, code: str, month_start: date) -> list[dict[str, Any]]:
        """One month of daily rows, after the pause that keeps requests to EVN well spaced."""
        await self._sleep(BACKFILL_PAUSE_SECONDS)
        month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
        return await self._client.async_daily(code, month_start, month_end)

    def _by_failures(self, live: Sequence[str]) -> list[str]:
        """Codes in roster order, but those whose last request failed go behind the others."""
        return sorted(live, key=lambda code: backfill_meta(self._data, code)["failures"])

    async def _async_refresh_previous_month(self, live: Sequence[str]) -> None:
        """EVN can still correct last month during its first days; look again once a day."""
        today = self._today()
        for code in self._by_failures(live):
            meta = backfill_meta(self._data, code)
            if not needs_previous_month_refresh(today, meta):
                continue
            previous = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            try:
                rows = await self._async_fetch_month(code, previous)
            except EvnApiError:
                _LOGGER.debug("EVN previous-month refresh skipped because EVN is unavailable")
                record_failure(meta)
                self._dirty = True
                return
            merge_daily(self._data, code, rows, self._unreported_from())
            mark_previous_month_refreshed(meta, today)
            self._dirty = True

    async def _async_backfill(self, live: Sequence[str]) -> None:
        """Walk one code back month by month; at most one code with requests per cycle."""
        today = self._today()
        for code in self._by_failures(live):
            meta = backfill_meta(self._data, code)
            if meta["done"]:
                continue
            fetched = False
            for _ in range(self._months_per_cycle):
                month = next_backfill_month(today, meta, self._cap)
                if month is None:
                    meta["done"] = True
                    self._dirty = True
                    break
                try:
                    rows = await self._async_fetch_month(code, month)
                except EvnApiError:
                    _LOGGER.debug("EVN backfill paused because EVN is unavailable")
                    record_failure(meta)
                    self._dirty = True
                    return
                fetched = True
                dated = [row for row in rows if row.get("date")]
                merge_daily(self._data, code, dated, self._unreported_from())
                record_backfill_month(meta, month, len(dated))
                self._dirty = True
                if meta["done"]:
                    break
            if fetched:
                return

    async def _async_import(self, codes: Sequence[str], selected: Sequence[str], aliases: Mapping[str, str]) -> None:
        days_by_code = {code: code_days(self._data, code) for code in codes if code in self._data["daily"]}
        specs = build_series(days_by_code, self._models, selected, aliases, self._tz())
        changed = [spec for spec in specs if spec.rows and self._imported.get(spec.statistic_id) != _signature(spec)]
        clear = series_to_clear(specs, self._data["series"])
        if not changed and not clear:
            return
        await self._importer(changed, clear)
        for statistic_id in clear:
            self._data["series"].pop(statistic_id, None)
            self._imported.pop(statistic_id, None)
        for spec in changed:
            self._imported[spec.statistic_id] = _signature(spec)
            self._data["series"][spec.statistic_id] = {
                "start": spec.rows[0]["start"].date().isoformat(), "count": len(spec.rows), "scope": spec.scope,
            }
        self._dirty = True


def _signature(spec: SeriesSpec) -> tuple:
    return spec.name, spec.scope, tuple((row["start"], row["sum"]) for row in spec.rows)


def create_daily_history(hass: Any, entry_id: str, client: Any) -> DailyHistory:
    """Wire the class to Home Assistant's storage, recorder and time zone."""
    from homeassistant.helpers.storage import Store
    from homeassistant.util import dt as dt_util

    async def importer(specs: Sequence[SeriesSpec], to_clear: Sequence[str]) -> None:
        await async_import_series(hass, specs, to_clear)

    return DailyHistory(
        client=client,
        store=Store(hass, STORE_VERSION, f"{DOMAIN}.daily.{entry_id}"),
        importer=importer,
        tz_provider=lambda: dt_util.DEFAULT_TIME_ZONE,
        today_provider=lambda: dt_util.now().date(),
    )
