---
project: "ha-epson-ef-projector"
type: adr
id: ADR-state-coordinator-002
description: 采用单一 DataUpdateCoordinator 的快/慢双速轮询：每轮只 2 条 HTTP 探电源态，每 12 轮才追加 33 条只读命令（32 条在待机短路段内 + 1 条 standby_mode），稳态平均约 4.75 条/轮。代价是慢值最大陈旧 180s、慢轮峰值 35 条可能超出轮询间隔。
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "单一轮询源 + 快/慢双速节流"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/coordinator.py
  - custom_components/epson_ef/const.py
  - custom_components/epson_ef/__init__.py
---

# ADR-002: 单一轮询源 + 快/慢双速节流

## 状态

accepted

## 1. 背景

**业务背景**：58 个实体需要持续反映设备状态（电源、灯时、17 项诊断、10 项可写设置当前值），而用户对电源状态变化的感知延迟要求最高——`config_flow.py:63` 允许轮询间隔低至 5s，默认 15s（`const.py:11`）。

**技术背景**：设备只有一个 HTTP 端点 `/api/pjctl`（`API_PATH`，`const.py:14`），且固件是单线程 HTTP 服务——生产代码注释自承「some commands hang the HTTP thread」（`api.py:340-346`）。设备无推送能力（`manifest.json:10` 的 `"iot_class": "local_polling"`）。集成侧的唯一调度入口是 HA 的 `DataUpdateCoordinator`（`coordinator.py:19`, `:28-33`）。

**核心问题**：在「每轮都想知道电源态」与「固件承受不了 40 条只读命令 × 每 15s」之间，如何取一个可持续的平衡点？

**约束条件**：

- 不能对同一设备并发打请求（会加剧单线程阻塞）。
- 电源态必须每轮都新，否则开关机体验与盲 toggle 决策都会错（`media_player.py:178-199`）。
- 大量诊断值变化极慢（固件版本、硬件能力、出厂配置），无需高频。
- 待机时绝大多数引擎查询必然 FAIL（`api.py:51-53`）。

## 2. 决策驱动因素（Decision Drivers）

1. **电源态时效**（重要性：高）：`media_player` 的 `state` 是核心体验，且盲 toggle 依赖它判断当前态。
2. **固件压力**（重要性：高）：单线程服务，一次挂起即 8080 全端口不可用（`docs/architecture/architecture_views/risk.md` R-17）。
3. **实体一致性**（重要性：中）：58 个实体必须共享同一份数据，不能各自轮询。
4. **实现复杂度**（重要性：中）：弱固件已经让调试困难，调度逻辑不应再引入复杂度。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|**Option A：单协调器 + 快/慢双速**（选中）|每轮 probe（2 条），每 12 轮追加 33 条只读|请求量恒定 2/轮（稳态 4.75）；电源态及时；实体共享一份数据|慢值最大陈旧 12×interval；慢轮峰值 35 条可能超间隔|慢轮积压导致快轮饿死（R-SC-02）|
|Option B：每实体独立轮询|每个 `CoordinatorEntity` 各取所需|无陈旧问题|请求数 = 58 × 各自频率，对单线程固件是灾难|极高|
|Option C：单一协调器每轮全量读|每轮 35 条，不分快慢|无陈旧|15s 间隔即 2.3 req/s 持续打固件；且每次慢轮都可能撞上挂起|高|
|Option D：事件驱动（设备推送）|最理想，零轮询|零请求|固件无推送/回调通道，`manifest.json:10` 明确 `local_polling`|不可行|
|Option E：混合（快轮 probe + 独立慢协调器）|两个 coordinator 各自节奏|慢轮不阻塞快轮|引入第二个轮询源，两个 coordinator 会并发打同一设备（更糟）；且两份 `data` 需合并|高|

### Option A：单协调器 + 快/慢双速（选中）

节流判据（`coordinator.py:59`）：

```python
if self._poll_count == 1 or self._poll_count % DIAGNOSTIC_EVERY_N_POLLS == 0:
```

`DIAGNOSTIC_EVERY_N_POLLS = 12`（`const.py:222`）。`_poll_count` 在每轮开头自增（`:38`），`== 1` 的条件单列是为了让**首次轮询**立即拿到完整数据——1 不被 12 整除，若没有这个条件，集成启动后要等 12 轮（默认 180s）才有诊断值。

快活段（`:42-56`）无条件执行，慢活段（`:59-132`）受节流保护，末尾 carry-forward（`:134-153`）无条件执行。

**精确 HTTP 预算**（AST 核实，见 `module_design.md` 流程一）：

| 场景 | 条数 | 公式 |
|-|-|-|
| 快轮（唤醒） | 2 | `probe_state` = `PRODUCT_NAME` + `LIGHT_TIME` |
| 快轮（待机） | 2–3 | 唤醒探针 FAIL + `verify` 重试一次（`api.py:252-264`） |
| 慢轮（唤醒） | 35 | `2 + 17(diag) + 6(switch) + 4(select) + 6(post)` |
| 慢轮（待机） | 2–3 | `probe(2~3) + standby_mode(1)`，引擎段被 `:61` 整块跳过 |
| 稳态平均 | ≈4.75 | `2 + 33/12` |

### Option C：每轮全量读（未选）

每轮 35 条，按默认 15s 即 2.3 req/s。对单线程固件而言，这不仅是负载问题，更是**命中挂起面的概率**问题：每多打一条命令就多一次让服务卡死的机会（`api.py:340-346`）。而 35 条里有 33 条（扣除 probe 的 2 条）的价值随时间衰减极慢（固件版本、硬件配置），高频毫无收益。

### Option E：双协调器（未选，且应禁止）

看似能让慢轮不阻塞快轮，但两个 coordinator 是**独立调度**的，会在时间上重叠并发打同一设备——恰好违反约束「不能并发」。且两份 `data` 需要合并，`CoordinatorEntity` 只能挂一个 coordinator，引入第三层同步。`principle-global-005` 已明确禁止新增第二个轮询源。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：在「请求量 × 时效」二维上取到可行点——电源态 15s 级、诊断值 180s 级、稳态请求量 2 条/轮。B 的请求量不可接受；C 对固件的压力与收益不成比例；D 固件不支持；E 违反「不并发」约束且引入双轮询源。

**这是有条件的选择**：A 的代价（慢值陈旧、慢轮峰值）是显式承认的，不是被忽略的。

### 4.1 实施要点

- `_poll_count` 每轮自增（`:38`），进程重启归零 → 重启后首轮必为慢轮（`:59` 的 `== 1`）。这是**有意的**：保证启动后立刻有完整数据。
- 慢段内部逐条串行 `await`，成功读到值时追加 `sleep(0.2)`（`:88`, `:96`, `:101`），不并发。
- 慢段整体包在 `if state == "on":` 内（`:61`），待机时引擎查询全跳过——这是本设计的第二大优化，把「必然失败的 32 条」降为 0。
- 唯一例外是 `standby_mode`：位于 `:61` 块外、慢轮节流内（`:130`），因为启动模式是待机态唯一可读的设备配置。
- 慢轮的结果不直接覆盖快照，而是 `dict(old)` 打底 + `update(new)` 合并（`:117-119`, `:125-127`），使「本轮没读到的键」保留旧值。
- 快轮不产出 `settings`/`diag` → 由 carry-forward 从上一轮结转（`:145-153`）。

## 备注

- **已知代价 1：慢轮峰值可能超出轮询间隔**。35 条串行、每条最坏 10s（`api.py:160`）+ 27×0.2s 节流 ≈ 355s 最坏，远超默认 15s。HA 的 `DataUpdateCoordinator` 在上一轮未完成时会跳过调度，导致快轮被饿死。当前**无整轮超时预算**（R-SC-02）。
- **已知代价 2：慢值最大陈旧 = 12 × poll_interval**。默认 180s。且 `poll_interval` 可配到 300s（`config_flow.py:63`），此时陈旧上限达 3600s。
- **已知代价 3：待机期间 `settings`/`diag` 整段不刷新**（`:61` 跳过 `:62-123`），只能靠 carry-forward 保持。`settings` 的 10 个实体**没有 `stale` 标记**（只有 `sensor.py` 的两个传感器有），用户无法分辨「当前值」与「关机前的值」（R-SC-03）。
- **已知浪费**：表内 6 条命令被读两次且参数完全相同（诊断表 `coordinator.py:65-70` 与设置表 `const.py:54-65`），另有 `CONFIG_GET_PRODUCT_NAME` 在慢轮被读两次（probe 的活性检查 `api.py:237` + `:121`）。去重后慢轮可从 35 降到 28（P-SC-03）。
- **与全局 ADR 的关系**：`docs/architecture/architecture_views/adrs/ADR-global-003-single-coordinator-dual-speed-polling.md` 是本决策的全局视角版本，结论一致；本文件补充了精确的 HTTP 条数公式、待机短路的 AST 证据与重复命令清单。
