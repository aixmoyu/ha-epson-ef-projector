"""Writable projector settings exposed as selects."""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EpsonApiError
from .const import (
    AMBIENT_MODE_OPTIONS,
    DEFAULT_NAME,
    DOMAIN,
    WRITABLE_SELECTS,
    standby_option_to_raw,
    standby_raw_to_option,
)
from .coordinator import Ef50Coordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [Ef50StandbyModeSelect(entry, data["coordinator"], data["base_id"])]
    )
    async_add_entities(
        [
            Ef50ConfigSelect(entry, data["coordinator"], data["base_id"],
                             suffix, key, get_cmd, set_cmd, options, labels)
            for suffix, key, get_cmd, set_cmd, options, labels in WRITABLE_SELECTS
        ]
    )
    async_add_entities(
        [Ef50AmbientModeSelect(entry, data["coordinator"], data["base_id"])]
    )


class Ef50ConfigSelect(CoordinatorEntity[Ef50Coordinator], SelectEntity):
    """通用固件设置选择器。

    有标签映射时选项显示语义名(如 正投/吊装),原始 API 值保留在
    extra_state_attributes.raw_value;无映射时选项即原始值。
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: Ef50Coordinator,
        base_id: str,
        suffix: str,
        key: str,
        get_cmd: str,
        set_cmd: str,
        raw_options: list[str],
        labels: dict[str, str] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._suffix = suffix
        self._get_cmd = get_cmd
        self._set_cmd = set_cmd
        self._labels = labels or {}
        self._attr_translation_key = key
        self._attr_unique_id = f"{base_id}-setting-{suffix}"
        self._attr_options = (
            [self._labels[r] for r in raw_options] if self._labels else raw_options
        )
        self._raw_to_option = dict(zip(raw_options, self._attr_options))
        self._option_to_raw = dict(zip(self._attr_options, raw_options))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def current_option(self) -> str | None:
        raw = ((self.coordinator.data or {}).get("settings") or {}).get(self._suffix)
        if raw is None:
            return None
        return self._raw_to_option.get(raw, raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = ((self.coordinator.data or {}).get("settings") or {}).get(self._suffix)
        return {"raw_value": raw}

    async def async_select_option(self, option: str) -> None:
        raw = self._option_to_raw.get(option, option)
        try:
            await self.coordinator.api.request(self._set_cmd, params=raw)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"写入 {self._suffix} 失败: {err}") from err
        settings = (self.coordinator.data or {}).setdefault("settings", {})
        settings[self._suffix] = raw
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class Ef50StandbyModeSelect(CoordinatorEntity[Ef50Coordinator], SelectEntity):
    """启动模式:节能 / 快速启动 (CONFIG_SET_PJ_STANDBY_MODE)."""

    _attr_has_entity_name = True
    _attr_translation_key = "standby_mode"
    _attr_icon = "mdi:projector"
    _attr_options = ["节能", "快速启动"]

    def __init__(
        self, entry: ConfigEntry, coordinator: Ef50Coordinator, base_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._base_id = base_id
        self._attr_unique_id = f"{base_id}-standby_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def current_option(self) -> str | None:
        raw = (self.coordinator.data or {}).get("standby_mode")
        return standby_raw_to_option(raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = (self.coordinator.data or {}).get("standby_mode")
        return {"raw_value": raw}

    async def async_select_option(self, option: str) -> None:
        raw = standby_option_to_raw(option)
        if raw is None:
            raise HomeAssistantError(f"未知的启动模式选项: {option}")
        try:
            await self.coordinator.api.set_standby_mode(raw)
        except EpsonApiError as err:
            raise HomeAssistantError(f"设置启动模式失败: {err}") from err
        # Optimistically reflect the new value, then let the coordinator confirm.
        if self.coordinator.data is not None:
            self.coordinator.data["standby_mode"] = raw
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class Ef50AmbientModeSelect(
    CoordinatorEntity[Ef50Coordinator], SelectEntity, RestoreEntity
):
    """氛围灯光效模式(CONFIG_SET_AMBIENT_LIGHT "1 <mode> 48")。

    MODE1..4 = 固件光效槽 3..6(槽 11 是小程序的临时预览配置,故意不暴露)。
    无读通路,实体为乐观状态。
    """

    _attr_has_entity_name = True
    _attr_translation_key = "ambient_mode"
    _attr_icon = "mdi:wall-sconce-round"
    _attr_options = [label for label, _mode in AMBIENT_MODE_OPTIONS]

    def __init__(
        self, entry: ConfigEntry, coordinator: Ef50Coordinator, base_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._option_to_mode = dict(AMBIENT_MODE_OPTIONS)
        self._attr_current_option: str | None = None
        self._attr_unique_id = f"{base_id}-select-ambient_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self._attr_current_option = last_state.state

    @property
    def assumed_state(self) -> bool:
        # 状态不可读,明确标记为假设状态
        return True

    async def async_select_option(self, option: str) -> None:
        mode = self._option_to_mode.get(option)
        if mode is None:
            raise HomeAssistantError(f"未知的氛围灯模式选项: {option}")
        try:
            await self.coordinator.api.set_ambient_mode(mode)
        except EpsonApiError as err:
            raise HomeAssistantError(
                f"设置氛围灯模式失败(投影仪需处于唤醒状态): {err}"
            ) from err
        self._attr_current_option = option
        self.async_write_ha_state()
