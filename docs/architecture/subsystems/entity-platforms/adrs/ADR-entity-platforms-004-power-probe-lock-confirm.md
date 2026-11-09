---
project: "ha-epson-ef-projector"
type: adr
id: ADR-entity-platforms-004
description: "电源动作采用『动作前探测 → 盲发一次 toggle → 乐观置态 → 最多 10s 确认 → 未确认回落刷新』的三段式，并用每实例 asyncio.Lock 串行化。理由是在只有 toggle 键的固件上，幂等必须实现在动作前；而锁的作用域选择每实例而非每设备，是一次有意识的取舍（避免跨 entry 死锁，代价是 services 与按钮可绕过）。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "电源动作探测-发键-确认三段式 + 每实例锁"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/media_player.py
  - custom_components/epson_ef/api.py
  - custom_components/epson_ef/services.py
---

# ADR-004: 电源动作探测-发键-确认三段式 + 每实例锁

## 状态

accepted（锁作用域的已知缺口见备注）

## 1. 背景

**业务背景**：投影仪只有一个电源键（`KEYCODE_POWER` = 26，`const.py:206`），语义是 toggle：发一次切换一次。而 HA 的 `media_player.turn_on` / `turn_off` 是**有向**语义。两者之间存在必然的语义鸿沟。

**技术背景**：

- 电源真值**可以**主动探测：`probe_state()` 先用 `CONFIG_GET_PRODUCT_NAME` 做 liveness（所有电源态都回），再用 `CONFIG_GET_LIGHT_TIME` 判唤醒（唤醒时成功、待机时 FAIL/超时/断连，`api.py:221-276`）。FAIL 还会在 1.5s 后重试一次以避免电源过渡期的瞬时误判（`api.py:252-264`）。
- 一次探测 = 2 次 HTTP，默认超时 10s + 5s。
- 状态由轮询维护（默认 15s），因此"发键后等下一轮"最长有 15s 的盲区；而在过渡期（开机预热/关机冷却）轮询可能读到中间态。

**核心问题**：在有向 API 与 toggle 固件之间，如何保证"用户点一次 = 设备变一次"，并且不让两个并发动作把设备翻两次？

**约束条件**：

- 没有方向性命令可用（不能发"开机"或"关机"）。
- 探测失败必须**不发键**（否则断电时点 turn_off 会在恢复供电后误开机）。
- 过渡期内 `probe_state` 可能给不出稳定结果，需要确认机制。

## 2. 决策驱动因素（Decision Drivers）

1. **幂等正确性**（重要性：高）：重复调用 turn_on 不应导致设备被翻两次。
2. **不能误发**（重要性：高）：探测失败时必须零副作用。
3. **并发安全**（重要性：高）：两个协程同时 turn_on/off 不能各发一次键。
4. **确认及时性**（重要性：中）：用户需要知道动作是否生效，不能只等到下一轮轮询。
5. **不阻塞事件循环**（重要性：中）：探测 + 确认合计可能耗时 10s+，需避免无限等待。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|Option A|**探测 → 盲发 26 → 乐观置态 → `wait_for_state(10s)` 确认 → 未确认则 refresh，全程持每实例锁**|幂等正确；误发风险为零；并发被串行化；有确认与回落|单次动作最长 ~25s 占用锁；锁不跨 services/按钮|锁作用域缺口（R-EP-05）|
|Option B|直接发 26，靠轮询收敛|实现最简单、最快|**完全不幂等**：turn_on 连点两次 = 开机又关机|用户误操作直接改变设备状态|
|Option C|探测 → 发 26 → 不做确认，直接等下一轮轮询|比 A 简单；有幂等|写后 15s 内界面显示的是乐观值，用户无法区分"生效"与"没生效"；过渡期轮询可能读到中间态并再次误判|用户体验与诊断能力下降|
|Option D|Option A + 把锁上提到 `hass.data` 的 entry 级共享对象|锁覆盖 services 与所有实体|需改 `__init__.py` 的装配契约，跨模块耦合；且多 entry 场景下仍不能覆盖另一台设备|实现成本与收益不匹配（当前仅 1 台设备的实际部署）|

### Option A：三段式 + 每实例锁

```python
async with self._power_lock:              # 串行化
    state = await self._probe()           # ① 探测（失败则抛错，不发键）
    if state == "on": return              # ② 幂等：已是目标态
    await self._key(26)                   # ③ 盲发一次 toggle
    self._assume("on")                    # ④ 乐观置态，UI 立刻响应
    await self._wait_and_reconcile("on")  # ⑤ 最多 10s 确认，未确认则 refresh
```

### Option D：把锁上提到 entry 级

在 `hass.data[DOMAIN][entry_id]` 里放一把 `power_lock`，媒体播放器、`services.send_key`、以及任何发 26 的路径共用。技术上最优，但需要修改 `__init__.py` 的装配契约（当前 `hass.data` 只放 `api`/`coordinator`/`product`/`base_id`/`serial`，`__init__.py:195-201`），属于跨模块变更。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

1. 对照 D1：探测在最前，已处于目标态则**零按键返回**（`media_player.py:181-183`, `:193-195`）——这是唯一能实现幂等的位置，因为命令本身没有方向。
2. 对照 D2：`_probe()` 捕获 `EpsonApiError` 后抛 `HomeAssistantError("投影仪不可达: …")`（`:151-157`），**不会走到发键语句**。
3. 对照 D3：`async with self._power_lock` 包住"探测+发键+确认"整段（`:180-188`, `:192-199`）。仅锁发键是不够的——两个协程可能同时探测到 off，然后各发一次。
4. 对照 D4：`wait_for_state(target, timeout_s=10.0)`（`:167-169`）每秒探测一次；超时则 warning + `async_request_refresh()`（`:170-176`），把权威判定交回轮询。
5. 对照 D5：确认阶段有 10s 硬上限，不会无限等待。

不选 D 的理由：锁作用域缺口（services/按钮绕过）是**已知且被记录**的（全局 risk R-08 / 本模块 R-EP-05），但在当前单设备部署下，绕过路径都是"用户主动调用底层服务"的高级场景；把锁上提到 entry 级需要改动组成根契约，收益/成本不匹配。该取舍被显式记录而非忽略。

### 4.1 实施要点

- **顺序不可调换**：探测必须在发键之前；乐观置态必须在发键之后（否则失败时会留下假状态）。
- **`_assume` 只改 `coordinator.data["state"]`**（`:159-163`），让所有读 `state` 的消费者（媒体播放器 state、`sensor` 的 `stale` 标记）同步看到新值。
- **确认失败不等于动作失败**：10s 未确认只记 warning 并刷新，不抛错——因为固件可能只是慢，报错会让用户重复点击（而重复点击会被幂等探测挡住，但仍多一次探测开销）。
- **锁必须是 `asyncio.Lock()` 在 `__init__` 内创建**（`:73`），不能是类属性（会跨 entry 共享）。

## 备注

- **锁的真实边界**（必须明说）：它是**每实体实例**的。绕过它的路径有两条，均无锁、可并发发出同一个盲 toggle（keycode 26）：
  (a) `services.send_key` 传 `key="POWER"`（`const.py:206` → `services.py:48-54`）；
  (b) `services.raw_request` 直接发 `NOTIFY_KEYEVENT` params="26"（`services.py:64`）。
  注意 `button.py` 的按键表**不含** POWER 键（仅 home/back/menu/input_source 四个 `NOTIFY_KEYEVENT`，`button.py:18-22`），因此按钮平台本身不构成该竞态源。
- **静音没有对应的确认机制**：它同样只有 toggle（164），但既不能探测（无读命令）也不能确认，只能靠本地记忆去重（`media_player.py:207-216`）。这是本 ADR 模式在无读通路能力上的降级形态，适用 ADR-002。
- 若未来固件提供独立的开/关机命令，本 ADR 应被简化：幂等可由命令语义直接保证，锁与确认均可退化为可选优化。
