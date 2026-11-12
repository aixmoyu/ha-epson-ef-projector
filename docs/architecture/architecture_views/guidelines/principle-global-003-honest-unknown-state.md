---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-global-003
description: "对固件不可读的状态，必须在实体上显式声明『这是假设值』（assumed_state + RestoreEntity + raw_value/stale 属性），绝不用本地记忆冒充设备真值；对可读状态，则必须暴露固件原始值供用户核对。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
principle_category: 架构
principle_scope: global
---

# 不可知状态必须诚实暴露

## 原则详细描述

HB71 固件对相当一批能力**没有读通路**（`CONFIG_GET_AMBIENT_LIGHT` 全子索引 FAIL、`GET_AUTO_CONTRAST` FAIL、测试图案无读、静音只有 toggle 键）。本项目的处理不是"隐藏这些实体"，也不是"假装读到了"，而是三条可核查的机制：

1. **`assumed_state = True`**：让 HA 前端把该状态渲染为"假设值"（`switch.py:166-169`、`switch.py:228-230`、`switch.py:288-290`、`number.py:444-447`、`select.py:203-206`）。
2. **`RestoreEntity` 跨重启恢复**：`async_get_last_state()` 读回上次值，只接受非 `unknown/unavailable` 的状态（`switch.py:158-164`、`number.py:433-442`、`select.py:197-201`）。
3. **暴露固件原始值**：可读实体统一带 `raw_value` 属性（`switch.py:100-102`、`select.py:99-102`、`number.py:135-140`、`number.py:370-377` 三个增益通道都暴露完整三元组）；不可读但可能陈旧的数据带 `stale` 布尔（`sensor.py:70-83`、`sensor.py:112-121`）。

写失败的语义同样诚实：**写成功才翻本地值**，失败抛 `HomeAssistantError` 且不翻转（`switch.py:171-179`、`number.py:291-292`、`select.py:208-219`）。

## 为什么要这样

- HA 的 `assumed_state` 是**面向用户的诚实契约**：UI 会画成不同的图标/提示，用户知道"这不是从设备读来的"。不标则是欺骗——用户会以为物理遥控器改过之后 HA 能自动跟上。
- 现场分析期最难判断的正是"到底有没有读通路"。如果代码里没有显式的 assumed 标记，下一个维护者会误以为该实体有真值来源，可能基于它写自动化。
- `raw_value` 解决了第二类问题：Epson 的选项语义（色温 0/1/2、动态对比度 0/1/2、投影偏好 35/6）不总能确认，暴露原始值让用户**用机身菜单对照**即可自证，无需改代码（`const.py:68-74` 明确写了这个理由）。

## 适用范围

- 全部可写实体（switch / select / number / media_player 静音）
- 全部只读传感器（尤其关机时陈旧的灯时、距离）
- 新增任何"写-only"能力时强制适用

## 规则

- 无读通路的实体：`assumed_state = True` + 实现 `RestoreEntity`（`async_added_to_hass` 里读 last state，过滤 `unknown/unavailable`）。
- 有读通路的实体：状态优先取 `coordinator.data`（真值），本地值只做兜底（`number.py:110-121`、`:189-199` 的 `raw → 回退 _attr_native_value` 模式）。
- 有歧义的枚举值：必须暴露 `raw_value` 属性，禁止只给标签。
- 可能陈旧的数据：必须暴露 `stale`（或等价的 `measured_at`），不得让陈旧值伪装成实时值（`sensor.py:75-83`）。
- 写入失败必须抛错且不改变本地状态；**禁止**"先翻本地值再发命令"。

## 反模式 / 禁止项

- ❌ 用 `RestoreEntity` 恢复值后当作设备真值展示（不标 `assumed_state`）。
- ❌ 在没有读通路的实体上实现 `native_value` 时返回硬编码默认值（如固定返回 0）。
- ❌ 写入前先改本地状态、失败后再"回滚"（本地会短暂对外呈现未发生的状态）。
- ❌ 只给中文标签、隐藏原始枚举值，导致语义争议时无法自证。

## 修改检查清单

- [ ] 新实体是否有真值来源？没有 → `assumed_state = True` + `RestoreEntity` 是否都到位？
- [ ] `async_added_to_hass` 是否过滤了 `unknown` / `unavailable`？
- [ ] 枚举类实体是否暴露 `raw_value`？
- [ ] 依赖轮询的只读值是否有 `stale` / `measured_at` 标记？
- [ ] 写失败路径是否 `raise` 且未修改本地状态？
- [ ] 已知例外：`media_player` 静音有 `RestoreEntity`（`media_player.py:75-81`）但**未**设 `assumed_state`（`media_player.py:50` 只给初值 `False`）——这是与原则不一致的现存偏差，改动该文件时应修正或显式记录理由。
