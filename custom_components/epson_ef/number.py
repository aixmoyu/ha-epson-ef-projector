"""number 实体:光源亮度 + 刷新定时器/手动对焦 + 色彩增益×3/氛围灯亮度。"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EpsonApiError
from .const import (
    AMBIENT_BRIGHTNESS_MAX,
    AMBIENT_BRIGHTNESS_MIN,
    AMBIENT_BRIGHTNESS_STEP,
    COLOR_GAIN_MAX,
    COLOR_GAIN_MIN,
    DEFAULT_NAME,
    DOMAIN,
    FOCUS_MAX,
    FOCUS_MIN,
    LIGHT_OUTPUT_MAX,
    LIGHT_OUTPUT_MIN,
    LIGHT_OUTPUT_STEP,
    REFRESH_TIMER_MAX,
    REFRESH_TIMER_MIN,
    REFRESH_TIMER_STEP,
)
from .coordinator import Ef50Coordinator

_LOGGER = logging.getLogger(__name__)

_COLOR_GAIN_CHANNELS = [
    ("color_gain_red", "mdi:palette", 0),
    ("color_gain_green", "mdi:palette", 1),
    ("color_gain_blue", "mdi:palette", 2),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    entities = [
        Ef50LightOutputNumber(entry, data["coordinator"], data["base_id"]),
        Ef50RefreshTimerNumber(entry, data["coordinator"], data["base_id"]),
        Ef50FocusNumber(entry, data["coordinator"], data["base_id"]),
        Ef50AmbientBrightnessNumber(entry, data["coordinator"], data["base_id"]),
    ]
    entities.extend(
        Ef50ColorGainNumber(entry, data["coordinator"], data["base_id"], key, icon, ch)
        for key, icon, ch in _COLOR_GAIN_CHANNELS
    )
    async_add_entities(entities)


class Ef50LightOutputNumber(
    CoordinatorEntity[Ef50Coordinator], NumberEntity, RestoreEntity
):
    """光源亮度 50–100(步进 5,与机身菜单滑条一致)。

    SET_LIGHT_OUTPUT 写入后设备立即生效(实测 100→55→100 回环);
    当前值来自慢速诊断轮询(settings["light_output"]),写后先乐观更新
    再等协调器确认。固件待机时写会 FAIL,实体会报错并保持原值。
    """

    _attr_has_entity_name = True
    _attr_translation_key = "light_output"
    _attr_native_min_value = LIGHT_OUTPUT_MIN
    _attr_native_max_value = LIGHT_OUTPUT_MAX
    _attr_native_step = LIGHT_OUTPUT_STEP
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-percent"

    def __init__(
        self, entry: ConfigEntry, coordinator: Ef50Coordinator, base_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{base_id}-number-light_output"
        self._attr_native_value: float | None = None
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
            try:
                self._attr_native_value = float(last_state.state)
            except ValueError:
                pass

    @property
    def native_value(self) -> float | None:
        # 轮询值优先(真状态);轮询还没覆盖时回退到乐观/恢复值
        raw = ((self.coordinator.data or {}).get("settings") or {}).get(
            "light_output"
        )
        if raw is not None:
            try:
                return float(raw)
            except ValueError:
                pass
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        ivalue = int(value)
        try:
            await self.coordinator.api.set_light_output(ivalue)
        except EpsonApiError as err:
            raise HomeAssistantError(f"设置光源亮度失败: {err}") from err
        settings = (self.coordinator.data or {}).setdefault("settings", {})
        settings["light_output"] = str(ivalue)
        self._attr_native_value = float(ivalue)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw = ((self.coordinator.data or {}).get("settings") or {}).get(
            "light_output"
        )
        return {"raw_value": raw}


class Ef50RefreshTimerNumber(
    CoordinatorEntity[Ef50Coordinator], NumberEntity, RestoreEntity
):
    """刷新模式定时器(小时,1–24)。

    GET_REFRESH 返回的是定时器小时数(非 0/1),对应机身「定时器(小时)」
    滑条(min 1 / max 24);写通路为 SET_REFRESH params="0 <h>"(size=2),
    变值回环 1→4→12→1 实测。
    ⚠️ 故意不暴露 SET_REFRESH "1" —— 那是「立即开始刷新并关机」。
    """

    _attr_has_entity_name = True
    _attr_translation_key = "refresh_timer"
    _attr_native_min_value = REFRESH_TIMER_MIN
    _attr_native_max_value = REFRESH_TIMER_MAX
    _attr_native_step = REFRESH_TIMER_STEP
    _attr_native_unit_of_measurement = "h"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-cog-outline"

    def __init__(
        self, entry: ConfigEntry, coordinator: Ef50Coordinator, base_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{base_id}-number-refresh_timer"
        self._attr_native_value: float | None = None
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
            try:
                self._attr_native_value = float(last_state.state)
            except ValueError:
                pass

    @property
    def native_value(self) -> float | None:
        raw = ((self.coordinator.data or {}).get("settings") or {}).get(
            "refresh_timer"
        )
        if raw is not None:
            try:
                return float(raw)
            except ValueError:
                pass
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # 与 light_output / focus_position 保持一致:有读通路的实体都暴露
        # 固件原始值,便于用户对照机身菜单核对。
        raw = ((self.coordinator.data or {}).get("settings") or {}).get(
            "refresh_timer"
        )
        return {"raw_value": raw}

    async def async_set_native_value(self, value: float) -> None:
        ivalue = int(value)
        try:
            await self.coordinator.api.set_refresh_timer(ivalue)
        except EpsonApiError as err:
            raise HomeAssistantError(f"设置刷新定时器失败: {err}") from err
        settings = (self.coordinator.data or {}).setdefault("settings", {})
        settings["refresh_timer"] = str(ivalue)
        self._attr_native_value = float(ivalue)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class Ef50FocusNumber(CoordinatorEntity[Ef50Coordinator], NumberEntity):
    """手动对焦位置(CONFIG_GET_FOCUS "POS" / SET_FOCUS "INC"/"DEC")。

    - GET_FOCUS params="RANGE" → 行程 "<min> <max>"(HB71 = 0 264)
    - GET_FOCUS params="POS"   → 当前位置
    - SET_FOCUS params="INC"/"DEC" → 单步移动(步长非线性,见 const.py)
    无参数或数字参数会走引擎查询路径并挂住服务;"RANGE"/"POS" 是快速子命令。
    固件无绝对定位命令,故用连续移动 + 定时 + 回读的闭环逼近(见 api.goto_focus)。
    """

    _attr_has_entity_name = True
    _attr_translation_key = "focus_position"
    _attr_native_min_value = FOCUS_MIN
    _attr_native_max_value = FOCUS_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:focus-field"

    def __init__(
        self, entry: ConfigEntry, coordinator: Ef50Coordinator, base_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{base_id}-number-focus_position"
        self._attr_native_value: float | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    @property
    def available(self) -> bool:
        # 对焦读数只在唤醒状态有效,跟随协调器在线状态
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        raw = ((self.coordinator.data or {}).get("settings") or {}).get(
            "focus_position"
        )
        if raw is not None:
            try:
                return float(raw)
            except ValueError:
                pass
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "focus_range": (data.get("diag") or {}).get("focus_range"),
            "raw_value": (data.get("settings") or {}).get("focus_position"),
        }

    async def async_set_native_value(self, value: float) -> None:
        target = int(value)
        api = self.coordinator.api
        try:
            # 闭环绝对定位:连续移动(MAX/MIN)+ 按剩余距离定时 + OFF + 回读。
            # 旧实现假设"每步 10 个单位",按步数换算 —— 实测固件在换向后
            # 首步 ±10、同向后续仅 ±1,导致远距离目标永远到不了
            # (目标 150 从 89 出发只到 128)。详见 const.py 的对焦注释。
            final = await api.goto_focus(target)
        except EpsonApiError as err:
            raise HomeAssistantError(f"对焦调整失败: {err}") from err
        if final is None:
            raise HomeAssistantError(
                "无法读取对焦位置(投影仪需处于唤醒状态)"
            )
        settings = (self.coordinator.data or {}).setdefault("settings", {})
        settings["focus_position"] = str(final)
        self._attr_native_value = float(final)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class Ef50ColorGainNumber(CoordinatorEntity[Ef50Coordinator], NumberEntity):
    """色彩增益 R/G/B 通道(CONFIG_GET/SET_COLOR_GAIN)。

    - GET(params="0")→ "R G B" 三元组(实机出厂 81 90 100)
    - SET params="R G B"(size=3),0–255 全范围精确读写
      (0/1/50/150/200/255 边界回环实测),不受投影偏好切换影响。
    注意:机身菜单的「色彩调谐器」滑条走 Android 全局设置,与本命令不同源。
    三个实体共享一条 GET(慢速诊断轮询 diag.color_gain),写入为
    "读现值→替换单通道→写整串"。过度提升会破坏白平衡,请以实测/校色
    仪为准小幅调整。
    """

    _attr_has_entity_name = True
    _attr_native_min_value = COLOR_GAIN_MIN
    _attr_native_max_value = COLOR_GAIN_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: Ef50Coordinator,
        base_id: str,
        key: str,
        icon: str,
        channel: int,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._channel = channel
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_unique_id = f"{base_id}-number-{key}"
        self._attr_native_value: float | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    def _triple(self) -> tuple[int, int, int] | None:
        raw = ((self.coordinator.data or {}).get("diag") or {}).get("color_gain")
        if not raw:
            return None
        parts = raw.split()
        if len(parts) != 3:
            return None
        try:
            return int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            return None

    @property
    def native_value(self) -> float | None:
        triple = self._triple()
        if triple is not None:
            return float(triple[self._channel])
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # 三个通道共享同一条 GET,原始三元组对排障很有用(换色偏时能直接
        # 看出另外两个通道的值),所以每个实体都暴露完整固件原始值。
        triple = self._triple()
        return {
            "raw_value": " ".join(str(v) for v in triple) if triple else None,
        }

    async def async_set_native_value(self, value: float) -> None:
        ivalue = int(value)
        current = self._triple()
        if current is None:
            raise HomeAssistantError(
                "无法读取当前色彩增益(投影仪需处于唤醒状态并完成一次诊断轮询)"
            )
        rgb = list(current)
        rgb[self._channel] = ivalue
        try:
            await self.coordinator.api.set_color_gain(*rgb)
        except EpsonApiError as err:
            raise HomeAssistantError(f"设置色彩增益失败: {err}") from err
        diag = (self.coordinator.data or {}).setdefault("diag", {})
        diag["color_gain"] = f"{rgb[0]} {rgb[1]} {rgb[2]}"
        self._attr_native_value = float(ivalue)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class Ef50AmbientBrightnessNumber(
    CoordinatorEntity[Ef50Coordinator], NumberEntity, RestoreEntity
):
    """氛围灯亮度(CONFIG_SET_AMBIENT_LIGHT "9 <0-255> 48")。

    固件亮度 0–255,UI 0–100 线性映射(×2.55,与官方小程序一致)。
    无读通路(GET_AMBIENT_LIGHT 本机全 FAIL),实体为乐观状态
    (RestoreEntity + assumed_state)。
    """

    _attr_has_entity_name = True
    _attr_translation_key = "ambient_brightness"
    _attr_native_min_value = AMBIENT_BRIGHTNESS_MIN
    _attr_native_max_value = AMBIENT_BRIGHTNESS_MAX
    _attr_native_step = AMBIENT_BRIGHTNESS_STEP
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:lightbulb-on-50"

    def __init__(
        self, entry: ConfigEntry, coordinator: Ef50Coordinator, base_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{base_id}-number-ambient_brightness"
        self._attr_native_value: float | None = None
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
            try:
                self._attr_native_value = float(last_state.state)
            except ValueError:
                pass

    @property
    def assumed_state(self) -> bool:
        # 状态不可读,明确标记为假设状态
        return True

    async def async_set_native_value(self, value: float) -> None:
        ivalue = int(value)
        try:
            await self.coordinator.api.set_ambient_brightness(ivalue)
        except EpsonApiError as err:
            raise HomeAssistantError(
                f"设置氛围灯亮度失败(投影仪需处于唤醒状态): {err}"
            ) from err
        self._attr_native_value = float(ivalue)
        self.async_write_ha_state()
