"""Diagnostic sensors from the native pjctl API."""
from __future__ import annotations

from typing import Any

from homeassistant.const import EntityCategory, UnitOfLength
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN
from .coordinator import Ef50Coordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            Ef50LightHoursSensor(entry, data["coordinator"], data["base_id"]),
            Ef50DistanceSensor(entry, data["coordinator"], data["base_id"]),
        ]
    )


class _Ef50Sensor(CoordinatorEntity[Ef50Coordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, entry: ConfigEntry, coordinator: Ef50Coordinator, base_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{base_id}-{self._suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class Ef50LightHoursSensor(_Ef50Sensor):
    """Lamp/light usage hours (only readable while the projector is awake)."""

    _suffix = "light_hours"
    _attr_translation_key = "light_hours"
    _attr_native_unit_of_measurement = "h"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("light_hours")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {
            "raw_extra": data.get("light_extra"),
            # True while the value is carried over from the last awake poll.
            "stale": data.get("state") != "on",
        }
        measured = data.get("light_measured_at")
        if measured is not None:
            attrs["measured_at"] = (
                measured.isoformat() if hasattr(measured, "isoformat") else measured
            )
        return attrs




class Ef50DistanceSensor(_Ef50Sensor):
    """Distance to the screen (CONFIG_GET_MEASURE_DISTANCE, millimetres).

    仅唤醒时可测;暴露为米,原始毫米值放属性。
    """

    _suffix = "distance"
    _attr_translation_key = "distance"
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | None:
        raw = ((self.coordinator.data or {}).get("diag") or {}).get("distance_mm")
        if raw is None:
            return None
        try:
            return round(float(raw) / 1000.0, 3)
        except ValueError:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        diag = data.get("diag") or {}
        attrs: dict[str, Any] = {}
        if diag.get("distance_mm") is not None:
            attrs["distance_mm"] = diag["distance_mm"]
        if data.get("state") != "on":
            attrs["stale"] = True
        return attrs
