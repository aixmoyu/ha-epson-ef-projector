---
project: "ha-epson-ef-projector"
type: adr
id: ADR-entity-platforms-002
description: "无读通路的 7 个实体选择『RestoreEntity 本地记忆 + assumed_state 显式声明』而不是隐藏实体、也不是伪造真值。理由是这些能力客观上只能写不能读（GET 全 FAIL 有实测记录），隐藏会剥夺可用的控制能力，伪造则破坏信任。代价是状态随实体遥控器使用而永久漂移，且用户只能靠 assumed_state 徽标识别。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "写-only 能力以本地记忆 + assumed_state 暴露，不隐藏也不伪造"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/switch.py
  - custom_components/epson_ef/select.py
  - custom_components/epson_ef/number.py
  - custom_components/epson_ef/media_player.py
  - custom_components/epson_ef/const.py
---

# ADR-002: 写-only 能力以本地记忆 + assumed_state 暴露

## 状态

accepted（7 个实体完整适用；`media_player` 静音部分适用，见备注）

## 1. 背景

**业务背景**：氛围灯（开关/亮度/模式）、测试图案、自动对比度、静音——这些是用户真实想控制的能力。氛围灯的写命令已在真机验证成功（`const.py:144-149` 记录 `"1 <mode> 48"`、`"3 <01> 48"`、`"9 <0-255> 48"` 三条均 SUCCESS）。

**技术背景**：这些能力的**读通路不存在**，且不是"暂时没找到"而是"反复探测确认失败"：

| 能力 | 探测结论 | 证据 |
|-|-|-|
|氛围灯全部子索引|`GET_AMBIENT_LIGHT "3"/"4"/"5"/"31"/"32"` 在本机（HB71）全部 FAIL，机身 UI 读侧也只回静态默认值|`const.py:150-151`|
|自动对比度|`GET_AUTO_CONTRAST` 带参实测 FAIL|`api.py:626-630`|
|测试图案|无对应读命令，只有 `CONFIG_GET_ESCVP` 透传写|`switch.py:252-258`|
|静音|固件只暴露 toggle 键 164，无状态查询|`media_player.py:48-49`、`:208-211`|

**核心问题**：对一个"能写但永远读不回来"的设备能力，Home Assistant 实体应该如何表达它的状态？

**约束条件**：

- HA 实体必须有一个 state；`None`/`unknown` 会让 UI 无法操作（滑条没有位置、开关没有方向）。
- 用户在重启 HA 后不应看到状态归零。
- 不得让用户误以为这是设备真值。

## 2. 决策驱动因素（Decision Drivers）

1. **能力可用性**（重要性：高）：写通路已验证可用，隐藏实体等于丢弃已确认的能力。
2. **诚实性**（重要性：高）：不能用一个本地猜测冒充设备真值。
3. **跨重启连续性**（重要性：中）：重启后状态归零会让用户以为设置被重置。
4. **交互可操作性**（重要性：中）：实体必须有可渲染的 state。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|Option A|**`RestoreEntity` 保存最后已知值 + `assumed_state=True` 显式声明 + 写成功才翻转**|能力可用；重启不归零；用户可见"假设"徽标；写失败不污染状态|状态仍会漂移（遥控器/小程序改动）；需要用户理解 assumed_state 含义|用户把假设值当真值（缓解：徽标 + raw_value）|
|Option B|不暴露实体，只提供 service 调用|绝对诚实，零误导|用户无法在 UI 上看到/操作；自动化需写脚本；能力等于废弃|违背"已确认能力应可用"的项目取向|
|Option C|实体 state 固定为 `unknown`，只保留写动作|诚实且可操作（按钮式）|滑条/开关没有初值，UI 体验差；每次操作后仍显示 unknown，用户无法确认"我上次设的是多少"|可用性受损|
|Option D|用 `_attr_* = False/0` 之类默认值充当已知状态，不声明 assumed_state|UI 最"干净"|把猜测伪装成事实——静音目前正是这种形态（`media_player.py:50`）|信任崩塌；用户无法区分"设备确实未静音"与"我们不知道"|

### Option A：RestoreEntity + assumed_state

三件套落地：
1. 继承 `RestoreEntity`，`async_added_to_hass` 里从 `last_state` 恢复，且守卫 `unknown`/`unavailable`（`switch.py:158-164`、`select.py:197-201`、`number.py:433-442`）；
2. `assumed_state` property 返回 `True`（`switch.py:166-169`、`:227-230`、`:287-290`、`select.py:203-206`、`number.py:444-447`）；
3. 写成功才 `self._attr_* = value`，失败只抛错（`switch.py:171-179`）。

### Option D：静默默认值（当前静音的做法）

`media_player.py:50` 直接 `_attr_is_volume_muted: bool | None = False`，注释只说"assume unmuted until we toggle it"。重启后靠 `:75-81` 恢复。整个类**没有 `assumed_state`**，因此前端不会显示任何"假设"提示。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

1. 对照 D1：Option A 保留全部 7 个能力的可操作性（氛围灯三件套、测试图案、自动对比度、静音）；Option B/C 都牺牲了可用性。
2. 对照 D2：只有 Option A 与 Option C 诚实。Option C 的诚实是以可用性为代价的过度保守——对一个已经真机验证写通路的能力，"能写但界面永远 unknown"会让用户反复怀疑是否生效。
3. 对照 D3：只有 Option A 提供跨重启连续性（`RestoreEntity`）。
4. 对照 D4：`assumed_state=True` 时 HA 前端仍正常渲染控件，只是加徽标，交互不受限。

### 4.1 实施要点

- **必须三件套齐备**：`RestoreEntity` + `assumed_state` + 写成功才翻转。缺 `assumed_state` 即退化为 Option D（静音现状）。
- **恢复守卫**：`last_state.state not in ("unknown", "unavailable")` 才恢复（`switch.py:161-164`），否则沿用 `None`。
- **失败零副作用**：`except EpsonApiError: raise HomeAssistantError(...)` 之后才翻转（`switch.py:174-179`）。
- **错误文案带前置条件**：`"氛围灯写入失败(投影仪需处于唤醒状态)"`（`switch.py:175-177`）——因为这些命令在待机时确实会 FAIL。
- **不回填 `coordinator.data`**：无读通路意味着缓存里没有可覆盖的权威值，回填反而会污染共享字典。
- **属性补充**：有读通路的写-only 变体（如 `number.ambient_brightness`）仍需 `raw_value`；纯写-only 项则无需。

## 备注

- **静音是唯一未完全遵守本 ADR 的实体**。它做了 ①③，缺 ②。风险实质更高：氛围灯只能由本集成或官方小程序控制，而静音键是实体遥控器上最常按的键，漂移概率更大（`media_player.py:209-211` 的注释自己承认 "It can still drift if the physical remote is used"）。建议补齐 `assumed_state`，使其与氛围灯三件套行为一致。
- Option B 在这个项目里被明确否定过一次的同类证据：`engine_refresh` 按钮因"单独发送无任何动作"而被删除（`button.py:41-43`）。即项目的取向是"只暴露真正有效的能力"，而写-only 能力**是有效的**，故应暴露——这与 Option B 的隐藏逻辑相区别。
- 若未来固件开放 `GET_AMBIENT_LIGHT` 的任一子索引（`const.py:150` 记录了 5 个已试子索引），这 7 个实体中至少 3 个应迁移到 ADR-001 的乐观写+回填模式，并移除 `assumed_state`。
