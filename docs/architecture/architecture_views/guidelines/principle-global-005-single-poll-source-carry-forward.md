---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-global-005
description: "所有实体状态只有一个上游来源——Ef50Coordinator 的 data 字典；实体不得各自发起读请求。读失败时不得把键写成 None，而应省略该键并在轮询末尾用上一轮值结转（carry-forward），配合 stale 标记暴露陈旧性。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
principle_category: 架构
principle_scope: global
---

# 单一轮询源 + carry-forward 结转

## 原则详细描述

58 个实体没有任何一个自己发读请求取状态。所有读都在 `Ef50Coordinator._async_update_data` 里完成，写进一个 `dict[str, Any]`，实体通过 `CoordinatorEntity` 的 property 读它（`coordinator.py:37-154` 是唯一写入方）。

数据分为四组键（`coordinator.py:51-153`）：

| 键 | 更新频率 | 生产位置 | 典型消费者 |
|-|-|-|-|
| `state`（on/off） | 每轮 | `coordinator.py:51` | 全部平台 |
| `light_hours` / `light_extra` / `light_measured_at` | 开机时每轮 | `coordinator.py:52-56` | `sensor.py:67-83`、`media_player.py:114-120` |
| `settings`（可写设置原始串） | 每 12 轮一次诊断 | `coordinator.py:91-119` | switch / select / number |
| `diag`（只读诊断原始串） | 每 12 轮一次诊断 | `coordinator.py:60-88`, `:124-127` | sensor / number(增益) / media_player 属性 |

关键的容错机制是 **carry-forward 而非置空**：读失败时命令返回 `None`，代码**不把 `None` 写进 data**，而是跳过该键，然后在周期末统一用 `self.data` 的旧值补齐（`coordinator.py:134-153`）。注释明确解释原因：`None` 表示"该状态下查不到"，不能用它覆盖上一次已知值（`coordinator.py:128-129`）。

## 为什么要这样

1. **固件能力本就时有时无**：`CONFIG_GET_LIGHT_TIME` 待机时必然 FAIL（`api.py:330-338` docstring 明示），`CONFIG_GET_*` 系列在待机时大量 FAIL。如果读失败即置 `None`，每次关机都会把灯时传感器打成 unknown，历史曲线断档。
2. **58 实体 × 独立轮询 = 请求风暴**：固件是单一 HTTP 端点（且疑似单线程），任何"每实体自己拉"的设计都会让请求量随实体数线性膨胀。单轮询源让成本恒定为 O(1) 请求族。
3. **carry-forward 让"陈旧"可被表达而非被隐藏**：值保留，但配 `stale` 属性（`sensor.py:75-83` 判 `data.get("state") != "on"`），下游自动化可以据此 guard。这比"值消失"信息量更大。

## 适用范围

- 全部 `CoordinatorEntity` 子类
- `coordinator.py` 的 `_async_update_data`
- 新增平台/实体

## 规则

- 实体状态**只**从 `coordinator.data` 读；不得在 property 里 `await api.xxx()`。
- 唯一允许的"实体侧主动 IO"是：写操作本身、写后 `async_request_refresh()`、以及电源路径的动作前探测（`media_player.py:151-157` `_probe()`，因为盲 toggle 必须先知道当前态）。
- 读失败 → 不写键（不是写 `None`）→ 由 carry-forward 补齐。
- 新增可能陈旧的数据，必须同时提供 `stale` 或 `measured_at` 属性。
- 写入乐观值时，**写进 `coordinator.data` 而不是只写 `self._attr_*`**——否则下一帧渲染会被陈旧 data 覆盖回弹（`number.py:110-121`、`:129-131` 的设计原因）。

## 反模式 / 禁止项

- ❌ 在实体 property 里发 HTTP（会造成每帧渲染一次请求）。
- ❌ 读失败时 `data[key] = None`（会让 carry-forward 失效并把传感器打成 unknown）。
- ❌ 新增第二个轮询器（如给某平台单独建 coordinator）。
- ❌ 乐观写只改 `self._attr_native_value` 而不同步 `coordinator.data` —— 用户会看到值"弹回"。
- ❌ 把待机时本就不刷新的键（`settings` / `standby_mode` / `focus_position`）当成"每轮都更新"来设计逻辑：它们只在诊断轮更新（`coordinator.py:61`, `:116-119`, `:130`；详见 `architecture_views/process_view.md` §5.4 的一致性差异表）。

## 修改检查清单

- [ ] 新实体是否只读 `coordinator.data`？
- [ ] 新增的读命令是否加进了 `coordinator.py` 的慢诊断段（而不是实体里）？
- [ ] 读失败路径是否保持"跳过键"语义、没有被改成写 `None`？
- [ ] 陈旧可能的值是否带 `stale` / 时间戳？
- [ ] 写后乐观值是否同时写进 `coordinator.data`？
- [ ] 该键在待机时是否也被刷新？（不是的话，UI 上的"刷新了没变化"是预期行为）
