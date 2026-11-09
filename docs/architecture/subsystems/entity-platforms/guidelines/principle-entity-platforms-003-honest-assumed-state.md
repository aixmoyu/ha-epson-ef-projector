---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-entity-platforms-003
description: 对固件不可读的状态，实体必须同时具备三件套——RestoreEntity（跨重启保持最后已知值）、assumed_state=True（显式声明这是假设值）、raw_value/时间戳属性（可核对）——并且写成功才翻转本地值。当前 7 个实体完整遵守，而媒体播放器的静音只做了 RestoreEntity，缺 assumed_state，是唯一不合规点。
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
principle_category: 架构
principle_scope: module
---

# 不可读状态必须"三件套"声明，不得用本地记忆冒充真值

## 原则详细描述

HB71 固件对若干能力**没有读通路**，实测证据集中在 `const.py` 的注释里：

| 能力 | 读通路状态 | 证据 |
|-|-|-|
| 氛围灯开关（kind 3/4/5）| `GET_AMBIENT_LIGHT` 各子索引全部 FAIL（含新增的 31/32）| `const.py:150-151` |
| 氛围灯亮度（kind 9）| 同上 | `const.py:150-151` |
| 氛围灯光效模式（kind 1）| 同上 | `select.py:172-174` |
| 自动对比度 | `GET_AUTO_CONTRAST` 带参仍 FAIL | `api.py:626-630` |
| 测试图案 | 无读命令 | `switch.py:288-290` |
| 静音 | 只有 toggle 键（164），无状态查询 | `media_player.py:48-49` |

本模块的合规处理是**三件套**，缺一不可：

1. **`RestoreEntity` + `async_added_to_hass` 恢复**：把上一次 HA state 读回本地字段，使重启后不丢"最后已知值"。实现见 `switch.py:158-164`（氛围灯）、`:219-225`（自动对比度）、`:279-285`（测试图案）、`select.py:197-201`（氛围灯模式）、`number.py:433-442`（氛围灯亮度）。恢复时都做了守卫：`last_state is not None and last_state.state not in ("unknown", "unavailable")`。
2. **`assumed_state` 返回 `True`**：让 HA 前端给实体打上"假设状态"徽标，并让自动化开发者知道该值不可信。见 `switch.py:166-169`、`:227-230`、`:287-290`、`select.py:203-206`、`number.py:444-447`——注释都是同一句"状态不可读，明确标记为假设状态"。
3. **写成功才翻转 + 失败不翻转**：`switch.py:171-179` 的结构是 `try: await api... except EpsonApiError: raise HomeAssistantError(...)` 之后才 `self._attr_is_on = on`。这一点让"本地值"至少等价于"最后一次设备接受的写入"。

**唯一不合规点：媒体播放器的静音。** 它做了 ①（`media_player.py:75-81` 从 `last_state.attributes["is_volume_muted"]` 恢复）、做了 ③（`:212-216` 目标态比对 → 发键 → 翻转），但**没有做 ②**：类里既没有 `assumed_state` 属性，也没有说明为什么豁免。对比同样是写-only 的氛围灯开关，静音的漂移风险实际上更高——氛围灯只能由本集成或官方小程序控制，而静音键是实体遥控器上最常被按的键之一，用户随时可能绕过集成改变它（`media_player.py:209-211` 的注释自己也承认"It can still drift if the physical remote is used"）。用户界面上，静音图标与氛围灯开关长得一样"确定"，但可信度不同，这个差异目前不可见。

## 为什么要这样

1. **诚实性优先于完整性**（与全局 `principle-global-003` 同源，本模块是其落地点）。这 8 个写-only 实体里如果有 1 个偷偷冒充真值，用户建立的信任会整体崩塌——尤其静音这种"看起来确定"的布尔量。
2. **`assumed_state` 有实际功能**：HA 前端对 assumed_state 实体的服务调用会先发"假设状态"提示；对自动化作者，`assumed_state` 属性可通过 `state_attr` 检查。
3. **失败不翻转是缓存正确性的基础**：若写失败也翻转，`coordinator.data` 与本地值都会记录一个从未发生过的状态，且无任何后续回读能纠正它（因为没有读通路）。

## 适用范围

- 全部写-only/无读通路的实体：`switch.{ambient_light,welcome_light,touch_sensor_light,test_pattern,auto_contrast}`、`select.ambient_mode`、`number.ambient_brightness`（7 个已合规）、`media_player`（静音，**部分合规**）。
- **不适用**：有读通路的设置类实体（适用 principle-002）。

## 规则

- 无读通路 → 必须同时满足：继承 `RestoreEntity`；`assumed_state` 返回 `True`；恢复时守卫 `unknown`/`unavailable`。
- 写成功才改本地值；异常路径禁止修改任何状态。
- 有读通路与无读通路的判定必须以**实测**为据（注释写明"GET_X 本机全部 FAIL"），不得凭命令名是否存在推断。
- 若某个无读通路实体刻意不声明 `assumed_state`，必须在类注释中写明豁免理由。

## 反模式 / 禁止项

- ✗ 只加 `RestoreEntity` 就当作"状态已处理"（静音当前的做法）。
- ✗ 在 `except` 分支里翻转本地值以求"界面一致"。
- ✗ 用一个看似精确的默认值（如 `False`）冒充已知状态而不声明 assumed_state（`media_player.py:50` 的 `_attr_is_volume_muted = False`）。
- ✗ 因为"命令存在"就假定"读通路存在"——`CONFIG_GET_AUTO_CONTRAST` 存在但 FAIL 是典型案例。

## 修改检查清单

- [ ] 该能力是否真的能读回来？有实测记录（注释中的"FAIL/回环"）吗？
- [ ] 无读通路：`RestoreEntity` + `assumed_state=True` + 恢复守卫，三者是否齐备？
- [ ] 有读通路：是否按 principle-002 做了双写回填 + 刷新？
- [ ] 类默认值是否会被误读为"已知状态"？是否需要在属性中加时间戳？
- [ ] 静音这类"实体遥控器可改"的能力，是否至少暴露了 `last_written_at` 之类的可观测线索？
