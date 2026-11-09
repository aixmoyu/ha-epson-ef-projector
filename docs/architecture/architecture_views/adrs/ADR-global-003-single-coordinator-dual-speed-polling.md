---
project: "ha-epson-ef-projector"
type: adr
id: ADR-global-003
description: "全集成只保留一个 DataUpdateCoordinator 作为唯一轮询源，采用快/慢双速：每轮只做 2 次 HTTP 探电源态，每 12 轮才追加约 33 条只读诊断与设置回读，读失败以 carry-forward 结转旧值。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "单一轮询源 + 快/慢双速轮询节流"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/coordinator.py
  - custom_components/epson_ef/const.py
---

# ADR-003: 单一 Coordinator 的快/慢双速轮询

## 状态

accepted

## 1. 背景

**业务背景**：58 个实体需要持续反映设备状态（电源、灯时、20 项诊断、10 项可写设置的当前值），而用户对电源状态变化的感知延迟要求最高（默认 15s 内）。

**技术背景**：设备只有一个 HTTP 端点 `/api/pjctl`（8080），且固件 NanoHTTPD 疑似单线程：行为记录表明某些查询会挂住整个服务（固件行为记录；生产代码注释亦承认"some commands hang the HTTP thread"，`api.py:340-346`）。设备无推送能力（`manifest.json:10` `iot_class=local_polling`）。

**核心问题**：如何在"只读命令数量多（40 个 GET）"与"固件承受能力弱、且要求电源态刷新快"之间取平衡？

**约束条件**：

- 不能对同一设备并发打请求（会加剧单线程阻塞）。
- 电源态必须每轮都新（否则开关机体验差）。
- 大量诊断值变化很慢（灯时、版本号、硬件配置），无需高频。

## 2. 决策驱动因素

1. **电源态时效**（高）：HA 的 `media_player` 状态是核心体验。
2. **固件压力**（高）：单线程服务，一次卡死即全端口不可用（`risk.md` R-17）。
3. **实体一致性**（中）：58 个实体必须共享同一份数据，不能各自轮询。

## 3. 候选方案

| 方案 | 简述 | 优点 | 缺点 | 风险 |
|-|-|-|-|-|
| **Option A：单 coordinator + 快/慢双速** | 每轮 probe，每 12 轮诊断 | 请求量恒定、电源态及时、实现简单 | 慢值最大陈旧 12×interval；慢轮本身耗时可能超过 interval | 慢轮积压（`risk.md` R-09） |
| Option B：每实体各自 CoordinatorEntity 独立轮询 | 各取所需 | 无陈旧问题 | 请求数随实体数线性增长（58×），对单线程固件是灾难 | 极高 |
| Option C：单一 coordinator，每轮全量读 | 简单直接 | 无陈旧 | 每轮 ~35 次 HTTP，按 15s 间隔即 2.3 req/s 持续打固件 | 高 |
| Option D：事件驱动（设备推送） | 最理想 | 零轮询 | 固件不提供推送/回调通道 | 不可行 |

### Option A（选中）

`_poll_count` 计数；`_poll_count == 1 or _poll_count % DIAGNOSTIC_EVERY_N_POLLS == 0`（`coordinator.py:59`，`DIAGNOSTIC_EVERY_N_POLLS = 12`，`const.py:222`）时执行慢诊断段；每轮都执行 `probe_state()`（`coordinator.py:42-56`）。所有慢值用 carry-forward 跨周期存活（`coordinator.py:134-153`）。

### Option C（未选）

虽无陈旧问题，但每轮 35 次请求对单线程固件压力过大，且大部分诊断值（固件版本、序列号、硬件能力）根本不变化。

## 4. 决策结果

**选择方案**：**Option A**

**选择理由**：在"请求量 × 时效"的二维上取到了可行点——电源态 15s 级、诊断值 180s 级、稳态请求量 2 次/轮。B 的请求量不可接受，D 不可行，C 对固件的压力与收益不成比例。

### 4.1 实施要点

- 慢段仅在 `state == "on"` 时执行读命令（待机时读必然 FAIL，`coordinator.py:61`）——因此**待机期间 `settings`/`diag` 全部不刷新**，只能靠 carry-forward 保持。
- 读失败**不写 `None`**，而是跳过键，末尾统一结转旧值（`coordinator.py:134-153`，注释 `:128-129`）。
- `light_measured_at` 记录真实测量时间，配合 `stale` 属性暴露陈旧性（`sensor.py:75-83`）。
- 慢段内部逐条 `await` + `sleep(0.2)` 节流（`coordinator.py:84-101`），不并发。

## 备注

- **已知代价**：慢轮最坏 ≈33×10s ≈330s，远超默认 15s 间隔（`risk.md` R-09 给出总预算超时方案）。
- **已知不一致**：`standby_mode` 与 `focus_position` 只在慢轮更新（`coordinator.py:113-115`, `:130`），因此实体写后调 `async_request_refresh()` 在快轮里"刷新了但值不变"——这是预期行为而非 bug，已在 `process_view.md` §5.4 的一致性差异表中记录。
- 两次 `_async_update_data` 是否可能重叠属 HA 框架调度行为，本仓库无法验证（[待确认]）。
