"""快捷按钮(NOTIFY / CONFIG_SET 通路:按键注入 + 功能触发 + 几何调整)。"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EpsonApiError
from .const import DEFAULT_NAME, DOMAIN, QC_SHIFT_STEP, QC_ZOOM_STEP
from .coordinator import Ef50Coordinator

# (后缀, 翻译键, 图标, 命令 ID, params)
BUTTON_DEFS: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("home", "home", "mdi:home", "NOTIFY_KEYEVENT", "3"),
    ("back", "back", "mdi:arrow-left", "NOTIFY_KEYEVENT", "4"),
    ("menu", "menu", "mdi:menu", "NOTIFY_KEYEVENT", "82"),
    # 243 = KEYCODE_TV_INPUT_HDMI_1 — 实验性,固件可能忽略
    ("input_source", "input_source", "mdi:video-input-hdmi", "NOTIFY_KEYEVENT", "243"),
    ("auto_focus", "auto_focus", "mdi:auto-fix", "NOTIFY_AUTO_FOCUS", None),
    ("patternless_auto_focus", "patternless_auto_focus", "mdi:focus-auto",
     "NOTIFY_PATTERNLESS_AUTO_FOCUS", None),
    # 以下为固件功能触发(NOTIFY_APP_* / 校正触发),效果以机身行为为准
    ("floating_settings", "floating_settings", "mdi:tune-vertical",
     "NOTIFY_APP_FLOATING_SETTINGS", None),
    ("manual_focus", "manual_focus", "mdi:focus-auto",
     "NOTIFY_APP_MANUAL_FOCUS", None),
    ("manual_qc", "manual_qc", "mdi:vector-square",
     "NOTIFY_APP_MANUAL_QC", None),
    ("manual_shift", "manual_shift", "mdi:arrow-expand-horizontal",
     "NOTIFY_APP_MANUAL_SHIFT", None),
    ("manual_zoom", "manual_zoom", "mdi:magnify-plus-outline",
     "NOTIFY_APP_MANUAL_ZOOM", None),
    ("panel_alignment", "panel_alignment", "mdi:grid",
     "NOTIFY_APP_PANELALIGNMENT", None),
    ("screen_fit", "screen_fit", "mdi:monitor-screenshot",
     "NOTIFY_APP_SCREENFIT", None),
    # engine_refresh(NOTIFY_APP_REFRESH)不暴露:该通知只是刷新确认的启动
    # 标记,单独发送无任何动作;真正开始刷新 = SET_REFRESH "1" 且随后自动
    # 关机,不宜作为按钮。
    ("btspk_mode", "btspk_mode", "mdi:speaker-bluetooth",
     "NOTIFY_APP_BTSPK_MODE", None),
    ("error_warning", "error_warning", "mdi:alert-circle-outline",
     "NOTIFY_APP_ERROR_WARNING", None),
    ("wall_color_fit", "wall_color_fit", "mdi:palette",
     "NOTIFY_AUTO_FIT_WALL_COLOR", None),
    ("camera_calibration", "camera_calibration", "mdi:camera-iris",
     "NOTIFY_CAMERA_CALIBRATION", None),
    ("depth_gsensor_calibration", "depth_gsensor_calibration", "mdi:axis-z-rotate-clockwise",
     "NOTIFY_DEPTH_GSENSOR_CALIBRATION", None),
    ("framefit", "framefit", "mdi:crop-free", "NOTIFY_FRAMEFIT", None),
    ("generate_camera_pattern", "generate_camera_pattern", "mdi:image-filter-center-focus",
     "NOTIFY_GENERATE_CAMERA_PATTERN", None),
    ("smart_eye_protect_run", "smart_eye_protect_run", "mdi:eye-outline",
     "NOTIFY_SMART_EYE_PROTECTION", None),
    # 以下两个参数格式为 size=2, params="1 1",真机实测返回 SUCCESS:
    ("auto_correction_run", "auto_correction_run", "mdi:crop-rotate",
     "NOTIFY_AUTO_CORRECTION", "1 1"),
    ("obstacle_avoidance_run", "obstacle_avoidance_run", "mdi:cube-scan",
     "NOTIFY_OBSTACLE_AVOIDANCE", "1 1"),
    # SET_QC 子命令表(真机 ± 回环零残差验证)。变焦/位移为遥控器等价的
    # 常规用户功能,可逆。调整模式 "4 1" 包裹经真机验证非必需,直接发步进。
    ("zoom_in", "zoom_in", "mdi:magnify-plus",
     "CONFIG_SET_QC", f"1 {QC_ZOOM_STEP}"),
    ("zoom_out", "zoom_out", "mdi:magnify-minus",
     "CONFIG_SET_QC", f"1 {-QC_ZOOM_STEP}"),
    ("shift_up", "shift_up", "mdi:arrow-expand-up",
     "CONFIG_SET_QC", f"2 0 {-QC_SHIFT_STEP}"),
    ("shift_down", "shift_down", "mdi:arrow-expand-down",
     "CONFIG_SET_QC", f"2 0 {QC_SHIFT_STEP}"),
    ("shift_left", "shift_left", "mdi:arrow-expand-left",
     "CONFIG_SET_QC", f"2 {-QC_SHIFT_STEP} 0"),
    ("shift_right", "shift_right", "mdi:arrow-expand-right",
     "CONFIG_SET_QC", f"2 {QC_SHIFT_STEP} 0"),
    # "5" = 重置全部几何校正(与机身「重置校正」同款;会清梯形校正,
    # 可用上方「自动校正触发」按钮重建)。作为变焦/位移误操作的逃生口。
    ("geometry_reset", "geometry_reset", "mdi:restore", "CONFIG_SET_QC", "5"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            Ef50Button(entry, data["coordinator"], data["base_id"],
                       suffix, key, icon, cmd, params)
            for suffix, key, icon, cmd, params in BUTTON_DEFS
        ]
    )


class Ef50Button(CoordinatorEntity[Ef50Coordinator], ButtonEntity):
    """发送一条 NOTIFY 命令(按键注入或功能触发)。"""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: Ef50Coordinator,
        base_id: str,
        suffix: str,
        key: str,
        icon: str,
        cmd_id: str,
        params: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._cmd_id = cmd_id
        self._params = params
        self._attr_translation_key = key
        self._attr_unique_id = f"{base_id}-button-{suffix}"
        self._attr_icon = icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=entry.data.get("name", DEFAULT_NAME),
            manufacturer="EPSON",
            model=(coordinator.data or {}).get("product") or "Epson EF Projector",
        )

    @property
    def available(self) -> bool:
        # 离线时保留按钮,按下会得到明确错误而不是被禁用
        return True

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.request(self._cmd_id, params=self._params)
        except EpsonApiError as err:
            raise HomeAssistantError(
                f"按钮 {self._attr_translation_key} 执行失败: {err}"
            ) from err
