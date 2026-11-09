---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-verification-harness-003
description: "真机结论必须按『探测脚本 → 载荷断言 → 缺陷回归用例』三步固化，且每条断言都要保留设备型号、日期、实测值。测试脚本不得自己复刻生产算法（否则会造出与生产分叉的第二份事实），必须 import 生产实现或用生产常量。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
principle_category: 工程
principle_scope: module
---

# 真机结论三步固化，禁止第二份事实

## 原则详细描述

本项目的协议知识来源不是文档，而是 HB71 真机回环。一条固件事实的生命周期有固定三步：

| 步骤 | 载体 | 例子 |
|-|-|-|
| ① 探测 | 用 `tools/` 下的脚本（importlib 直载 `api.py`）对真机发命令 | `tools/verify_full.py`、`tools/diagnose_state.py` |
| ② 固化到生产 | 写进 `api.py` 的方法 + `const.py` 的常量/注释 | `api.py:590-598` 的 `geometry_zoom` docstring 含 "Live verification: '1 104' then '1 -104' returned GET_QC to the exact 8-token baseline (zero residue)" |
| ③ 固化成断言 | `tests/test_api.py` 的 `RoundNTests` + `tests/test_regressions.py` 的缺陷类 | `Round5Tests`（`tests/test_api.py:354`）、`Round6Tests`（`:425`） |

配套的**禁止项**是这条原则的核心：**不得在验证设施里重新实现生产算法**。因为一旦复刻，就出现了两份"事实"，改了一处另一处不动，验证结论会与生产行为分叉。

反例：曾有脚本自己写了 `STEP_UNIT = 10`、`MAX_STEPS = 30` 并据此换算对焦目标、以 `< STEP_UNIT` 判收敛。而生产实现是：

- `const.py:122` `FOCUS_STEP_UNIT = 1`
- `const.py:105-118` 的纠正注释："INC 124→134(+10)、134→135(+1)…因此'按 ±10 换算步数'的旧算法无法把焦点送到远距离目标（实测：目标 150、起点 89 时只到 128 就放弃）"
- `api.py:544-584` 的 `goto_focus` 改为 MAX/MIN 连续移动 + 按剩余距离定时 + OFF + 回读的闭环

**用这类脚本验收，会得到与集成相反的结论**。这不是理论风险：该假设曾在真机上导致缺陷。

## 为什么要这样

- **真机时间是最贵的资源**。每次探测都要开关机、可能打挂服务器、需要人在设备旁。因此每条结论都必须留下可追溯的"型号 + 日期 + 实测值"，让下一个人不必重跑。
- **注释即证据**。`tests/test_api.py` 的 docstring 保留了大量第一手数据：`Round4Tests`（`:300`）、`Round5Tests`（`:355-356`）、`GET_FOCUS "RANGE" → "0 264"(HB71 实测)`（`:322`）、`GET_COLOR_GAIN "0" → "R G B" 三元组(实机出厂 81 90 100)`（`:359`）。这些数字让"为什么是 104 不是 100"这类问题可以直接回答。
- **三步缺一即失效**：只有 ① 则结论留在某人的 /tmp 里；只有 ② 则下次重构会改回去；只有 ③ 则测试断言的是想象而非设备。
- **复刻算法是最隐蔽的错误**：它看起来"更独立、更可信"（不依赖生产代码），实际上把"验证"变成了"两个实现互相证明"，而两者不一致时无法判断谁对。

## 适用范围

- `tools/probe_*.py` / `tools/verify_*.py` 的全部结论
- `api.py` 的方法 docstring
- `tests/test_api.py` 的 `RoundNTests`
- `tests/test_regressions.py` 的全部回归类（每条对应一次真机事故）

## 规则

- **每条真机断言必须带三要素**：设备型号（HB71）、日期（2026-11-01 / 2026-11-07）、实测值（`0 264`、`81 90 100`、`error 0 / error 2`）。
- **验证脚本必须加载生产实现**：`importlib` 直载 `api.py`（20/20 脚本已做到），而不是自己拼 HTTP 请求或复刻算法。
- **需要生产常量时必须 import**，不得在脚本里重写字面量。若脚本刻意测量"与生产不同"的假设，必须在文件头写明"本脚本验证的是旧假设，仅供对照"。
- **每条实测结论必须配对**：真机观察到的行为 ↔ `tests/` 中的对应断言。
- **危险命令必须有显式禁止注释**：`tools/probe_refresh.py:9-11` 写"RefreshModeActivity(startRefreshMode):SET_REFRESH size=1 params='1' = 立即开始刷新并 PowerManager.shutdown(关机跑刷新)⚠️ 本脚本绝不发送 '1'"；`api.py:481-487` 的 `set_refresh_timer` docstring 重复了同一警告。**同一危险事实写两遍是刻意的**。

## 反模式 / 禁止项

- ❌ 在 `tools/` 里重写 `STEP_UNIT`、`QC_ZOOM_STEP`、`AMBIENT_BRIGHTNESS_FW_SCALE` 之类的生产常量——应直接 import `const.py`。
- ❌ 断言"当前实现的行为"而不写"固件实测的行为"——前者随重构变化，后者是事实。
- ❌ 只写 `self.assertEqual(final, 150)` 而不写"为什么不是按步数换算"。
- ❌ 真机探测完成后不落断言，只留在脚本输出里。
- ❌ 把某台设备的实测值（`0 264` 行程、`81 90 100` 增益）当作所有机型的普适常量而不标注来源。

## 修改检查清单

- [ ] 新增 `api.py` 方法后，`RoundNTests` 是否有对应载荷断言？
- [ ] 断言/docstring 是否带型号 + 日期 + 实测值？
- [ ] 该条实测结论是否在 `tests/` 中有对应断言？
- [ ] 脚本里有没有重写生产常量？用 `grep -n "= [0-9]" tools/*.py` 逐个确认。
- [ ] 危险/不可逆命令（关机、立即刷新、持续移动马达、改写几何）是否在脚本头与生产方法 docstring 各写一次警告？
- [ ] 结论若只适用于 HB71，是否在注释里显式限定？
