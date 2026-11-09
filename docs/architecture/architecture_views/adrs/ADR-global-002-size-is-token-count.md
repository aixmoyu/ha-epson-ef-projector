---
project: "ha-epson-ef-projector"
type: adr
id: ADR-global-002
description: "pjctl 请求体的 size 字段定义为 params 的空格分词数（token count），而非字节长度；该定义被固化为 request() 内的唯一计算点，是打通所有 CONFIG_SET_* 写通路的根因。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "size 字段语义定为 params 的分词数（token count）"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/api.py
  - custom_components/epson_ef/const.py
  - tests/test_api.py
---

# ADR-002: `size` = params 的分词数，不是字节长度

## 状态

accepted（真机验证后确定；此前按字节长度实现，导致所有写通路被 MCU 拒绝）

## 1. 背景

**业务背景**：早期版本"能读不能写"——`CONFIG_SET_LIGHT_OUTPUT`、`CONFIG_SET_PICTURE`、`CONFIG_SET_AMBIENT_LIGHT` 等写命令一律返回 FAIL，导致集成只能做只读监控。

**技术背景**：pjctl 请求体为 `{"id": "...", "size": N, "params": "..."}`。`size` 是必填字段（缺失会 `invalid JSON data`），最初按直觉实现为 `len(params)`（字节/字符长度）。

**核心问题**：`size` 到底表示什么？为什么写命令被 MCU 拒绝而读命令大多正常？

**约束条件**：

- 无法读固件源码，只能从 固件静态分析（`WebServer.java`）+ 真机回环验证推断。
- 判据必须能解释"读宽容、写严格"的现象。

## 2. 决策驱动因素

1. **写通路可用性**（高）：不解决则集成的写能力为零。
2. **语义可证伪**（高）：必须能设计真机实验区分"字节长度"与"分词数"两种假说。
3. **一致性**（中）：所有命令（GET/SET/NOTIFY）必须共用同一 `size` 计算，不能每处手填。

## 3. 候选方案

| 方案 | 简述 | 优点 | 缺点 | 风险 |
|-|-|-|-|---|
| Option A：`size = len(params)`（字节/字符长度） | 直觉实现 | 简单 | 与固件期望不符：`"3 1 48"` 会得 6 而非 3 → MCU 判失败 | 写命令全部被拒（历史事实） |
| Option B：`size = len(params.split(" "))`（分词数） | 与固件 UI 的调用方式一致 | 与 UI 行为一致、可解释读写差异 | 需处理多空格/空 params 边界 | 低 |
| Option C：调用方手填 size | 灵活 | 无 | 98 个命令各填一次，极易错；无法统一验证 | 高 |

### Option A：字节长度（被推翻）

历史实现。真机现象：GET 大多可用（固件对读命令宽容），SET 全部 FAIL。

### Option B：分词数（选中）

`api.py:171` 单点计算：`"size": 0 if params is None else len(params.split(" "))`。注释给出三组对照样例证明该假说：`SET_LIGHT_OUTPUT size=1 params="100"`、`SET_REFRESH size=2 params="0 4"`、`SET_AMBIENT_LIGHT size=3 params="3 1 48"`（`api.py:163-170`）。修正后 `SET_PICTURE`/`SET_LIGHT_OUTPUT`/`SET_AMBIENT_LIGHT` 变值回环全部通过。

## 4. 决策结果

**选择方案**：**Option B**

**选择理由**：唯一能同时解释"读宽容/写严格"差异，且与固件 UI 的写入行为一致。A 已被真机证伪，C 无法统一维护。

### 4.1 实施要点

- 计算点唯一：`EpsonApi.request()`（`api.py:171`）。任何高层方法（`set_light_output`、`set_color_gain`…）都只传 `params` 字符串，**不得**自行拼 payload。
- 单测锁定：`tests/test_api.py:235-245` `test_set_light_output_size_is_token_count`；`tests/test_api.py:246-263` 覆盖氛围灯多组 params 的 size。
- 注释即契约：`api.py:160-171` 保留"曾经的错误假设 + 真机验证依据"，防止被重新猜回字节长度（见 `guidelines/principle-global-002-measured-firmware-facts.md`）。

## 备注

- 推论：写命令携带的 `params` **必须以单空格分隔**；拼接时避免多余空格（现有代码均用 `f"{a} {b}"` 形式）。
- 相关但独立的事实：`GET` 多数对 `size` 不敏感；`NOTIFY` 类命令的 params 同样遵循该规则（如 `NOTIFY_AUTO_CORRECTION "1 1"`，`button.py:61-64`）。
