---
project: "ha-epson-ef-projector"
type: adr
id: ADR-global-004
description: "对固件没有读通路的能力（氛围灯、自动对比度、测试图案、静音）采用『乐观写入 + RestoreEntity 本地记忆 + assumed_state=True』三件套，而不是隐藏实体或伪造读值。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
status: accepted
title: "无读通路能力采用乐观状态 + assumed_state 显式标注"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/switch.py
  - custom_components/epson_ef/select.py
  - custom_components/epson_ef/number.py
  - custom_components/epson_ef/media_player.py
---

# ADR-004: 无读通路能力的诚实建模

## 状态

accepted

## 1. 背景

**业务背景**：HB71 固件对一批能力只有写通路没有读通路——`CONFIG_GET_AMBIENT_LIGHT` 的全部子索引（3/4/5/31/32）返回 FAIL，`GET_AUTO_CONTRAST` 带参亦 FAIL，测试图案（`GET_ESCVP AGPATTERN`）无状态查询，静音只有 toggle 键 `KEYEVENT 164` 而无状态查询。

**技术背景**：这些写命令本身真机验证为 SUCCESS（`api.py:387-430`、`:623-632`），能力是真实存在的；缺的只是"读回当前值"。

**核心问题**：对一个"能写不能读"的能力，HA 实体应该怎么建模状态？

**约束条件**：

- 不能假装读到真值（会让用户误以为 HA 能跟踪物理遥控器的改动）。
- 不能因为"读不到"就把功能砍掉（这些是用户实际要的能力）。
- 必须跨 HA 重启保持可观（否则每次重启状态归零）。

## 2. 决策驱动因素

1. **诚实性**（高）：HA 有专门的 `assumed_state` 机制来表达"这是假设值"，不用是失职。
2. **功能可用性**（高）：用户能发命令，比能显示状态更重要。
3. **可恢复性**（中）：HA 重启后不应丢失最后已知值。

## 3. 候选方案

| 方案 | 简述 | 优点 | 缺点 | 风险 |
|-|-|-|-|-|
| **Option A：乐观写 + RestoreEntity + `assumed_state=True`** | 写成功才翻本地值，跨重启恢复，UI 标注为假设 | 诚实、功能完整、可恢复 | 与真实设备状态可能漂移（遥控器改动） | 低 |
| Option B：隐藏这些实体 | 不暴露 | 不产生错误信息 | 用户失去功能；不解决根本问题 | 功能缺失 |
| Option C：伪造读值（如写死默认值或固定返回上次写的值但标 `assumed_state=False`） | UI 看起来完整 | 简单 | 欺骗用户；自动化会基于假数据决策 | 高 |
| Option D：写后立刻回读（`GET` 尝试） | 若固件以后支持则自动升级 | 面向未来 | 当前每次都 FAIL，等于多发一次无效请求并延长写入耗时 | 低（可作未来增强） |

### Option A（选中）

三件套的具体落点：

- `assumed_state = True`：`switch.py:166-169`（氛围灯）、`switch.py:228-230`（自动对比度）、`switch.py:288-290`（测试图案）、`number.py:444-447`（氛围灯亮度）、`select.py:203-206`（氛围灯模式）。
- `RestoreEntity`：`async_added_to_hass()` 里读 `async_get_last_state()`，过滤 `unknown`/`unavailable`（`switch.py:158-164`、`number.py:433-442`、`select.py:197-201`）。
- **写成功才翻值**：失败抛 `HomeAssistantError` 且不修改本地状态（`switch.py:171-179`、`number.py:449-458`、`select.py:208-219`）。

### Option C（明确否决）

这是最容易滑入的反模式：为了让 UI"看起来正常"而隐藏 `assumed_state`，或用本地变量冒充真值。

## 4. 决策结果

**选择方案**：**Option A**

**选择理由**：它是唯一同时满足诚实性与功能完整性的方案。B 牺牲功能，C 产生虚假数据，D 在当前固件上纯属浪费请求（但作为未来增强保留可能性：若某机型出现读通路，可加"写后探读、成功则退出 assumed 模式"）。

### 4.1 实施要点

- 每个无读通路实体必须同时具备：`assumed_state`、`RestoreEntity`、写失败不翻转。
- 翻译文案与文档要说明"状态为假设值"（`README.md` 多处标注"乐观状态"）。
- 与 `sensor.py` 的 `stale` 机制区分：`stale` 用于"曾读到、现在陈旧"，`assumed_state` 用于"从来没有读通路"。

## 备注

- **现存偏差**：`media_player` 的静音状态用 `RestoreEntity`（`media_player.py:75-81`）但**未**设 `assumed_state`（`media_player.py:50` 用 `_attr_is_volume_muted = False` 做初值）。`media_player.py:208-211` 的注释已承认"可能因物理遥控器而漂移"，但未在实体层面标注。改动该文件时应修正。
- **未做写后回读**：`set_ambient_light` 等写后不尝试 `GET`（`api.py:387-430`），因为本机型固定 FAIL；若未来机型支持，可作为增强点。
