---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-entity-platforms-005
description: available 只能表达"该实体的数据源当前是否可信"，不能用 coordinator.last_update_success 冒充"设备是否唤醒"。待机时 HTTP 仍可达、last_update_success 为 True，因此照抄基类语义的 7 处覆写对"仅唤醒可读"的读数（对焦/亮度/增益）不做任何屏蔽；并且只有 connectivity 与 button 两处"恒 True"是有意例外，必须保留注释说明理由。
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
principle_category: 架构
principle_scope: module
---

# available 表达数据可信度，不表达电源状态

## 原则详细描述

`CoordinatorEntity.available` 的基类语义已经是 `coordinator.last_update_success and super().available`。本模块有 **9 处** `available` 覆写，可分为两类：

**A 类：恒 True（2 处，有意例外，必须保留）**

| 位置 | 实现 | 理由 | 证据 |
|-|-|-|-|
|`Ef50ConnectivitySensor`|`return True`|"连通性传感器必须保持可用才能报告 off"；若跟随 `last_update_success`，设备离线时实体会变 unavailable，永远无法表达"离线"这一事实|`binary_sensor.py:49-52`|
|`Ef50Button`|`return True`|"离线时保留按钮，按下会得到明确错误而不是被禁用"|`button.py:131-134`|

**B 类：与基类等价（7 处，冗余）**

`media_player.py:95-97`、`number.py:257-260`（对焦）、`number.py:347-349`（色彩增益）、`select.py:88-90`、`select.py:138-140`、`switch.py:86-88`、`sensor.py:52-54` 全部写 `return self.coordinator.last_update_success` —— 与基类实现相比，只少了 `and super().available` 一项（该项默认 True）。

其中 `number.py:258-260` 的注释是**误导性的**：

```python
@property
def available(self) -> bool:
    # 对焦读数只在唤醒状态有效,跟随协调器在线状态
    return self.coordinator.last_update_success
```

"只在唤醒状态有效"与"跟随在线状态"是两件不同的事：待机时投影仪 HTTP 端口仍然应答（`probe_state` 的 liveness 探针 `CONFIG_GET_PRODUCT_NAME` 在所有电源态都能回，`api.py:226-233`），`last_update_success` 保持 True；而对焦 `POS` 在待机时读不到（`api.py:562-564`），协调器会把上一轮的 `settings["focus_position"]` 通过 carry-forward 原样结转（`coordinator.py:150-153`）。结果是：**设备已关机，对焦实体仍 available，显示的是关机前的旧位置**，且没有 `stale` 标记（对比 `sensor.py:76` 的灯时传感器有）。

同样的陈旧暴露面还包括光源亮度、色彩增益与 14 个 `settings` 消费点，以及 6 个 `diag` 消费点（含 `media_player` 的 16 键属性白名单）。

## 为什么要这样

1. **`available` 是自动化可见的信号**。用户写 `condition: state: entity_id: number.focus_position, state: unavailable` 来防误操作，如果 available 不随唤醒态变化，这条自动化就失效，用户会对着一张黑屏拖对焦滑条。
2. **"保留按钮以便报错"与"保留数值以便报错"价值不同**。按钮是**动作**，失败时能立刻给出明确错误（`button.py:139-141`）；数值是**读值**，available 时用户会当真值相信。两者的例外处理不应对称。
3. **注释与实现不符比没有注释更糟**。`number.py:259` 声称"只在唤醒状态有效"，会让后来者以为已有保护而不再添加。

## 适用范围

- 全部 58 个实体的 `available` 实现。
- 特别针对"仅唤醒可读"的项：`number.focus_position`、`number.light_output`、`number.color_gain_*`、`sensor.light_hours`、`sensor.distance`。
- 恒 True 的两个例外（connectivity / button）**适用但不得修改**，改动需先反驳其注释中的理由。

## 规则

- 覆写 `available` 前先确认基类语义是否已足够；等价覆写应删除（减少误读）。
- 若某读值"仅唤醒有效"，`available` 必须叠加 `(self.coordinator.data or {}).get("state") == "on"`，或至少暴露 `stale` 属性。
- 恒 True 必须配一行注释说明"为什么保持可用"。
- 注释描述的语义必须与实现一致；不得用"跟随在线状态"描述"跟随唤醒状态"。

## 反模式 / 禁止项

- ✗ 复制粘贴 `return self.coordinator.last_update_success` 到一个"只在唤醒时可读"的实体上并附一句"只在唤醒时有效"。
- ✗ 让 connectivity 传感器跟随 `last_update_success`（会导致离线时传感器消失）。
- ✗ 让按钮在离线时 unavailable（用户失去"按下得到明确错误"的诊断路径）。
- ✗ 用 `assumed_state` 代替 `stale` 来表达"读数陈旧"——前者表示"从未读到"，后者表示"曾经读到但已过期"。

## 修改检查清单

- [ ] 新增的 `available` 覆写是否与基类等价？等价就删掉。
- [ ] 该实体的数据在待机时能否读到？（查 `coordinator.py:61` 的 `if state == "on":` 分支）
- [ ] 若不能，是否叠加了唤醒判断或 `stale` 属性？
- [ ] 若是恒 True 的例外，注释是否说明了理由？
- [ ] 注释里的措辞是否精确（"在线"≠"唤醒"）？
