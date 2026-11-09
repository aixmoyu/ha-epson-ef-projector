"""可写配置开关(NOTIFY/CONFIG_SET 通路,同值回写法实测)。"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EpsonApiError
from .const import (
    AMBIENT_LIGHT_SWITCHES,
    DEFAULT_NAME,
    DOMAIN,
    WRITABLE_SWITCHES,
)
from .coordinator import Ef50Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            Ef50ConfigSwitch(entry, data["coordinator"], data["base_id"], suffix,
                             key, get_cmd, set_cmd)
            for suffix, key, get_cmd, set_cmd in WRITABLE_SWITCHES
        ]
    )
    async_add_entities(
        [
            Ef50AmbientLightSwitch(entry, data["coordinator"], data["base_id"],
                                   suffix, key, icon, kind)
            for suffix, key, icon, kind in AMBIENT_LIGHT_SWITCHES
        ]
    )
    async_add_entities(
        [Ef50TestPatternSwitch(entry, data["coordinator"], data["base_id"])]
    )
    async_add_entities(
        [Ef50AutoContrastSwitch(entry, data["coordinator"], data["base_id"])]
    )


class Ef50ConfigSwitch(CoordinatorEntity[Ef50Coordinator], SwitchEntity):
    """0/1 型固件设置开关。"""

    _attr_has_entity_name = True
    _attr_entity_category = None  # 用户可操作的开关,放常规分组

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: Ef50Coordinator,
        base_id: str,
        suffix: str,
        key: str,
        get_cmd: str,
        set_cmd: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._suffix = suffix
        self._get_cmd = get_cmd
        self._set_cmd = set_cmd
        self._attr_translation_key = key
        self._attr_unique_id = f"{base_id}-setting-{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    def _raw(self) -> str | None:
        return ((self.coordinator.data or {}).get("settings") or {}).get(self._suffix)

    @property
    def is_on(self) -> bool | None:
        raw = self._raw()
        if raw is None:
            return None
        return raw == "1"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"raw_value": self._raw()}

    async def _write(self, value: str) -> None:
        try:
            await self.coordinator.api.request(self._set_cmd, params=value)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"写入 {self._suffix} 失败: {err}") from err
        settings = (self.coordinator.data or {}).setdefault("settings", {})
        settings[self._suffix] = value
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write("1")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write("0")


class Ef50AmbientLightSwitch(
    CoordinatorEntity[Ef50Coordinator], SwitchEntity, RestoreEntity
):
    """氛围灯开关(CONFIG_SET_AMBIENT_LIGHT,写-only 乐观状态)。

    固件的 GET_AMBIENT_LIGHT 子索引在本机全部 FAIL(机身 UI 读侧也只回
    静态默认值),因此状态不可读:开关用 RestoreEntity 保存最后已知状态,
    写入成功后乐观翻转。写格式:
    kind 3 → "3 <01> 48"(开/关灯)、4 → "4 <01>"(开机自动光效)、
    5 → "5 <01>"(触控面板);写命令均返回 SUCCESS。
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: Ef50Coordinator,
        base_id: str,
        suffix: str,
        key: str,
        icon: str,
        kind: int,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._kind = kind
        self._attr_translation_key = key
        self._attr_unique_id = f"{base_id}-setting-{suffix}"
        self._attr_icon = icon
        self._attr_is_on: bool | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            "unknown", "unavailable",
        ):
            self._attr_is_on = last_state.state == "on"

    @property
    def assumed_state(self) -> bool:
        # 状态不可读,明确标记为假设状态
        return True

    async def _write(self, on: bool) -> None:
        try:
            await self.coordinator.api.set_ambient_light(self._kind, on)
        except EpsonApiError as err:
            raise HomeAssistantError(
                f"氛围灯写入失败(投影仪需处于唤醒状态): {err}"
            ) from err
        self._attr_is_on = on
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)


class Ef50AutoContrastSwitch(
    CoordinatorEntity[Ef50Coordinator], SwitchEntity, RestoreEntity
):
    """引擎自动对比度开关(CONFIG_SET_AUTO_CONTRAST,写-only 乐观状态)。

    写通路真机验证(SET "0"/"1" 均 SUCCESS),读通路 GET_AUTO_CONTRAST
    带参 FAIL(HB71 无读通路),与氛围灯开关同款乐观模式
    (RestoreEntity + assumed_state)。注意:机身菜单「动态对比度」是
    另一个命令(DYNAMIC_CONTRAST,select 实体),本开关是引擎侧自动对比度。
    ⚠️ 效果未可感知验证:实机对比观察不明显。
    """

    _attr_has_entity_name = True
    _attr_translation_key = "auto_contrast"
    _attr_icon = "mdi:contrast-circle"

    def __init__(
        self, entry: ConfigEntry, coordinator: Ef50Coordinator, base_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{base_id}-setting-auto_contrast"
        self._attr_is_on: bool | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            "unknown", "unavailable",
        ):
            self._attr_is_on = last_state.state == "on"

    @property
    def assumed_state(self) -> bool:
        # 读通路 FAIL,状态不可读,明确标记为假设状态
        return True

    async def _write(self, on: bool) -> None:
        try:
            await self.coordinator.api.set_auto_contrast(on)
        except EpsonApiError as err:
            raise HomeAssistantError(
                f"自动对比度写入失败(投影仪需处于唤醒状态): {err}"
            ) from err
        self._attr_is_on = on
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)


class Ef50TestPatternSwitch(
    CoordinatorEntity[Ef50Coordinator], SwitchEntity, RestoreEntity
):
    """测试图案(GET_ESCVP 透传 MCU「AGPATTERN 01/00」)。

    AGPATTERN 是 MCU 内置的检验图案发生器,无需解锁 debug 模式即可透传,
    01=显示图案、00=恢复画面(写命令均 SUCCESS)。用于对焦/光学校正时
    投射参照图案。图案外观未逐码确认,只暴露 01 这一档;若图案残留,
    关闭本开关即恢复。
    """

    _attr_has_entity_name = True
    _attr_translation_key = "test_pattern"
    _attr_icon = "mdi:grid"

    def __init__(
        self, entry: ConfigEntry, coordinator: Ef50Coordinator, base_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{base_id}-setting-test_pattern"
        self._attr_is_on: bool | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            "unknown", "unavailable",
        ):
            self._attr_is_on = last_state.state == "on"

    @property
    def assumed_state(self) -> bool:
        # 图案显示状态不可读,明确标记为假设状态
        return True

    async def _write(self, on: bool) -> None:
        try:
            await self.coordinator.api.set_test_pattern(on)
        except EpsonApiError as err:
            raise HomeAssistantError(
                f"测试图案切换失败(投影仪需处于唤醒状态): {err}"
            ) from err
        self._attr_is_on = on
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)
