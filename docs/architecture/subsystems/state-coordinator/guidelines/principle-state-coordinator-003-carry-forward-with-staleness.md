---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-state-coordinator-003
description: 读失败时跳过键而不是写 None，并在轮询末尾统一用上一轮值结转；同时必须让「陈旧」对下游可见（时间戳或 stale 标记）。这两件事是成对的——只做结转不做标记，等于用旧值伪装新值。
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
principle_category: 架构
principle_scope: module
---

# 结转旧值必须同时暴露陈旧性

## 原则详细描述

本模块的容错策略是 **carry-forward（结转）而非置空**：任何读命令返回 `None` 都不写入快照键，而是跳过该键，在轮询末尾统一用 `self.data` 的上一轮值补齐（`coordinator.py:134-153`）。

具体 6 条规则与其边界行为：

| # | 键 | 守卫 | 边界行为 |
|-|-|-|-|
| 1 | `product` | 无 `is not None` | 旧值为 `None` 时显式写回 `None`（消费方 `switch.py:83` 用 `or` 兜底） |
| 2 | `standby_mode` | 无 `is not None` | 同上，但 `:131` 已挡住「读到 None 覆盖旧值」 |
| 3 | `light_hours` + `light_extra` + `light_measured_at` | 以 `light_hours` 缺失为唯一判据 | **三键捆绑**结转，保证时间戳与读数同生同死 |
| 4 | `diag` | 有 `is not None` | 引用复制，非深拷贝 |
| 5 | `settings` | 有 `is not None` | 引用复制；与 `:117` 的合并构成两重保护 |
| 6 | `state` | **不结转** | 每轮无条件覆盖（`:51`）——刻意为之 |

代码注释直接给出了理由（`:128-129`）：

> `# A None here means "query failed in this power state" — do not`
> `# overwrite the last known value with it (see carry-forward below).`

以及 `:140-141`：

> `# keep last lamp reading visible while off, together with the`
> `# timestamp that says when it was actually measured`

**关键点：结转与陈旧标记是一对，不能拆开。** 结转让值「看起来还在」，如果同时不告诉下游「这是旧的」，下游会把旧值当新值用——这比置空更危险，因为置空至少是显式的。所以本模块为需要新鲜度语义的值配了标记：

- `light_hours` → `light_measured_at`（`:56`，ISO 字符串）+ `sensor.py:76` 的 `stale` 属性（判据 `data.get("state") != "on"`）。
- `distance_mm` → `sensor.py:118-119` 的 `stale` 属性。

## 为什么要这样

1. **置空会打断历史**：`light_hours` 是 `SensorStateClass.TOTAL_INCREASING`（`sensor.py:64`）。每次关机置 `None` 会在 HA 历史里造成断档，累计统计变得不可信。
2. **弱固件下读失败是常态而非异常**：待机时大量 `CONFIG_GET_*` 必然 FAIL（`api.py:51-53`）。如果读失败即置空，则每次关机都会清空 13 个 settings 实体的当前值，用户界面在「关机」与「未知」之间反复跳。
3. **陈旧是信息，不是噪声**：一个带时间戳的旧灯时告诉用户「上次测量是 3 小时前」，这比 `unknown` 信息量大得多。`sensor.py:75-77` 的注释明说 `# True while the value is carried over from the last awake poll.`
4. **不结转 `state` 是同一原则的另一面**：电源态不允许陈旧，因为 `media_player.py:178-199` 依赖它做盲 toggle 前的决策——用陈旧的 `state` 判断会翻转错方向。所以「哪些值可以陈旧」是逐个判定过的，不是统一的。

## 适用范围

- `coordinator.py:134-153` 的全部结转分支
- `sensor.py:70-83`、`:111-119` 的 `stale` 属性
- 新增任何可能被结转的值

## 规则

- **读失败 → 不写键**（`if raw is not None:` 后再写），**绝不** `data[key] = None`。
- **新增可陈旧值时必须同时提供三种之一**：`<key>_measured_at` 时间戳、`stale` 布尔属性、或在实体 `available` 里叠加条件。
- **判断该值能否陈旧**：涉及**动作决策**的（电源态、写入前置条件）不得陈旧，必须每轮新或明确失败；涉及**展示与统计**的可以陈旧，但必须带标记。
- **捆绑结转**：逻辑上同一时刻测量的一组值必须在同一分支内一次写齐（参照 `:139-144` 的三键）。否则会出现「新灯时配旧时间戳」的假新鲜。
- **`merged` 必须是新字典**（`:117` 的 `dict(...)`），避免实体原地写入污染上一轮快照。

## 反模式 / 禁止项

- ❌ `data["light_hours"] = None` 表示「读不到」——会让传感器变 `unknown` 并打断 `TOTAL_INCREASING` 统计。
- ❌ 只做结转不给标记：「亮度 100%」在已关机的设备上显示，用户无法分辨。
- ❌ 把电源态也结转：「上一轮是 on 所以这轮也用 on」——盲 toggle 会翻转错。
- ❌ 单独结转 `light_hours` 而漏掉 `light_measured_at`——时间戳不跟随会让陈旧值看起来像刚测的。
- ❌ 在结转分支里用 `self.data.get(k)` 却不判 `self.data` 是否为 `None`（`:135`, `:137`, `:139`, `:145`, `:150` 都做了 `and self.data` 守卫）。
- ❌ 假设「结转的分支一定被执行」：`:124-127` 的 `diag` 合并在 `:61` 块外，待机慢轮里 `diag` 恒空故 `if diag:` 为假，实际是靠 `:145` 的结转兜的——不要把这个巧合当成保证。

## 修改检查清单

- [ ] 新增的读命令失败时，是「跳过键」还是「写 None」？（必须跳过）
- [ ] 新增的可陈旧值是否配了 `stale` / `_measured_at` / `available` 条件？
- [ ] 该值参与**动作决策**吗？若是，它不应该被允许陈旧。
- [ ] 一组同刻测量的值是否在同一分支内捆绑写齐？
- [ ] `self.data` 为 `None`（首轮）时该分支是否安全？
- [ ] 是否有人把 `state` 加进了结转？（禁止）
- [ ] 新的 `merged` 是 `dict(...)` 新对象还是原地改？（必须新对象）
