---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-global-001
description: "协议传输层（api.py）必须保持零 Home Assistant 依赖：只用 aiohttp + stdlib，从而让协议编解码与错误分类能在没有 HA 运行时的环境里被离线单测与 tools/ 脚本直接复用。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
principle_category: 架构
principle_scope: global
---

# 传输层与 HA 运行时解耦

## 原则详细描述

`custom_components/epson_ef/api.py` 是整个集成里唯一直接说 pjctl 协议的地方，它必须**不 import 任何 `homeassistant.*`**。模块 docstring 直接写死了这条契约：

> `"""Async client for the projector's native WebApi (pjctl over HTTP). Pure aiohttp + stdlib; no Home Assistant imports so it can be tested standalone."""`
> — `custom_components/epson_ef/api.py:1-3`

实测其 import 面只有 stdlib（`asyncio/hashlib/logging/random/re/typing`，`custom_components/epson_ef/api.py:7-12`）＋ `aiohttp`（`:14`）＋包内 `const`（`:16`）。所有 HA 概念（`ConfigEntry`、`DataUpdateCoordinator`、`HomeAssistantError`）都被关在 `coordinator.py` 与各平台文件里。

配套后果是三层收益同时成立：单测不需 HA（`tests/test_api.py` 全程不 import homeassistant，实测 64 用例通过）；`tools/` 的脚本用 `importlib` 直载 `api.py` 做真机验证；协议修复可以在秒级回归而不必起 HA。

## 为什么要这样

1. **CI 不装 HA**（`.github/workflows/tests.yml:18` 只 `pip install aiohttp ruff pyflakes`）。如果 `api.py` import 了 HA，整个协议层就变成 CI 不可测——而协议层恰恰是 bug 最密集的地方（`size` 语义、Digest、电源态判定）。
2. **真机验证成本极高**。现场分析脚本必须复用生产代码的命令构造逻辑，否则会出现"脚本能写、集成不能写"的分叉（否则容易出现"脚本能写、集成不能写"的分叉）。
3. **错误语义需要独立于框架**。4 类异常（`EpsonApiError` / `EpsonApiUnavailable` / `EpsonApiCommandFailed` / `EpsonApiTimeout`，`api.py:40-61`）是协议层对"设备状态"的判断，由协调器再翻译成 HA 语义；反向依赖会让这套分类被框架细节污染。

## 适用范围

- `custom_components/epson_ef/api.py`（强制）
- 新增的协议辅助模块（应遵守）
- `tools/` 下任何需要发 pjctl 的脚本（消费方）

## 规则

- `api.py` 内**不得**出现 `import homeassistant` / `from homeassistant ...`。
- `api.py` 抛出的异常必须是自定义的 `EpsonApi*` 家族，不得抛 `HomeAssistantError`（后者只在平台层转换，如 `media_player.py:142-149`）。
- 新增命令方法只依赖构造 params 字符串与解析响应文本；设备状态的语义判断（什么算 on/off）留在 `api.py`，是否可用/是否显示交给平台。
- 平台层若捕获 `EpsonApiError`，必须转成 `HomeAssistantError` 再上抛（用户可见的错误语义）。

## 反模式 / 禁止项

- ❌ 在 `api.py` 里读 `hass.data`、用 `dt_util`、或记 HA 日志 handler。
- ❌ 为图省事把 `ConfigEntry`/`coordinator` 传进 `EpsonApi` 构造函数。
- ❌ 让测试文件为了 import `api.py` 而必须安装 homeassistant（现有测试用 `types.ModuleType("epson_ef")` 伪造包绕过 `__init__.py`，`tests/test_api.py:23-26`，这是绕行手段而非许可）。

## 修改检查清单

- [ ] `grep -n "homeassistant" custom_components/epson_ef/api.py` 无命中。
- [ ] 新增命令方法的返回值可被不依赖 HA 的单测断言（无 HA 对象入参）。
- [ ] `python3 -m pytest tests/ -q` 仍全绿（当前基线 64 passed）。
- [ ] 若新增异常类型，是否属于 `EpsonApiError` 家族、并在平台层有对应的用户语义转换？
