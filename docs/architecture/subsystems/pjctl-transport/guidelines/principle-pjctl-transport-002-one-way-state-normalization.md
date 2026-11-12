---

project: "ha-epson-ef-projector"

type: development-principle

id: principle-pjctl-transport-002-one-way-state-normalization

description: "电源判定的异常重分类是单向且不可逆的：存活探针的 Timeout 必须升级为 Unavailable（端点不可达），而存活通过后的任何引擎查询失败必须降级为 off（待机）。两个方向都不得反向，否则上层降级逻辑整体失效。"

base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c

principle_scope: module

principle_category: 架构

---

# 状态归一化单向不可逆

## 原则详细描述

`probe_state` 把两次不同性质的查询结果压缩成一个二元组 `("on"|"off", 灯时)`（`api.py:221-276`），压缩过程中做了两次方向的、互相不可逆的重分类：

**方向一：升级（Timeout → Unavailable）。** 存活探针 `CONFIG_GET_PRODUCT_NAME` 抛 `EpsonApiTimeout` 时，`probe_state` 捕获并改抛 `EpsonApiUnavailable`（`api.py:240-246`）。理由写在注释里：完全不回应的主机（市电断、IP 错、防火墙 DROP）表现为超时而非连接错误，"它同样不可达"。若不做这一步，只处理 `EpsonApiUnavailable` 的调用方会把断电的投影仪当成"活着但卡住"。

**方向二：降级（引擎失败 → off）。** 存活通过之后，`CONFIG_GET_LIGHT_TIME` 的任何失败——`EpsonApiTimeout`、`EpsonApiUnavailable`、`EpsonApiCommandFailed`——一律 `return ("off", None)`（`api.py:258-264`）。注释给出依据：该引擎查询在深待机下"或者 FAIL，或者服务端直接断连"，两者都只说明待机，绝不能上报为不可达。

两个方向的共同前提是**执行顺序**：先证活（任何电源态都应答的命令），再问"醒着吗"（仅唤醒态应答的命令）。顺序颠倒会让"断电"与"待机"不可区分。

## 为什么要这样

- **语义纯度换上层简单性**：协调器只需要两个分支就能处理三种物理状况——`EpsonApiUnavailable` → `UpdateFailed`（`coordinator.py:44-45`）、`EpsonApiCommandFailed` → 当作 off（`coordinator.py:46-49`）。如果模块把 Timeout 原样抛出，协调器要多写一个分支，且每个新调用方都要重复这段判断。
- **深待机的物理事实**：HB71 在深待机下不是"回一个错误码"，而是让 HTTP 线程挂住直到客户端超时（`api.py:248-251` 的注释）。因此"引擎查询超时"在语义上就是"待机"，不是"故障"。
- **两级探针是唯一可判据的方案**：固件没有统一的 power state 查询；`CONFIG_GET_PRODUCT_NAME` 恒应答但无状态信息，`CONFIG_GET_LIGHT_TIME` 有状态信息但不恒应答。只有组合才能既证活又判态。
- **为什么不引入 `"unknown"` 第三态**：一旦引入，所有消费方（`media_player.state`、`binary_sensor.is_on`、协调器的诊断门控 `coordinator.py:61`）都要处理三态；而物理上"没醒"与"读不到"在本设备上是同一件事，三态只会制造无法区分的分支。诚实性由**实体层**承担（`sensor.py:118-120` 的 `stale` 标记、乐观实体的 `assumed_state`），而不是由传输层承担。

## 适用范围

- `custom_components/epson_ef/api.py` 的 `probe_state` / `wait_for_state`。
- 任何新增"多步探测再合成一个结论"的方法（如未来的网络质量探测）：必须显式规定每一步失败的方向。
- 不适用于单条命令方法：它们必须原样暴露四类异常（见 `principle-pjctl-transport-001`）。

## 规则

1. 存活探针失败 → **只允许**抛 `EpsonApiUnavailable`（含从 `EpsonApiTimeout` 升级而来的情况），不得抛 `CommandFailed`。
2. 存活通过后的引擎查询失败 → **只允许**返回 `("off", None)`，不得上抛任何异常。
3. 新增一步探测时，必须明确它属于"证活"还是"判态"，并放在正确的顺序位置。
4. `wait_for_state` 不得重新引入第三种结果：它只有 `True`（观察到目标）/ `False`（未观察到，含异常），（`api.py:278-293`）。

## 反模式 / 禁止项

- ✗ 把 `except EpsonApiTimeout: raise` 直接放行到存活探针——会让断电被误判为"活着但卡住"。
- ✗ 在引擎查询失败时 `raise EpsonApiUnavailable`——会让待机被误判为不可达，协调器抛 `UpdateFailed`，全部实体 unavailable。
- ✗ 用 `except EpsonApiError` 一把抓两段——会把两个方向压成一个，语义丢失。
- ✗ 顺序颠倒（先问引擎再证活）——断电与待机不再可区分。
- ✗ 引入 `"unknown"`/`None` 状态值——消费方无法区分"未探测"与"探测失败"。

## 修改检查清单

- [ ] 存活探针与引擎查询的**顺序**是否仍是"先证活"？
- [ ] 存活探针的异常出口是否只有 `EpsonApiUnavailable`（Timeout 已改写）？
- [ ] 引擎查询的异常出口是否覆盖 `Timeout`/`Unavailable`/`CommandFailed` 三者且都返回 off？
- [ ] 改完后跑 `tests/test_api.py` 的 `ProbeTests`（`:165-215`，含 6 个用例覆盖超时/断连/verify 开关）
- [ ] 协调器的 `except` 分支（`coordinator.py:44-49`）是否仍与归一化方向匹配？
