---
project: "ha-epson-ef-projector"
type: adr
id: ADR-state-coordinator-003
description: 读命令失败时不把键写成 None，而是跳过该键并在轮询末尾用上一轮值结转（carry-forward），共 6 条规则；同时为可陈旧的值提供时间戳或 stale 标记。代价是陈旧度无上界，且读 settings 的 13 个实体目前没有陈旧标记。
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
status: accepted
title: "读失败结转旧值而非置空"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/coordinator.py
  - custom_components/epson_ef/sensor.py
  - custom_components/epson_ef/media_player.py
---

# ADR-003: 读失败结转旧值而非置空

## 状态

accepted

## 1. 背景

**业务背景**：`light_hours` 是 `SensorStateClass.TOTAL_INCREASING` 的累计量（`sensor.py:62-64`），用户会看它的长期曲线。13 个读 `settings` 的实体（6 开关 + 4 选择 + 3 数值）在关机后仍显示在 UI 上。用户期望这些值「关机后还在」，而不是每次关机就变 `unknown`。

**技术背景**：本模块的 35 条慢轮只读命令中，每一单条都可能在特定电源态失败（`api.py:51-53` 明示 `CONFIG_GET_LIGHT_TIME` 待机必 FAIL）。`api.py` 的所有 `get_*` 方法与 `get_raw` 在失败时统一返回 `None`（`api.py:340-351`），不抛异常。因此协调器面对的不是「成功或异常」，而是「值或 None」，必须自己决定 None 的语义。

**核心问题**：当一条读命令返回 `None` 时，快照里那个键应该怎么写——写 `None`、删掉键、还是保留上一轮值？

**约束条件**：

- HA 的 `CoordinatorEntity` 只在 `last_update_success=True` 时渲染，键缺失与键为 `None` 对实体 property 的影响不同（`None` 会让 `native_value` 返 `None` → `unknown`）。
- 慢轮每 12 轮才执行一次，快轮（11/12 的轮次）根本不产出 `settings`/`diag`。
- 电源态必须每轮新（见 ADR-002），不能结转。

## 2. 决策驱动因素（Decision Drivers）

1. **历史连续性**（重要性：高）：`TOTAL_INCREASING` 传感器断档会破坏长期统计。
2. **UI 稳定性**（重要性：高）：关机后设置值不应跳成「未知」。
3. **陈旧可辨识**（重要性：高）：结转的值必须能被识别为旧值，否则比置空更危险。
4. **实现简洁**（重要性：中）：每条读命令都要处理失败，逻辑不能太绕。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|**Option A：跳过键 + 末尾统一结转**（选中）|失败不写键，`:134-153` 用上一轮值补齐|历史连续；逻辑集中；快轮天然靠它存活|陈旧度无上界；必须配陈旧标记|用户看到旧值当新值（若标记缺失）|
|Option B：失败即写 `None`|`data[key] = raw`（含 None）|最简单，一行|每次关机灯时变 `unknown`，曲线断档；13 个 settings 实体在关机时全部未知|高|
|Option C：失败即删键|`if raw: data[key] = raw` 不结转|无陈旧值|快轮（11/12 轮次）不产出 settings/diag → 键消失 → 实体在 11 轮里全部 `unknown`，只在慢轮闪一下|极高|
|Option D：保留上一轮整个快照|失败时直接 `return self.data`|最简单|电源态也一起陈旧 → 盲 toggle 翻转错方向|高|
|Option E：逐键 TTL|每条命令带过期时间，超期则置空|陈旧有上界|需要为 27 个键各设 TTL；且「超期置空」在关机场景下与 Option B 同罪|中|

### Option A：跳过键 + 末尾统一结转（选中）

实现要点（`coordinator.py`）：

- **写入侧**：9 处 `if raw is not None:` 守卫（`:86`, `:94`, `:99`, `:103`, `:107`, `:111`, `:114`, `:122`, `:131`），只有非 None 才写键。注释明说理由（`:128-129`）：`A None here means "query failed in this power state" — do not overwrite the last known value with it`。
- **结转侧**：每轮末尾 6 条规则（`:134-153`），逐键判断「本轮是否产出」。
- **合并侧**：慢轮用 `dict(old)` + `update(new)`（`:117-119`, `:125-127`），使「本轮没读到的键」自动继承旧值。

### Option C：失败即删键（未选）

这是最容易被误认为「干净」的方案，但它对**快轮**是灾难性的：11/12 的轮次根本不执行慢段，`settings`/`diag` 从不产生。若不结转，13 个 settings 实体在其中 11 轮里全部 `unknown`，只在第 12 轮闪一下真值。这比陈旧值糟糕得多。

### Option D：保留整个旧快照（未选）

一行 `return self.data` 就能"解决"所有失败场景，但它会让 `state` 也陈旧。`media_player.py:178-199` 的电源路径依赖 `state` 做盲 toggle 前的决策——用陈旧 `state` 判断会翻转错方向（设备已关但以为还开着 → 发 POWER 键把它打开）。这是本模块最危险的失误模式，因此 `state` **明确排除在结转之外**（`:51` 无条件覆盖）。

### Option E：逐键 TTL（未选，但部分值得借鉴）

TTL 能让陈旧度有上界，理论上更严谨。但 27 个键各设 TTL 是巨大的配置面，且「超期置空」在关机场景下退化为 Option B。当前的替代做法是**用具体时间戳表达单点新鲜度**（`light_measured_at`），成本远低于通用 TTL 机制。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

- 对照因素 1（历史连续性）：A 胜，结转保证 `light_hours` 不中断。
- 对照因素 2（UI 稳定性）：A 胜，关机后设置值仍在。
- 对照因素 3（陈旧可辨识）：A 需要配套措施才能满足，这是**选择 A 的前置条件**——必须同时提供时间戳或 `stale` 标记。实现了的部分：`light_hours` → `light_measured_at`（`:56`）+ `sensor.py:76` 的 `stale`；`distance_mm` → `sensor.py:118-119` 的 `stale`。
- 对照因素 4（简洁）：A 的结转逻辑集中在 20 行内，可读。

### 4.1 实施要点：6 条结转规则的精确语义

| # | 键 | 判据 | None 守卫 | 边界行为 | 证据 |
|-|-|-|-|-|-|
| 1 | `product` | `"product" not in data and self.data` | **无** | 旧值为 None 时显式写回 None；消费方 `switch.py:83` 用 `or "Epson EF Projector"` 兜底 | `:135-136` |
| 2 | `standby_mode` | 同上 | **无** | 但 `:131` 已挡住「读到 None 覆盖旧值」，故实际安全 | `:137-138` |
| 3 | `light_hours` / `light_extra` / `light_measured_at` | `"light_hours" not in data` | 无（三键捆绑） | **三键必须同生同死**，否则出现「新读数配旧时间戳」的假新鲜 | `:139-144` |
| 4 | `diag` | `"diag" not in data and self.data` | **有**（`:148` 判 `previous is not None`） | 引用复制，非深拷贝 | `:145-149` |
| 5 | `settings` | 同上 | **有**（`:152`） | 引用复制；与 `:117` 的合并构成两重保护 | `:150-153` |
| 6 | `state` | **不结转** | — | 每轮无条件被 `probe_state` 覆盖 | `:51` |

**规则 6 的不对称是刻意的**，并在代码中被间接说明：`:128-129` 的注释只针对「诊断值」，而 `:51` 位于所有条件之外。

**速度/功耗/一致性权衡**：结转不产生任何额外请求，纯内存操作，成本为零。

## 备注

- **已知缺口 1：陈旧度无上界**。若设备连续 N 个慢轮都读不到某值，该值会无限期保持最后一次成功读数。唯一的时间信息是 `light_measured_at`（且只在 `light_hours` 上）。其余 26 个键（`diag` 17 + `settings` 10 中的大部分）**没有任何时间信息**。
- **已知缺口 2：13 个读 `settings` 的实体无陈旧标记**。`switch.py`、`select.py`、`number.py` 的 `settings` 类实体都没有 `stale` 属性，也不暴露 `measured_at`。用户在设备关机后看到「亮度 100%」无法分辨这是当前值还是关机前的值。`sensor.py` 的两个传感器有 `stale`（`:76`, `:118-119`），形成不一致（R-SC-03）。
- **已知缺口 3：`product` 与 `standby_mode` 的结转缺 None 守卫**，与 `diag`/`settings` 的写法不对称。当前无害（`:122` 与 `:131` 已挡住 None），但属脆弱的隐式依赖。
- **与实体的隐式协作**：实体侧写乐观值时直接改 `coordinator.data["settings"]`（`switch.py:109-110`、`select.py:110-111`、`number.py:129-130`, `:216-217`, `:297-298`, `:392-393`），而本模块 `:117` 的 `dict()` 浅拷贝会把这些乐观值一并带入下一轮。这个协作**未被任何文档或类型声明固化**，若将来改成深拷贝或重建快照，乐观值会立即消失（R-SC-14）。
