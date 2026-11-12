---
project: "ha-epson-ef-projector"
type: adr
id: ADR-entry-core-003
description: "配置交互拆成三条职责单一的流：user（新增，写 data + unique_id）、reconfigure（只改连接信息）、options（只改轮询间隔）。地址变更必须走 reconfigure 而非重新添加，因为只有 reconfigure 能保留条目与实体历史。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
status: accepted
title: "三流分治：新增 / 重配置 / 选项各管一件事"
date: 2026-11-01
code_refs:
  - "custom_components/epson_ef/config_flow.py:23-105"
  - "custom_components/epson_ef/config_flow.py:108-131"
  - "custom_components/epson_ef/__init__.py:184-187"
  - "custom_components/epson_ef/__init__.py:209-210"
  - "custom_components/epson_ef/strings.json:2-38"
---

# ADR-003: 三流分治：新增 / 重配置 / 选项各管一件事

## 状态

accepted（`VERSION = 2`，`reconfigure` 依赖 HA 2024.4+，`hacs.json:3` 声明该下限）

## 1. 背景

**业务背景**：用户对一条集成配置只有三种诉求——首次添加设备、设备 IP 变了改地址、跑得太勤/太懒改轮询间隔。三者的影响面完全不同：新增会创建 config entry 与全部实体；改地址只应更新连接参数；改间隔只应改协调器节奏。

**技术背景**：HA 提供两套流机制：主 flow（user/reconfigure 等 step）与 options flow。主 flow 的 `reconfigure` step 通过 `async_update_and_abort(entry, data_updates=...)` 原地更新条目（`config_flow.py:87-89`）；options flow 通过 `async_create_entry` 写 `entry.options`（`:113`）。本模块另有 `entry.add_update_listener(_async_update_listener)`，监听器无条件 `async_reload`（`__init__.py:205`、`:209-210`）。

**核心问题**：在"改地址"与"改间隔"都不能重建条目的前提下，如何划分流程职责，使每次修改只触发一次 reload、且不改变实体身份？

**约束条件**：

- 改地址后实体与历史必须保留（`README.md:87`、`:156`）。
- reload 只能发生一次，不能"流内部 reload + 监听器再 reload"双触发（`config_flow.py:85-86` 注释明确此点）。
- 兼容 HA 2024.4+（`hacs.json:3`），`reconfigure` 是 2024.4 引入的能力。

## 2. 决策驱动因素（Decision Drivers）

1. **条目与实体历史不得因改地址而丢失**（重要性：高）。
2. **避免重复 reload**（重要性：高）：两次 reload 会让实体闪烁两次，且第二次可能是无谓的网络往返。
3. **能力边界清晰**（重要性：中）：用户不应在"选项"里改到连接参数，也不应在"重配置"里改到轮询节奏。
4. **实现量与 HA 版本兼容**（重要性：中）：不愿自研 flow 骨架，只用 HA 原生 step。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|---|---|---|---|---|
|Option A|三流分治：user 新增 / reconfigure 改地址 / options 改间隔|职责单一；改地址保留历史；改间隔不触碰连接信息；每个流只触发一次 reload|三处入口对用户有认知成本；reconfigure 与 options 都要求 HA 较新版本|低|
|Option B|单一 options 流容纳所有可改项（含 host/port）|入口少|改地址走 options 时，`entry.unique_id`（=`host:port`）与实体身份不会更新；用户若同时改地址与间隔，语义混杂；无法表达"地址变化"这一事件|中：身位语义混乱|
|Option C|改地址 = 删除后重新添加|实现零成本|实体全部重建，历史断档，与 README 承诺冲突；且新条目会与原条目共存产生重复|高：已实际造成问题（全局 R-16）|
|Option D|reconfigure 同时改地址与轮询间隔（合并 B 的能力）|入口更少|违反"轮询间隔存 options、连接信息存 data"的存储分层；改地址会顺带改节奏|低但语义不佳|

### Option A：三流分治（已采纳）

- `async_step_user`：四项输入 + 连通性探测 + `set_unique_id(f"{host}:{port}")` + `abort_if_unique_id_configured`（`config_flow.py:35-66`）。
- `async_step_reconfigure`：`_get_reconfigure_entry()` 取回条目，只渲染 host/port，成功后 `async_update_and_abort(entry, data_updates=user_input)` 并**不自行 reload**（`:68-105`）。
- `Ef50OptionsFlow.async_step_init`：只渲染 poll_interval，`async_create_entry(title="", data=user_input)` 写 options（`:108-131`）。

### Option B：单一 options 流

把 host/port 也放进 options 会让"连接参数"从 `entry.data` 漂到 `entry.options`，而 setup 目前只从 `entry.data` 读 host/port（`__init__.py:168-169`），从 `entry.options` 只读 poll_interval（`:184`）。要支持就得在 setup 里做 options 覆盖 data 的读取——可以，但会让"新增时填的 host"与"后来改的 host"分居两个容器，诊断导出（`diagnostics.py:20-24` 分别导出 data 与 options）也会变得难以解读。更重要的是它无法表达"地址变了"这一需要迁移/去重语义的事件。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

- 驱动 1：只有 reconfigure 能在不重建条目的前提下改连接信息，实体 `unique_id` 由 `base_id` 决定（`__init__.py:178-181`），故历史保留。
- 驱动 2：`async_update_and_abort` 之后不手动调 reload，由 update listener 统一触发一次（`config_flow.py:85-89` 注释 + `__init__.py:209-210`）。
- 驱动 3：三个 schema 各只声明自己那一组字段（`:56-65`、`:94-103`、`:117-131`），越权字段根本无法提交。
- 驱动 4：全部用 HA 原生 `ConfigFlow` / `OptionsFlow` 骨架，无自研调度。

**被否决方案的关键否决点**：B 否决于存储分层与事件语义；C 否决于历史断档（与 ADR-001 的目标直接冲突）；D 否决于"改地址顺带改节奏"的耦合。

### 4.1 实施要点

1. 三条流都以 `get_product_name()` 作唯一连通性判据，失败统一 `errors["base"] = "cannot_connect"`（`:40-54`、`:77-90`）。
2. reconfigure 成功后**必须**用 `async_update_and_abort`，禁止 `async_reload` 自调用（否则与监听器重复）。
3. options 的默认值必须回显"生效值"：`options.get(...)` → `data.get(...)` → `DEFAULT_POLL_INTERVAL`（`:119-128`），与 setup 的三级回退逐级一致（`__init__.py:184-187`）。
4. 轮询间隔在三条流中都是 `vol.All(vol.Coerce(int), vol.Range(min=5, max=300))`（`:61-63`、`:129`）。
5. 新增条目时 `unique_id = f"{host}:{port}"`（`:47-49`），这是条目级去重键（与实体身份 `base_id` 不同源，属 ADR-001 的未完成部分）。

### 4.2 已知局限

- **reconfigure 不改 `unique_id`**：条目身份仍是旧地址，两个条目可被改到同一地址而互不感知；也无法阻止用户"改 IP 后重新添加"造出重复条目（全局 R-16）。
- **host 无格式校验**：schema 只要求 `str`（`:58`），空串也能提交；真实防护完全依赖探测失败。
- **无自动发现/重新认证**：不存在 `async_step_zeroconf` / `dhcp` / `import` / `reauth`（grep 无命中），IP 必须手工填写。
- **options 回显逻辑与 setup 取值逻辑是两处独立实现**（`config_flow.py:119-128` vs `__init__.py:184-187`），改一处必须同步另一处。

## 备注

- 三流的中文文案见 `strings.json:2-38`。
- 配置项语义与取值范围见 `module_design.md` 的「配置接口」表。
