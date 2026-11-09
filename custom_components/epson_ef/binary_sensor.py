"""Connectivity sensor for automations (offline detection)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
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
        [Ef50ConnectivitySensor(entry, data["coordinator"], data["base_id"])]
    )


class Ef50ConnectivitySensor(CoordinatorEntity[Ef50Coordinator], BinarySensorEntity):
    """On while the projector's HTTP API answers."""

    _attr_has_entity_name = True
    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self, entry: ConfigEntry, coordinator: Ef50Coordinator, base_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{base_id}-connectivity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    @property
    def available(self) -> bool:
        # A connectivity sensor must stay available so it can report "off".
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success
