"""Keeps each code's daily kWh over time and publishes it as long-term statistics.

Every step is guarded: a storage, backfill or statistics error is logged at debug
level and never fails the sensor update.  The Home Assistant pieces (Store,
recorder import, time zone) are injected, so this class runs without HA.
"""

from __future__ import annotations

import asyncio
import calendar
import copy
from datetime import date, datetime, timedelta, tzinfo
import logging
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .api import EvnApiError
from .const import (
    BACKFILL_MONTHS_PER_CYCLE, BACKFILL_PAUSE_SECONDS, DAILY_HISTORY_DAYS, DEFAULT_RECONCILE_THRESHOLD_KWH, DOMAIN,
    MAX_BACKFILL_MONTHS,
)
from .daily_store import (
    STORE_VERSION, backfill_meta, code_days, compose_window, earliest_day, mark_previous_month_refreshed, mark_tail_tried,
    merge_daily, needs_previous_month_refresh, needs_previous_month_tail, next_backfill_month, normalize_store,
    record_backfill_month, record_failure,
)
from .pricing import PriceModel
from .reconcile import annotate_bills, plan_events, safe_label
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
        entry_id: str = "",
        now_provider: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backfill_cap: int = MAX_BACKFILL_MONTHS,
        months_per_cycle: int = BACKFILL_MONTHS_PER_CYCLE,
    ) -> None:
        self._client, self._store, self._importer = client, store, importer
        self._tz, self._now, self._sleep = tz_provider, now_provider, sleep
        self._entry_id = entry_id
        self._cap, self._months_per_cycle = backfill_cap, months_per_cycle
        self._data: dict[str, Any] | None = None
        self._models: dict[str, PriceModel] = {}
        self._imported: dict[str, tuple] = {}
        self._dirty = False

    @property
    def available(self) -> bool:
        """True once the store has loaded; before that (or when loading failed) only live rows exist."""
        return self._data is not None

    def today(self) -> date:
        """The HA-local calendar date: the one clock every window and retry derives from."""
        return self._now().date()

    def days(self, code: str) -> dict[str, float]:
        return code_days(self._data, code) if self._data else {}

    def compose(self, code: str, live_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """The last DAILY_HISTORY_DAYS days up to today: stored days with this poll's live rows over them."""
        return compose_window(self.days(code), live_rows, self.today(), DAILY_HISTORY_DAYS)

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

    def annotate_bills(self, code: str, bills: Sequence[Mapping[str, Any]], threshold_kwh: float) -> list[dict[str, Any]]:
        """Bill rows with the canonical period and, once the store is loaded, their kWh reconciliation."""
        days = self.days(code) if self._data is not None else None
        return annotate_bills(bills, days, threshold_kwh)[0]

    async def async_update(
        self,
        *,
        meters: Mapping[str, Mapping[str, Any]],
        codes: Sequence[str],
        selected: Sequence[str],
        aliases: Mapping[str, str],
        allow_backfill: bool,
        threshold_kwh: float = DEFAULT_RECONCILE_THRESHOLD_KWH,
    ) -> list[dict[str, Any]]:
        """One coordinator cycle: merge, refresh last month, backfill, import; returns the bill events to fire.

        The events come back only after the state that records them has been saved.
        """
        await self._step("load", self._async_load)
        if self._data is None:
            return []
        await self._step("merge", self._async_merge, meters)
        live = [code for code in codes if code in meters]
        if allow_backfill:
            # The first refresh after a restart only merges and imports, so setup is not held up by requests.
            await self._step("previous month", self._async_refresh_previous_month, live)
            await self._step("backfill", self._async_backfill, live)
        await self._step("import", self._async_import, codes, selected, aliases)
        events = await self._async_bill_events(meters, aliases, threshold_kwh)
        if self._dirty:
            self._dirty = False
            self._store.async_delay_save(lambda: self._data, SAVE_DELAY_SECONDS)
        return events

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

    async def _async_bill_events(
        self, meters: Mapping[str, Mapping[str, Any]], aliases: Mapping[str, str], threshold_kwh: float,
    ) -> list[dict[str, Any]]:
        """Plan the new-bill events of codes whose bills are fresh; save the seen state before returning them."""
        try:
            return await self._plan_and_save_bill_events(meters, aliases, threshold_kwh)
        except Exception as err:  # noqa: BLE001 - a reconciliation problem must never fail the sensor update
            _LOGGER.debug("EVN bill events skipped (%s)", type(err).__name__)
            return []

    async def _plan_and_save_bill_events(
        self, meters: Mapping[str, Mapping[str, Any]], aliases: Mapping[str, str], threshold_kwh: float,
    ) -> list[dict[str, Any]]:
        previous = self._data["bills"]
        planned = copy.deepcopy(previous)
        today, events = self.today(), []
        for code, overview in meters.items():
            if overview.get("bills_fresh") is not True:
                continue
            try:
                _, periods, results = annotate_bills(overview.get("bills", []), code_days(self._data, code), threshold_kwh)
                if not periods:
                    continue  # nothing was seen: an empty list must not mark the code as seeded
                fired, planned[code] = plan_events(
                    self._entry_id, code, safe_label(code, aliases.get(code)), periods, results, planned.get(code),
                    today, threshold_kwh,
                )
            except Exception as err:  # noqa: BLE001 - one code's bad rows must not silence the others
                _LOGGER.debug("EVN bill events of one code skipped (%s)", type(err).__name__)
                continue
            events.extend(fired)
        if planned == previous:
            return []
        self._data["bills"] = planned
        try:
            await self._store.async_save(self._data)
        except Exception as err:  # noqa: BLE001 - nothing is announced that could not be remembered
            self._data["bills"] = previous
            _LOGGER.debug("EVN seen bills could not be saved (%s)", type(err).__name__)
            return []
        self._dirty = False
        return events

    async def _async_load(self) -> None:
        if self._data is None:
            self._data = normalize_store(await self._store.async_load())

    def _unreported_from(self) -> str:
        """Zero rows from yesterday on are EVN not having reported those days yet."""
        return (self.today() - timedelta(days=1)).isoformat()

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
        """EVN can still correct last month during its first days (once a day) and may publish its last day late (tail)."""
        now = self._now()
        today = now.date()
        for code in self._by_failures(live):
            meta = backfill_meta(self._data, code)
            daily_due = needs_previous_month_refresh(today, meta)
            tail_due = needs_previous_month_tail(now, meta, code_days(self._data, code))
            if not daily_due and not tail_due:
                continue
            previous = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            mark_tail_tried(meta, now)
            self._dirty = True
            try:
                rows = await self._async_fetch_month(code, previous)
            except EvnApiError:
                _LOGGER.debug("EVN previous-month refresh skipped because EVN is unavailable")
                record_failure(meta)
                return
            merge_daily(self._data, code, rows, self._unreported_from())
            if daily_due:
                mark_previous_month_refreshed(meta, today)

    async def _async_backfill(self, live: Sequence[str]) -> None:
        """Walk one code back month by month; at most one code with requests per cycle."""
        today = self.today()
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
        entry_id=entry_id,
        importer=importer,
        tz_provider=lambda: dt_util.DEFAULT_TIME_ZONE,
        now_provider=dt_util.now,
    )
