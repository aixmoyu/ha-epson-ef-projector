---

project: "ha-epson-ef-projector"

type: development-principle

id: principle-pjctl-transport-001-reads-degrade-writes-throw

description: "传输层的读方法与写方法遵循有意不对称的失败契约：读（`get_*`）在命令于当前电源态不可用时返回 None，绝不抛错；写（`set_*`/`send_*`/`geometry_*`）失败必须上抛。这条不对称是整个集成降级逻辑（carry-forward、乐观状态、错误提示）成立的前提。"

base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3

principle_scope: module

principle_category: 工程

---

# 读降级为 None、写上抛（不对称失败契约）

## 原则详细描述

`EpsonApi` 的 14 个 `get_*` 读方法中有 13 个（`get_light_time` 例外，见下）用同一段骨架：调用 `request()`，捕获 `EpsonApiError`，返回 `None` 而不是让异常穿透（`api.py:295-315`、`:340-351`、`:353-378`、`:432-448`、`:466-514`、`:634-656`）。语义不是"值为空"，而是**"该命令在当前电源态下不可用"**——这是投影仪固件的常态：同一个 `CONFIG_GET_*` 在唤醒时返回数值、在普通待机返回 FAIL、在深待机干脆挂断连接。

写方法则相反：`set_standby_mode`（`api.py:324-325`）、`set_light_output`（`:376-378`）、`set_picture_preference`（`:384-385`）、`set_ambient_light`（`:387-398`）、`set_ambient_brightness`（`:400-409`）、`set_ambient_mode`（`:411-418`）、`set_test_pattern`（`:420-430`）、`set_color_gain`（`:449-460`）、`set_refresh_timer`（`:481-488`）、`set_auto_contrast`（`:623-632`）、`send_keyevent`（`:327-328`）、`geometry_*`（`:590-618`）、`focus_*`（`:516-542`）**全部不捕获异常**，让 `EpsonApiUnavailable`/`EpsonApiCommandFailed`/`EpsonApiTimeout` 直达平台层，由平台层翻译成 `HomeAssistantError`（如 `number.py:290-295`、`switch.py:172-177`、`number.py:126-128`）。

唯一被有意排除在读契约之外的是 `get_light_time`：它在待机时抛 `EpsonApiCommandFailed`（`api.py:330-338`），因为"灯时读不到"必须与"灯时为 0 小时"区分开，否则新机（真实 0 小时）会被误判为不可读。

## 为什么要这样

- **读是轮询的常态路径**：协调器每 15s（`const.py:11`）轮询一次，其中 30+ 条诊断读只在唤醒态执行（`coordinator.py:59-104`）。如果读失败抛错，每一轮待机都会产生一条异常与一条日志，噪声淹没真实故障，且 `DataUpdateCoordinator` 会把它当成整轮失败。
- **None 是 carry-forward 的信号**：协调器用 `if raw is not None:` 决定是否覆盖旧值（`coordinator.py:86-87`、`:94-95`、`:99-100`），并在周期末尾显式结转（`coordinator.py:128-132`、`:139-144`）。若读返回异常或空串，这套结转逻辑就无从判断"该保留还是该清零"。
- **写失败必须让用户知道**：写是用户主动动作，静默吞掉会让 UI 显示"已设置"而设备毫无变化。平台层因此统一把异常转成 `HomeAssistantError`，HA 会把错误弹给用户。
- **为什么不用统一返回 `Result[T]` 类型**：本模块刻意不引入 homeassistant 之外的自定义类型（`api.py:1-3` 的无 HA 依赖目标），`T | None` 是零成本表达；代价是丢失错误原因（见 risk R-T22/R-T26 的推论）。

## 适用范围

- 全部新增的 `EpsonApi` 公开方法（`custom_components/epson_ef/api.py`）。
- 任何"读固件状态"与"改固件状态"混用的新方法：必须显式选择一边，并在 docstring 写明。
- 不适用于 `request()` 本身——它是原始管道，永远抛错（`api.py:147-216`）。

## 规则

1. 读方法签名必须是 `-> T | None`，内部最后一步是 `except EpsonApiError: return None`，且异常捕获范围**只能是 `EpsonApiError` 及其子类**，不得用裸 `except Exception`。
2. 解析失败（token 数不对、`int()` 失败）与命令失败必须走同一个 `None` 出口（对照 `api.py:441-447`）。
3. 写方法不得捕获 `EpsonApiError`；若需要做清理（如 `goto_focus` 的 OFF 兜底），只在 `finally` 内处理且**不得重新抛出掩盖原异常**（`api.py:573-582` 是唯一被允许的例外，且只记 warning）。
4. 需要在"读不到"与"值为零"之间区分时，必须升格为抛异常并在 docstring 说明（`get_light_time` 是先例，`api.py:330-338`）。

## 反模式 / 禁止项

- ✗ 在读方法里 `raise`：会让待机轮询变成异常风暴。
- ✗ 在读方法里返回 `""`/`0`/`-1` 冒充失败：caller 无法与真实值区分（`get_serial_number` 明确把空串归一化为 None，`api.py:315`）。
- ✗ 在写方法里 `except EpsonApiError: pass` 或返回 bool：用户会以为设置成功。
- ✗ 用裸 `except Exception` 包住写路径——`services.py:64-66` 是**反例**（它把写失败吞成事件字典），不得被复制到模块内。

## 修改检查清单

- [ ] 新读方法是否返回 `T | None` 且只在 `except EpsonApiError` 处返回 None？
- [ ] 新写方法是否完全不捕获 `EpsonApiError`？
- [ ] 若该命令在待机时会挂起 HTTP 线程，是否在 docstring 提醒调用方自行限时（`api.py:343-346` 是范例）？
- [ ] 平台层对应实体是否把异常翻译成 `HomeAssistantError`（不要漏成裸 raise）？
- [ ] 是否有测试覆盖"命令失败 → None"与"命令失败 → 异常"两条分支？
