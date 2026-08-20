"""Sensor entities for EVN Vietnam customer codes and their local total."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EvnDataUpdateCoordinator, aggregate_customer_codes, configured_customer_codes

_METRICS: tuple[tuple[str, str, SensorDeviceClass | None, str | None, SensorStateClass | None], ...] = (
    ("today_consumption", "Today's consumption", SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR, SensorStateClass.MEASUREMENT),
    ("yesterday_consumption", "Yesterday's consumption", SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR, SensorStateClass.MEASUREMENT),
    ("current_month_consumption", "Current month consumption", SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR, SensorStateClass.MEASUREMENT),
    ("current_month_amount", "Estimated current month cost", SensorDeviceClass.MONETARY, "VND", SensorStateClass.MEASUREMENT),
    ("latest_index", "Latest meter index", None, None, None),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add sensor sets for every configured customer and the computed total."""
    coordinator: EvnDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    codes = configured_customer_codes(entry)
    entities = [EvnSensor(coordinator, entry, code, metric) for code in codes for metric in _METRICS]
    if len(aggregate_customer_codes(entry)) > 1:
        entities.extend(EvnSensor(coordinator, entry, "__aggregate__", metric) for metric in _METRICS if metric[0] != "latest_index")
    async_add_entities(entities)


class EvnSensor(CoordinatorEntity[EvnDataUpdateCoordinator], SensorEntity):
    """A value exposed by the coordinator's legacy-compatible calculation."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EvnDataUpdateCoordinator, entry: ConfigEntry, customer_code: str, metric: tuple[str, str, SensorDeviceClass | None, str | None, SensorStateClass | None]) -> None:
        super().__init__(coordinator)
        key, label, device_class, native_unit, state_class = metric
        self._customer_code, self._metric = customer_code, key
        identifier = "aggregate" if customer_code == "__aggregate__" else customer_code.lower()
        self._attr_unique_id = f"{entry.entry_id}_{identifier}_{key}"
        self._attr_name = label
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = native_unit
        self._attr_state_class = state_class
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_{identifier}")},
            "name": "EVN total" if customer_code == "__aggregate__" else f"EVN {customer_code}",
            "manufacturer": "Electricity of Vietnam",
            "model": "CSKH customer account",
        }

    @property
    def native_value(self) -> Any:
        item = self._source
        return item.get(self._metric) if item else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = self._source
        if not item:
            return {"attribution": ATTRIBUTION}
        attrs: dict[str, Any] = {"attribution": ATTRIBUTION, "customer_code": self._customer_code}
        if self._metric == "current_month_consumption":
            attrs["daily_history"] = item.get("daily_history", [])
            attrs["monthly_history"] = item.get("monthly_history", [])
            attrs["latest_reading"] = item.get("latest_index")
        if self._metric == "current_month_amount":
            attrs["bills"] = item.get("bills", [])
        if self._customer_code == "__aggregate__":
            attrs["selected_customer_codes"] = item.get("selected_customer_codes", [])
            attrs["successful_customer_codes"] = item.get("successful_customer_codes", [])
            attrs["partial_errors"] = item.get("partial_errors", {})
            attrs["is_partial"] = item.get("is_partial", False)
        return attrs

    @property
    def _source(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        if self._customer_code == "__aggregate__":
            return self.coordinator.data.get("aggregate")
        return self.coordinator.data.get("meters", {}).get(self._customer_code)
