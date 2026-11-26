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
#
# 曝光规则(0.0.4 起,按 HACS 评审意见收紧):只暴露"按键注入/步进"这一类
# 按钮 —— 效果在真机上即时可观察,且可用反向操作撤销。home/back/menu 是
# 遥控器同款键注入;变焦与位移走 CONFIG_SET_QC 步进,经真机 ± 回环零残差
# 验证;auto_focus 是单次镜头对焦,可重复执行。
# 其余一律不暴露:凡是会重写几何校正、标定状态或整机模式的命令,在真机
# 确认前都不做成按钮 —— "返回 SUCCESS" 只证明 HTTP 通路打通,不证明动作
# 真的发生在用户设备上,而后者的代价不可逆。
# 判据是"本仓库能否拿出真机证据",不是"看着应该没事":注释里只写了
# 作用、没写实测结论的命令,一律按未验证处理。
#
# 未列入本表的命令及其原因(禁止凭命名推测加回):
#   - NOTIFY_KEYEVENT "243" (KEYCODE_TV_INPUT_HDMI_1): 实验性,固件可能忽略。
#   - NOTIFY_PATTERNLESS_AUTO_FOCUS: 无图案自动对焦,效果未在真机确认。
#   - NOTIFY_APP_MANUAL_ZOOM / _MANUAL_FOCUS / _MANUAL_QC / _MANUAL_SHIFT:
#     只是把用户送进机身的手动调整界面;本集成已有等价的变焦/位移/对焦
#     实体,重复且效果未逐项确认。
#   - NOTIFY_APP_FLOATING_SETTINGS / NOTIFY_APP_ERROR_WARNING: 只唤出机身
#     界面,按说无副作用,但注释里从未写下实测结论,同样按未验证处理。
#     确认"按下后界面确实弹出且无其它状态变化"后可加回。
#   - NOTIFY_APP_PANELALIGNMENT / _SCREENFIT: 会重写面板对齐与画面适配的
#     几何结果,后果不可逆,确认前不暴露。
#   - NOTIFY_APP_BTSPK_MODE: 把整机切到蓝牙音箱模式,可能断开当前网络会
#     话,未在真机确认前不暴露。
#   - NOTIFY_CAMERA_CALIBRATION / NOTIFY_DEPTH_GSENSOR_CALIBRATION: 会改写
#     用户真机上的标定状态,后果不可逆,确认前不暴露。
#   - NOTIFY_FRAMEFIT / NOTIFY_GENERATE_CAMERA_PATTERN /
#     NOTIFY_SMART_EYE_PROTECTION: 效果未在真机确认。
#   - NOTIFY_AUTO_FIT_WALL_COLOR: 重写墙面颜色校正结果,属标定类。
#   - NOTIFY_AUTO_CORRECTION / NOTIFY_OBSTACLE_AVOIDANCE: 重写梯形与避障
#     校正结果,属标定类。
#   - CONFIG_SET_QC "5" (geometry_reset): 一次性清空全部几何校正。动作本身
#     可解释,但上方两条自动校正移出后,保留它就等于留一个没有回退路径的
#     单向破坏性按钮,故一并移出;需要重置时用机身菜单或遥控器。
#     api.py 的 geometry_reset() 方法保留,供工具脚本与排障使用。
BUTTON_DEFS: tuple[tuple[str, str, str, str, str | None], ...] = (
    # A. 按键注入(遥控器等价)
    ("home", "home", "mdi:home", "NOTIFY_KEYEVENT", "3"),
    ("back", "back", "mdi:arrow-left", "NOTIFY_KEYEVENT", "4"),
    ("menu", "menu", "mdi:menu", "NOTIFY_KEYEVENT", "82"),
    # 单次镜头自动对焦:结果肉眼可见、可重复执行,不写标定或几何状态
    ("auto_focus", "auto_focus", "mdi:auto-fix", "NOTIFY_AUTO_FOCUS", None),
    # A. 几何步进:SET_QC 子命令经真机 ± 回环零残差验证,正反向可撤销。
    # 调整模式 "4 1" 包裹经真机验证非必需,直接发步进。
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
