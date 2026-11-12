---
project: "ha-epson-ef-projector"
type: adr
id: ADR-entity-platforms-001
description: "实体层采用『乐观写 + 回填 coordinator.data + 可选刷新』而非『写完等回读再上屏』或『纯事件驱动』。因为它对全部 55 个可写实体统一适用，把 UI 响应压到一帧内，并把与固件真值的收敛责任交给既有的慢诊断轮——代价是无读通路的 7 个实体退化为本地记忆，且缓存成为可写共享状态。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
status: accepted
title: "以乐观写 + 缓存回填作为实体写路径的统一模式"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/number.py
  - custom_components/epson_ef/switch.py
  - custom_components/epson_ef/select.py
  - custom_components/epson_ef/media_player.py
  - custom_components/epson_ef/coordinator.py
---

# ADR-001: 以乐观写 + 缓存回填作为实体写路径的统一模式

## 状态

accepted（自 v3 起贯穿 7 个平台；无写回退记录）

## 1. 背景

**业务背景**：用户调节光源亮度、切换投影方式、开关氛围灯时，期待滑条/开关**立刻**响应。而固件的写命令是「写后不回执」的——`CONFIG_SET_*` 只返回 `resultCode=0`，不返回新值；要读回真值必须再发一条 `CONFIG_GET_*`。

**技术背景**：状态由单一 `DataUpdateCoordinator` 定时轮询（`coordinator.py:37-154`），快轮只探电源（2 次 HTTP），慢轮才回读全部设置（33–35 次 HTTP），慢轮每隔 12 个轮询周期执行一次（`const.py:222`、`coordinator.py:59`）。默认轮询间隔 15s（`const.py:11`），即**慢轮间隔最坏约 180 秒**。

**核心问题**：在固件不回执、慢轮回读间隔可达 3 分钟的前提下，用户点一下开关后，界面应该如何以及在多久内反映新状态？

**约束条件**：

- 不能为每次写入引入一次同步 `GET`（会把单次写从 1 个请求放大到 2 个，且待机时 GET 会 FAIL，反而让写入看起来失败）。
- 不能修改轮询架构（慢轮的存在是为了控制对单线程固件 NanoHTTPD 的压力）。
- 有 7 类能力**根本没有读命令**（`const.py:150-151`、`api.py:626-630`），任何"等回读"策略对它们无解。

## 2. 决策驱动因素（Decision Drivers）

1. **交互响应性**（重要性：高）：写操作的视觉反馈必须在一帧内完成，否则用户会重复点击（对盲 toggle 类命令尤其危险）。
2. **固件压力**（重要性：高）：单线程 HTTP 服务，额外请求会直接拉长其它请求的排队时间。
3. **一致性可收敛**（重要性：中）：乐观值必须能被真值纠正，否则界面永久撒谎。
4. **实现一致性**（重要性：中）：55 个可写实体若各写一套，维护成本不可控。
5. **无读通路能力的可表达性**（重要性：中）：策略必须能覆盖"永远读不回来"的 7 个实体。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|Option A|**乐观写 + 回填 `coordinator.data` + 可选 `async_request_refresh()`**|UI 一帧内响应；不额外发 GET；统一模式覆盖全部实体；真值由慢轮自然收敛|缓存成为可写共享状态，无锁无版本；无读通路实体永久漂移|并发写可能互相覆盖（R-EP-01/R-EP-04）|
|Option B|写后同步 `GET` 回读确认，再用真值上屏|界面永不撒谎；无需本地记忆|每次写变成 2 个请求；待机时 GET FAIL 会让成功写入被误报为失败；慢（固件往返 + 0.2s 节流）|用户重复点击；对盲 toggle 造成二次触发|
|Option C|写后仅改 `self._attr_*`，不碰缓存|实现最简单，零耦合|下一帧被 `coordinator.data` 的旧值覆盖 → **滑条弹回**（`number.py:110-121` 的读优先级决定了这点）|功能不可用（实测会弹回）|
|Option D|写后要求用户等待，界面显示"写入中"直到慢轮回读|状态最诚实|最坏 3 分钟"写入中"，交互不可接受；且对无读通路能力永远无法结束|用户强行刷新/重启集成|

### Option A：乐观写 + 缓存回填

写命令成功后，实体同时更新 `coordinator.data` 中的对应键与自身 `self._attr_*`，调用 `async_write_ha_state()` 立即上屏，并（对有读通路的能力）`await coordinator.async_request_refresh()` 触发一次权威回读。回填位置由"读优先级"决定：因为 `native_value` 优先读协调器（`number.py:110-121`），所以缓存必须被回填，否则本地值无效。

### Option B：写后同步回读

`await api.set_x(v)` 后立即 `await api.get_x()`，用返回的真值上屏。看似最严谨，但与固件的电源态强耦合：待机时 `CONFIG_GET_*` 一律 FAIL（`coordinator.py:61` 的 `if state == "on":` 就是为此），而 `CONFIG_SET_*` 在待机时同样 FAIL，两者的失败形态无法区分，会产生"写成功了却被报错"的假象。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：对照驱动因素逐条 ——

1. 响应性：一帧内完成（`async_write_ha_state()`），满足 D1。
2. 固件压力：写后不强制 GET，刷新请求交给协调器的既有节奏，满足 D2。
3. 可收敛：慢轮 `merged.update(settings)`（`coordinator.py:116-119`）会用真值覆盖乐观值，满足 D3。
4. 一致性：同一个模式复制到 55 个实体（`number.py:129-133`、`select.py:110-113`、`switch.py:109-111`、`select.py:160-164`、`number.py:216-220/297-301/392-396`），满足 D4。
5. 无读通路：Option A 的"本地记忆"分支天然覆盖（`switch.py:120-185` 等），满足 D5。Option B/C/D 在这一条上全部失效或代价过高。

### 4.1 实施要点

- **回填位置必须与读属性一致**：`settings` 类写 `data["settings"][suffix]`，`diag` 类写 `data["diag"][key]`，电源写 `data["state"]`，启动模式写 `data["standby_mode"]`。
- **双写而非单写**：缓存与 `self._attr_*` 都要改（`number.py:129-131`）。只改一处会被另一处覆盖。
- **失败零副作用**：先 `await` 写命令，成功后才回填；异常路径直接 `raise HomeAssistantError(...) from err`。
- **有读通路才刷新**：6 个 `Ef50ConfigSwitch`（`switch.py:104-111`）当前遗漏了 `async_request_refresh()`，属已知偏离（见 risk R-EP-14）。
- **无读通路走另一分支**：`RestoreEntity` + `assumed_state`（ADR-002 讨论），不回填缓存。
- **回填的是设备接受值**：对焦回写实测终值而非目标值（`number.py:297-299`），因为闭环有 ±容差。

## 备注

- 该决策的代价是可量化的：**`coordinator.data` 从"只读快照"变成"可写缓存"**，8 个实体写它、1 个协调器 merge 它，无锁无版本号（risk R-EP-04）。
- `media_player` 的电源路径不走本模式：它是"乐观写 `state` + `wait_for_state` 确认 + 未确认则刷新"的变体（`media_player.py:159-176`），因为它有一个其它实体没有的能力——**可以主动探测真值**（`probe_state`）。
- 若未来固件提供写回执（返回新值），本 ADR 应被取代：届时 Option B 的缺点（额外请求 + 失败歧义）会消失。
