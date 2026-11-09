"""Constants for the Epson EF Projector integration."""
from __future__ import annotations

DOMAIN = "epson_ef"

CONF_PORT = "port"
CONF_POLL_INTERVAL = "poll_interval"

DEFAULT_NAME = "Epson EF Projector"
DEFAULT_PORT = 8080
DEFAULT_POLL_INTERVAL = 15

# Native control API (WebApiSrv, fixed by the projector firmware — not user specific)
API_PATH = "/api/pjctl"
DIGEST_USER = "EPSONICL"
DIGEST_REALM = "Secret Zone"
# Credentials are baked into the projector firmware (all HB7x-family units),
# not something tied to a particular installation.
DIGEST_PASSWORD = "qYT_h*Gm+RLx7|,Z!D##q6z%dQSc3vn/"

KEYCODE_SEND = "NOTIFY_KEYEVENT"

# 启动模式(PJ_STANDBY_MODE)取值映射。
# 节能=false、快速启动=true,固件 API 以 0/1 存储。原始值始终保留在实体属性
# raw_value 中,若实际设备映射相反,可通过属性核对。
STANDBY_MODE_OPTIONS: dict[str, str] = {
    "0": "节能",
    "1": "快速启动",
}


def standby_option_to_raw(option: str) -> str | None:
    """Map a human option ('节能'/'快速启动') to the raw API value."""
    for raw, label in STANDBY_MODE_OPTIONS.items():
        if label == option:
            return raw
    return None


def standby_raw_to_option(raw: str | None) -> str | None:
    """Map a raw API value to its human option; unknown values stay None."""
    if raw is None:
        return None
    return STANDBY_MODE_OPTIONS.get(raw)


# 同值回写法实测可写的设置项(0/1 型开关)。
# (唯一后缀, 翻译键, GET 命令, SET 命令)
# 注:refresh 不在此表——GET_REFRESH 返回的是刷新定时器小时数(1–24)而非 0/1;
# SET_REFRESH "1" 是「立即开始刷新并关机」。现为 number 实体(refresh_timer)。
WRITABLE_SWITCHES: list[tuple[str, str, str, str]] = [
    ("auto_correction", "auto_correction",
     "CONFIG_GET_AUTO_CORRECTION", "CONFIG_SET_AUTO_CORRECTION"),
    ("auto_fit_wall_color", "auto_fit_wall_color",
     "CONFIG_GET_AUTO_FIT_WALL_COLOR", "CONFIG_SET_AUTO_FIT_WALL_COLOR"),
    ("movement_sensor", "movement_sensor",
     "CONFIG_GET_MOVEMENT_SENSOR", "CONFIG_SET_MOVEMENT_SENSOR"),
    ("smart_eye_protection", "smart_eye_protection",
     "CONFIG_GET_SMART_EYE_PROTECTION", "CONFIG_SET_SMART_EYE_PROTECTION"),
    ("startup_correction", "startup_correction",
     "CONFIG_GET_STARTUP_CORRECTION", "CONFIG_SET_STARTUP_CORRECTION"),
    ("vmute", "vmute",
     "CONFIG_GET_VMUTE", "CONFIG_SET_VMUTE"),
]

# 多档设置(档位=原始 API 值,语义留待用户对照机身菜单确认)
# 元组结构:(唯一后缀, 翻译键, GET 命令, SET 命令, 原始档位, 档位标签或 None)
# 标签依据机身菜单选项定义:
#   投影方式(正投/吊装/背投/吊装背投),读写回环已实测
#   动态对比度(关/正常/高速)
#   色温的 pjctl 原始值语义尚未确认(机身菜单的标准/暖色/冷色走 Android 全局设置,
#   与 pjctl COLOR_TEMP 数值不同源),故不给标签、保持原始值。
WRITABLE_SELECTS: list[tuple[str, str, str, str, list[str], dict[str, str] | None]] = [
    ("color_temp", "color_temp",
     "CONFIG_GET_COLOR_TEMP", "CONFIG_SET_COLOR_TEMP",
     ["0", "1", "2"], None),
    ("dynamic_contrast", "dynamic_contrast",
     "CONFIG_GET_DYNAMIC_CONTRAST", "CONFIG_SET_DYNAMIC_CONTRAST",
     ["0", "1", "2"], {"0": "关", "1": "正常", "2": "高速"}),
    ("projection", "projection",
     "CONFIG_GET_PROJECTION", "CONFIG_SET_PROJECTION",
     ["0", "1", "2", "3"],
     {"0": "正投", "1": "吊装", "2": "背投", "3": "吊装/背投"}),
    # PICTURE 实为投影偏好,写通路因 size=token 数修复而打通(变值回环
    # 35→6→35 实测):色彩=35 / 明亮=6
    ("picture_preference", "picture_preference",
     "CONFIG_GET_PICTURE", "CONFIG_SET_PICTURE",
     ["35", "6"], {"35": "色彩", "6": "明亮"}),
]

# 光源亮度滑条范围(min 50 / max 100 / step 5,与机身滑条一致)
LIGHT_OUTPUT_MIN = 50
LIGHT_OUTPUT_MAX = 100
LIGHT_OUTPUT_STEP = 5

# 刷新定时器(与机身「定时器(小时)」滑条一致:读 CONFIG_GET_REFRESH,范围 1–24)
REFRESH_TIMER_MIN = 1
REFRESH_TIMER_MAX = 24
REFRESH_TIMER_STEP = 1

# 手动对焦(CONFIG_GET_FOCUS "POS"/"RANGE",SET_FOCUS "INC"/"DEC"/"MIN"/"MAX"/"OFF")。
# HB71 实测 RANGE="0 264"。
#
# ⚠️ 2026-11-07 真机复测纠正了此前对 INC/DEC 步长的假设:
#   - **换向后第一步 ±10**,之后同向每步 **仅 ±1**(与空闲时长无关:
#     静置 15s 后再 INC 仍是 +1,只有方向反转才会让下一步变粗)。
#   例:INC 124→134(+10)、134→135(+1)、135→136(+1)…;
#       DEC 139→129(−10)、129→128(−1)…
#   因此"按 ±10 换算步数"的旧算法无法把焦点送到远距离目标
#   (实测:目标 150、起点 89 时只到 128 就放弃)。
#
# 正确的绝对定位方式(实测验证):SET_FOCUS "MAX"/"MIN" 连续移动、
# "OFF" 停止。闭环"按剩余距离定时 + OFF + 回读 POS"可精确到位
# (目标 60/200/94 误差 0,120 误差 2)。
FOCUS_MIN = 0
FOCUS_MAX = 264
# INC/DEC 单步位移(仅用于需要微调一格时)
FOCUS_STEP_UNIT = 1
# 连续移动速度:实测 MAX 连续移动约 65 单位/秒
FOCUS_TRAVEL_UNITS_PER_SEC = 65.0
# 单次连续移动的时长上下限(秒)
FOCUS_MIN_PULSE_S = 0.10
FOCUS_MAX_PULSE_S = 2.0
# 闭环定位的接受误差(固件 POS 分辨率 1,留 2 个单位的裕量)
FOCUS_TOLERANCE = 3
# 闭环定位最多尝试次数
FOCUS_GOTO_MAX_ATTEMPTS = 14
# 对焦马达等待:每步之后轮询 POS,连续两次读数一致视为停稳
FOCUS_SETTLE_POLL = 0.45

# 氛围灯开关(SET_AMBIENT_LIGHT,写-only:GET 子索引本机全部 FAIL,
# 机身 UI 读侧也只回静态默认值,故开关为乐观状态)。
# (唯一后缀, 翻译键, 图标, 子索引 kind) — kind 3 的写格式 "3 <01> 48"
AMBIENT_LIGHT_SWITCHES: list[tuple[str, str, str, int]] = [
    ("ambient_light", "ambient_light", "mdi:lightbulb-on-outline", 3),
    ("welcome_light", "welcome_light", "mdi:party-popper", 4),
    ("touch_sensor_light", "touch_sensor_light", "mdi:gesture-tap", 5),
]

# SET_AMBIENT_LIGHT 的完整参数表 —— kind token 即首参数:
#   "1 <mode> 48" 切光效模式(MODE1..4=3..6;11=临时预览槽,不暴露)
#   "3 <01> 48"   开/关灯(48=EFFECT_DEFAULT 固件常量)
#   "9 <0-255> 48" 灯效亮度(固件 0–255;UI 0–100 线性映射 ×2.55)
#   "255"         恢复氛围灯出厂设置(会清用户光效配置,不暴露)
# GET_AMBIENT_LIGHT "3"/"4"/"5"/"31"/"32" 为读通路,本机(HB71)全部 FAIL,
# 故相关实体均为乐观状态。
AMBIENT_BRIGHTNESS_MIN = 0
AMBIENT_BRIGHTNESS_MAX = 100
AMBIENT_BRIGHTNESS_STEP = 1
# 固件亮度 0–255 与 UI 0–100 的换算(线性映射)
AMBIENT_BRIGHTNESS_FW_SCALE = 255.0 / 100.0
# 光效模式选项 → SET_AMBIENT_LIGHT 首参数值
AMBIENT_MODE_OPTIONS: list[tuple[str, int]] = [
    ("模式1", 3),
    ("模式2", 4),
    ("模式3", 5),
    ("模式4", 6),
]

# CONFIG_SET_QC 首 token 为子命令,完整参数表(真机 ± 回环验证):
#   "0 <角0-3> <dx> <dy>" (size=4) 角点调整(0=左上/1=右上/2=右下/3=左下,
#       步进 1,1,2,4,7,10,15 随长按重复加速)
#   "1 ±104"  (size=2) 变焦步进,+ = 放大(四角外扩,- = 缩小)
#   "2 dx dy" (size=3) 画面位移,单步 13(+x=右,+y=下)
#   "3 0/1"   (size=2) 冻结/恢复几何自动调整(进出瞬态,不暴露)
#   "4 1/0"   (size=2) 进入/退出调整模式(真机实测非必需,不发送)
#   "5"       (size=1) 重置全部校正(与机身「重置校正」同款)
# 真机回环:'1 104'→'-104' 与 '2 13 0'→'-13 0' 的 GET_QC 八 token 四角
# 坐标均精确回到基线,零残差;GET_QC 与写通路同源可读。
QC_ZOOM_STEP = 104
QC_SHIFT_STEP = 13

# 测试图案(GET_ESCVP 透传 MCU 串口命令;无需 debug 模式)。
# "2 AGPATTERN <code>":00=关闭,01=初始图案;
# 完整码表 00-19 中,其余图案视觉语义未确认,只暴露开/关。
TEST_PATTERN_ON = "2 AGPATTERN 01"
TEST_PATTERN_OFF = "2 AGPATTERN 00"

# 色彩增益(CONFIG_GET/SET_COLOR_GAIN):
# GET(params="0")→ "R G B" 三元组(实机 81 90 100);SET params="R G B"
# size=3,0–255 全范围精确读写(0/1/50/150/200/255 变值回环实测),
# 不受投影偏好切换影响。菜单里的 ColorTuner 走 Android Settings.Global,
# 与本命令不同源(勿混淆)。
COLOR_GAIN_MIN = 0
COLOR_GAIN_MAX = 255


# Remote key names -> Android keycodes
KEYCODES: dict[str, int] = {
    "HOME": 3,
    "BACK": 4,
    "MENU": 82,
    "DPAD_UP": 19,
    "DPAD_DOWN": 20,
    "DPAD_LEFT": 21,
    "DPAD_RIGHT": 22,
    "DPAD_CENTER": 23,
    "VOLUME_UP": 24,
    "VOLUME_DOWN": 25,
    "POWER": 26,
    "MEDIA_PLAY": 126,
    "MEDIA_PAUSE": 127,
    "MEDIA_PLAY_PAUSE": 85,
    "MEDIA_NEXT": 87,
    "MEDIA_PREVIOUS": 88,
    "MUTE": 164,
    "SLEEP": 223,
    "WAKEUP": 224,
    "TV_INPUT_COMPONENT_1": 242,
    "TV_INPUT_HDMI_1": 243,
    "TV_INPUT_HDMI_2": 244,
    "TV_INPUT_HDMI_3": 245,
}

# How often (in polls) to refresh slow-changing diagnostic values
DIAGNOSTIC_EVERY_N_POLLS = 12
