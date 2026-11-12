---
project: "ha-epson-ef-projector"
type: adr
id: ADR-state-coordinator-001
description: 用「表 + 通用实体类」承载 10 个可写设置，而不是为每个设置写一个实体类。表是纯数据和定宽元组，一处声明、四处解包（实体创建 ×2、轮询回读 ×2），代价是宽度成为跨文件契约，必须配 AST 级静态护栏。
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
status: accepted
title: "声明式元组表驱动可写设置，而非逐实体类"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/const.py
  - custom_components/epson_ef/switch.py
  - custom_components/epson_ef/select.py
  - custom_components/epson_ef/coordinator.py
  - tests/test_const_contract.py
---

# ADR-001: 声明式元组表驱动可写设置

## 状态

accepted

## 1. 背景

**业务背景**：集成要暴露 10 个可写设置（6 个 0/1 开关 + 4 个多档选择），另有 3 个氛围灯开关与 4 个光效模式、30 个按钮。这些设置共享完全同构的读写模式：读一个 `CONFIG_GET_*` 拿当前值，写一个 `CONFIG_SET_*` 设新值，值与值之间的差别只是命令号和档位。

**技术背景**：设置在两个时机被消费——**实体创建时**（要构造实体、算 `unique_id`、定 options）与**轮询回读时**（要拿当前值填 `settings` 字典）。这两处位于不同模块（`switch.py`/`select.py` vs `coordinator.py`），且必须对「有哪些设置」保持完全一致的认知。

**核心问题**：如何让「新增一个可写设置」的成本从「写一个类 + 改轮询 + 改翻译」降到「加一行数据」，同时不引入「两处认知不一致」的漂移风险？

**约束条件**：

- 新增设置是高频操作（仓库历史显示分 6 轮陆续确认出这些设置）。
- 每个设置的差异极小：命令号 + 档位列表 + 显示标签。
- 设备侧没有任何反射/元数据接口，命令号必须硬编码。
- 实体 `unique_id` 一旦发布不能改（会打断用户历史）。

## 2. 决策驱动因素（Decision Drivers）

1. **新增设置的成本**（重要性：高）：6 轮迭代中不断加设置，若每轮都要写类+改轮询，摩擦过大。
2. **两处认知一致性**（重要性：高）：`switch.py` 与 `coordinator.py` 必须对「有哪些开关」认知相同，否则实体存在但永不回读，或回读了却没有实体。
3. **类型安全**（重要性：中）：元组无字段名，靠位置记忆。
4. **失败可见性**（重要性：高）：契约破裂的后果是全集成不可用。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|**Option A：定宽元组表 + 通用实体类**（选中）|`const.py` 声明 `list[tuple[...]]`，`switch.py`/`select.py` 用通用类遍历建实体，`coordinator.py` 遍历回读|新增设置 = 加一行；两处消费同一数据源，认知天然一致|宽度靠位置记忆，漂移风险高；无常量名字段|契约破裂 → 全集成 unavailable（已发生一次）|
|Option B：逐实体类|每个设置一个 `class Ef50VmuteSwitch(...)`|类型安全、可读性最好、IDE 可跳转|10 个类 × ~60 行 ≈ 600 行重复代码；新增设置要改 4 处（类、setup、轮询、翻译）|两处认知漂移（轮询漏加）更隐蔽|
|Option C：`NamedTuple` / dataclass 表|表项改为 `SwitchSpec(suffix=..., key=..., get_cmd=..., set_cmd=...)`|字段名明确、宽度错误在构造时就报错、IDE 可补全|需要额外类型定义；`ast.literal_eval` 风格的静态测试无法直接读（需 import 或用 AST 解析 Call 节点）|低，但改动面大|
|Option D：外部 YAML/JSON 配置驱动|表放在 `data/*.json`，运行时加载|非程序员也能改|引入运行时加载失败面；HA 集成惯例是代码内常量；打包需含数据文件|文件缺失/格式错误 → 启动失败|

### Option A：定宽元组表 + 通用实体类（选中）

`const.py` 声明：`WRITABLE_SWITCHES: list[tuple[str,str,str,str]]`（6 行，`const.py:53-66`）与 `WRITABLE_SELECTS: list[tuple[str,str,str,str,list[str],dict[str,str]|None]]`（4 行，`const.py:75-92`）。

实体创建侧遍历表：`switch.py:34-40`、`select.py:36-42`。
轮询回读侧遍历表：`coordinator.py:92-96`、`:97-101`。

两侧解包的是**同一份数据**，因此不可能出现「实体存在但没回读」。这是 Option A 相对 Option B 的核心优势——Option B 里「加了类但忘了加进轮询」是一个真实的、静默的失误模式。

### Option B：逐实体类（未选）

可读性与类型安全更好，但 10 个类的读写逻辑完全同构（`switch.py:104-117` 的 `_write` 对每个开关都一样）。重复 10 遍只增加了维护面，不增加表达力。且 Option B 并未消除「轮询侧要同步」的问题，只是把它藏得更深。

### Option C：NamedTuple（未选，作为演进方向保留）

这是**当前设计的最佳改进方向**（见 risk.md「模块内技术债务」）：宽度错误会在构造时立即暴露，而不是等到慢轮。未立即采用的原因是它会让 `tests/test_const_contract.py` 的 AST 校验方式失效（`ast.literal_eval` 无法求值 `SwitchSpec(...)` 调用），需要重写护栏。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

- 对照驱动因素 1（新增成本）：A 明确胜出，新增一行 vs 写一个类。
- 对照驱动因素 2（两处一致性）：A 明确胜出——同一份数据被两处消费，物理上不可能不一致。
- 对照驱动因素 3（类型安全）：A 最弱，这是**已知且已付出代价的取舍**。
- 对照驱动因素 4（失败可见性）：A 最差（运行时才炸），因此**必须**用 Option A 的配套措施——AST 静态护栏——来补偿。

即：**选择 A 是有条件的**，条件是必须有 `tests/test_const_contract.py` 这类静态测试。没有护栏的 Option A 不可接受。

### 4.1 实施要点

- 表头必须写明列语义，格式固定为 `(唯一后缀, 翻译键, GET 命令, SET 命令)`（`const.py:49`, `:69`）。
- 第一列「唯一后缀」是三处主键：`settings` 键（`coordinator.py:95`）、`unique_id` 尾段（`switch.py:78`、`select.py:75`）、legacy 清理匹配（`__init__.py:146-150`）。
- 标签列允许为 `None`，语义是「档位语义未确认，直接暴露原始值」（`const.py:73-74`, `:78`）。
- 护栏：`tests/test_const_contract.py:111-125` 用 AST 求表宽度并比对**全部**解包点宽度（含推导式，`:64-88` 特意覆盖 `ast.comprehension` 因为 `select.py`/`switch.py` 用推导式）；`:127-142` 锁定解包点位置，防止有人把解包挪到测试看不到的地方。
- 该测试**不 import 任何 HA 依赖**（docstring `:8-9` 明说「在无 HA 的 CI 环境也能跑」），这是它能在 CI 常驻的前提。

## 备注

- **已知缺口**：`AMBIENT_MODE_OPTIONS`（`const.py:158-163`，4×2）**不在** `TABLE_SOURCES`（`tests/test_const_contract.py:27-32`）里。它被 `select.py:180` 的列表推导与 `:187` 的 `dict()` 消费，不产生元组解包 AST 节点，因此护栏看不到它。见 risk.md R-SC-06。
- **已知缺口**：`coordinator.py:63` 的 `probes` 表（17×3）也不在护栏内，且其类型标注写成了 4 元组（与实际 3 元组不符）。
- **历史代价**：3.10.0 发布前 `WRITABLE_SELECTS` 从 5 元组扩到 6 元组而 `coordinator.py` 未跟改，真机部署后每次慢轮抛 `ValueError`，全集成 unavailable（`tests/test_const_contract.py:1-9`）。这个事故正是护栏的由来，也是「Option A 必须有护栏」这一条件的实证。
