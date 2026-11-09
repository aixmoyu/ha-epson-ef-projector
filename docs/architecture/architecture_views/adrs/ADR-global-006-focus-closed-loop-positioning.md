---
project: "ha-epson-ef-projector"
type: adr
id: ADR-global-006
description: "对焦定位没有固件的 set-absolute 命令，且 INC/DEC 步长非线性（换向后首步 ±10、同向后续 ±1），因此放弃『按步数换算』改为『MAX/MIN 连续移动 + 按剩余距离定时 + OFF + 回读 POS』的闭环，容差 3、最多 14 次。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "对焦绝对定位采用连续移动闭环而非步数换算"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/api.py
  - custom_components/epson_ef/const.py
  - custom_components/epson_ef/number.py
  - tests/test_regressions.py
---

# ADR-006: 对焦绝对定位的闭环方案

## 状态

accepted（2026-11-07 真机复测后替换原"按 ±10 步数换算"实现）

## 1. 背景

**业务背景**：HA 用户希望像用滑条一样设定"对焦位置 = 200"（0–264），而不是反复按方向键。

**技术背景**：固件提供 `CONFIG_GET_FOCUS "POS"/"RANGE"`（读位置/行程）与 `CONFIG_SET_FOCUS "INC"/"DEC"/"MIN"/"MAX"/"OFF"`（单步/连续移动/停止），**没有**"设置到 X"的命令。行程实测 `0 264`（`const.py:108` 注释）。

**核心问题**：在没有绝对定位命令的前提下，如何把对焦马达准确送到目标位置？

**约束条件**：

- 马达是物理器件，越界/长时间空转有磨损风险。
- 步长语义曾被认为固定 ±10（`number.py:229` 的旧 docstring 仍保留该描述），后被真机推翻。
- 必须在 HA 关停/任务取消时也能停下马达。

## 2. 决策驱动因素

1. **定位精度**（高）：用户设定 200 就该到 200。
2. **马达安全**（高）：任何路径都必须能停下来。
3. **可预测耗时**（中）：不能因为精度而让一次写入耗时过久。

## 3. 候选方案

| 方案 | 简述 | 优点 | 缺点 | 风险 |
|-|-|-|-|-|
| Option A：按步数换算（每步固定 ±10） | 算 `(target-pos)/10` 步 `INC/DEC` | 简单、无需定时 | **假设错误**：实机换向后首步 ±10、同向后续仅 ±1 → 目标 150 从 89 出发只到 128 就放弃 | 功能不可用（已发生） |
| **Option B：MAX/MIN 连续移动 + 定时 + OFF + 回读闭环** | 开环脉冲 + 闭环校验 | 精度高（实测误差 ≤3）、可预测 | 需要速度常量（65 单位/s）；脉冲时长需 clamp | 中（速度随负载/机型变） |
| Option C：纯 `INC/DEC` 微调到达 | 逐格移动 | 精度最高 | 远距离目标需数十次请求，耗时不可接受 | 低但慢 |
| Option D：用自动对焦（`NOTIFY_AUTO_FOCUS`）替代 | 让固件自己找焦点 | 无需定位 | 目标是"用户指定位置"，不是"清晰"，语义不同 | 不适用 |

### Option A（被推翻）

历史实现。真机复测（2026-11-07）证明单步位移非线性：

> `INC 124→134`(+10)、`134→135`(+1)、`135→136`(+1)…；`DEC 139→129`(−10)、`129→128`(−1)…
> 只有**方向反转**才会让下一步变粗，与空闲时长无关（静置 15s 后仍 +1）。
> — `const.py:108-118`

### Option B（选中）

`EpsonApi.goto_focus`（`api.py:544-584`）：

1. 回读 `POS`；若 `|target - pos| <= FOCUS_TOLERANCE(3)` 直接返回。
2. 否则 `duration = |remaining| / FOCUS_TRAVEL_UNITS_PER_SEC(65.0)`，clamp 到 `[FOCUS_MIN_PULSE_S(0.10), FOCUS_MAX_PULSE_S(2.0)]`。
3. `focus_move_start(方向)` → `sleep(duration)` → **`finally` 里必定** `focus_move_stop()`。
4. `sleep(FOCUS_SETTLE_POLL=0.45)` 等停稳，回到步骤 1；最多 `FOCUS_GOTO_MAX_ATTEMPTS=14` 轮。
5. 预算耗尽后再回读一次 `POS` 返回。

实测精度：目标 60/200/94 误差 0，120 误差 2（`api.py:553-554`，真机记录）。

## 4. 决策结果

**选择方案**：**Option B**

**选择理由**：只有 B 能在"精度"与"请求数"上都可接受——14 轮上限内通常 1–3 轮收敛；`finally` 保证马达必停（这是 A 方案同样需要但更易被忽略的安全点）。A 已被真机证伪，C 太慢，D 语义不同。

### 4.1 实施要点

- 速度常量来自真机实测（≈65 单位/s，`const.py:123-124`），**不是配置项**；换机型需重测。
- 脉冲时长必须 clamp（`api.py:570-571`）：短距离不至于过冲，长距离不至于空转数秒。
- 安全兜底：`try/finally` 包裹 sleep 并强制 `OFF`（`api.py:573-582`），注释说明"runaway focus motor"风险。
- 写后回读值写回 `settings["focus_position"]`（`number.py:297-299`），否则 UI 会回弹到旧位置（因为该键只在慢轮更新）。
- 回归测试锁定三性质：使用连续通路而非步数换算（`tests/test_regressions.py:41-65`）、sleep 被取消时仍发 `OFF`（`tests/test_regressions.py:66-88`）、`FOCUS_STEP_UNIT == 1`（`tests/test_regressions.py:89-95`）。

## 备注

- **现存缺口**：`finally` 内的 `await focus_move_stop()` **未** `asyncio.shield`（`api.py:575-582`），二次取消仍可能跳过 `OFF`（`risk.md` R-03）。
- **现存缺口**：`goto_focus` 与实体 setter 均无锁（`api.py:561-583`、`number.py:282-301`），并发两次定位会交错驱动马达（`risk.md` R-02）。
- **文档残留**：`number.py:229` 的类 docstring 仍写"实测一步 = 10 个单位"，与 `const.py:108-118` 的新结论矛盾，属未清理的旧假设，改动该文件时应一并修正。
- `tools/verify_round4.py:24` 另有一份 `STEP_UNIT = 10` 的旧假设副本，**不要**用它验证对焦（`risk.md` 技术债表已记录）。
