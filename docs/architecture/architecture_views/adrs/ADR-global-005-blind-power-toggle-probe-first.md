---
project: "ha-epson-ef-projector"
type: adr
id: ADR-global-005
description: "电源控制没有 set-power 命令，只有盲 toggle 键 KEYCODE_POWER(26)，因此 turn_on/turn_off 实现为『加锁 + 动作前探测 + 盲发 + 动作后确认轮询 + 超期回落刷新』，把幂等性建立在探测而非幂等键上。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
status: accepted
title: "盲 toggle 电源键的先探测后发送策略"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/media_player.py
  - custom_components/epson_ef/api.py
  - custom_components/epson_ef/coordinator.py
---

# ADR-005: 盲 toggle 电源键的先探测后发送

## 状态

accepted

## 1. 背景

**业务背景**：HA 的 `media_player.turn_on` / `turn_off` 语义是**幂等**的——"确保处于开/关状态"，而不是"切换"。用户与自动化都按这个语义使用（例如"每天 8 点确保开机"）。

**技术背景**：固件**没有** `SET_POWER on/off` 命令，只有 `NOTIFY_KEYEVENT params="26"`（KEYCODE_POWER），是**盲 toggle**：发一次翻转一次，不看当前状态。同时电源态探测本身是有损的——`CONFIG_GET_LIGHT_TIME` 在待机时 FAIL 或直接断连（`api.py:248-264` 注释详述），深层待机会挂住 5s。

**核心问题**：如何用"盲 toggle"实现"幂等 turn_on/turn_off"，并避免重复调用把设备翻两次导致 HA 与设备失步？

**约束条件**：

- 探测存在假阴性风险（深层待机下的超时/断连都被判为 off）。
- 探测耗时（最长 15s/次）。
- 设备无推送，无法被动得知状态变化。

## 2. 决策驱动因素

1. **幂等性**（高）：自动化重复触发是常态，翻两次是严重缺陷。
2. **不误发**（高）：设备不可达时绝不能发 toggle（会变成"下次开机时莫名其妙关机"）。
3. **收敛性**（中）：发完要尽快让 HA 状态与设备一致。

## 3. 候选方案

| 方案 | 简述 | 优点 | 缺点 | 风险 |
|-|-|-|-|-|
| **Option A：加锁 + 先探测 + 盲发 + 后确认** | 每次动作都是"读—判—写—验" | 幂等、不误发、可收敛 | 每次操作多 2–N 次 HTTP；确认有软超时 | 低（`risk.md` R-08 指出锁范围仅单实体） |
| Option B：直接盲发 toggle | 简单 | 快 | 破坏幂等；自动化重触发会翻转 | 高 |
| Option C：只发不确认（乐观） | 快速响应 | 少请求 | HA 状态可能与设备分叉，且无法发现失败 | 中 |
| Option D：靠长轮询等待状态变化再发 | 极保守 | 最稳 | 15s 轮询延迟内根本无法判断"刚按过" | 不可用 |

### Option A（选中）

实现分四步（`media_player.py:178-199`）：

1. `async with self._power_lock:` —— 串行化同实体的 turn_on/off（`media_player.py:73`, `:180`, `:192`），注释说明理由："KEYCODE_POWER is a blind toggle, so two concurrent calls would flip twice"。
2. `await self._probe()` —— 动作前取真值；不可达则抛 `HomeAssistantError` 且**不发键**（`media_player.py:151-157`）。
3. 已处于目标态则 `return`（`media_player.py:182-183`, `:194-195`）——**这就是幂等性的实现**。
4. 发键 → `_assume(target)` 乐观改缓存 → `_wait_and_reconcile(target)` 轮询确认（≤10s 预算）；确认失败则 `async_request_refresh()` 回落到协调器真值（`media_player.py:159-176`）。

### Option B（明确否决）

历史缺陷来源。若无先探测，HA 与设备的相对状态无法保证，"确保开机"会变成"今天开、明天关"。

## 4. 决策结果

**选择方案**：**Option A**

**选择理由**：幂等性必须建立在"知道当前状态"之上，而唯一能知道状态的方式是探测；因此"先探测"不是优化而是正确性前提。加锁解决并发重入，后确认解决异步收敛。

### 4.1 实施要点

- 锁必须是**每实体**的（`media_player.py:73` 在 `__init__` 内）——这也意味着 `services.send_key` 与按钮发的 POWER 不经过它（`risk.md` R-08）。
- 乐观更新必须写进 `coordinator.data`（`media_player.py:159-163`），否则下一帧会回弹。
- 确认超时 `_POWER_CONFIRM_TIMEOUT = 10.0`（`media_player.py:27`）是**软预算**：`wait_for_state` 先 probe 再判 deadline（`api.py:284-292`），单次 probe 最长 15s，故实际可到 ~25s。
- 异常语义分三类转换（`media_player.py:139-149`）：不可达 / 未执行 / 未知。

## 备注

- **静音键同属盲 toggle**（`KEYVENT 164`），采用同一思路的轻量版：只在目标值与本地值不同时才发（`media_player.py:212-213`），但**没有**探测（无读通路）也无确认——因此会因物理遥控器而漂移（`media_player.py:208-211` 注释承认）。
- ADR-004 处理"无读通路"的同类问题，但结论不同：静音无法探测，故只能本地记忆；电源可探测，故必须探测。
