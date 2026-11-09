---
project: "ha-epson-ef-projector"
type: index
description: "本目录是从代码事实反向恢复的代码知识库（aet-project-analysis2 流水线产物）。全局层 5 视图 + 风险 + 6 原则 + 6 ADR + 全局功能树；模块层 5 个子系统 × 6 类产物。所有结论带 path/to/file:line 证据，frontmatter 均含 base_commit。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
---

# 项目知识库索引

> **基线**：`base_commit = 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3`（HEAD，含 v3.10.0 工作树快照）。
> **项目**：`ha-epson-ef-projector` — Home Assistant 自定义集成，通过 Epson EF/HB7x 投影仪固件自带的 pjctl HTTP API（8080，Digest 认证）做本地控制。生产代码 `custom_components/epson_ef/`（14 模块 / 3 055 行），58 个实体。
> **证据纪律**：每条结论附 `path/to/file:line`；无法从代码确定者标 `[待确认]`；仓库根 `README.md` 不作事实来源（Doc Distrust）。
> **目录约定**：`docs/` 下仅存本知识库。写入与更新规则见仓库根 [AGENTS.md](../../AGENTS.md) §3–§4。

## 从哪里开始读

| 你的问题 | 先读这个 |
|-|-|
| 这个系统整体怎么分层、模块怎么依赖？ | [logical_view.md](architecture_views/logical_view.md) |
| 我该怎么开始改代码？最短路径是什么？ | [development_view.md](architecture_views/development_view.md) §4 |
| 运行时协程怎么协作？一致性与并发如何保障？ | [process_view.md](architecture_views/process_view.md) |
| 部署在哪里？故障域与恢复手段？ | [deployment_view.md](architecture_views/deployment_view.md) |
| 典型业务旅程怎么端到端走通？ | [scenario_view.md](architecture_views/scenario_view.md) |
| 有哪些风险、按什么优先级修？ | [risk.md](architecture_views/risk.md) |
| 系统"能做什么"（业务能力清单）？ | [function_tree.yml](architecture_views/function_tree.yml) |
| 为什么当初这么设计？ | [adrs/](architecture_views/adrs/) |
| 改代码要守哪些约束？ | [guidelines/](architecture_views/guidelines/) |

## 全局层（`architecture_views/`）

### 4+1 视图

| 视图 | 文件 | 核心结论（一句话） |
|-|-|-|
| 逻辑 | [logical_view.md](architecture_views/logical_view.md) | 真实分层是 HA 集成六层（入口装配 → 动作入口 → 状态编排 → 传输协议 → 实体平台 → 契约常量），非 DDD 四层；依赖单向无环 |
| 开发 | [development_view.md](architecture_views/development_view.md) | 无构建步骤、无包管理清单、源码即产物；测试 64 例全离线（不 import HA），CI 仅 ruff(F,E9)+unittest |
| 进程 | [process_view.md](architecture_views/process_view.md) | 单轮询协程 + 实体回调 + 进程外固件；全仓仅 1 把锁（电源）；无消息总线；写后乐观 + 慢轮收敛 |
| 部署 | [deployment_view.md](architecture_views/deployment_view.md) | 进程内模块，1 entry : 1 设备 : 1 session；明文 HTTP；故障域=HA 重启/固件挂起/断网/DHCP 漂移；无自愈 |
| 场景（+1） | [scenario_view.md](architecture_views/scenario_view.md) | 6 个端到端场景串起四视图，含场景×四视图追溯矩阵与非功能需求达成表 |

### 风险、原则、决策、功能树

| 类别 | 数量 | 位置 | 要点 |
|-|-|-|-|
| 风险登记 | 29 条（R-01…R-28 + R-10b） | [risk.md](architecture_views/risk.md) | P0=写路径无并发保护、对焦可重入；P1=马达不停/增益丢更新/轮询积压/身份双源/测试漏 11 例/HA 零覆盖 |
| 全局原则 | 6 条 | [guidelines/](architecture_views/guidelines/) | 传输层零 HA 依赖、实测优先于推断、不可知状态须诚实暴露、表驱动+宽度契约、单一轮询源+结转、身份与地址解耦 |
| 全局 ADR | 6 条 | [adrs/](architecture_views/adrs/) | 选 pjctl 而非 ADB、size=分词数、单 coordinator 双速、乐观状态、盲 toggle 先探测、对焦闭环 |
| 全局功能树 | 162 节点（34 function） | [function_tree.yml](architecture_views/function_tree.yml) | 8 个业务能力域；每个 function 带 `module_refs` + `code_mapping` + `depends_on`；已过 R1–R7 校验 |

## 模块层（`subsystems/`）

5 个单层叶子域（Minimal Nested：全部一级，无嵌套子域）：

| # | 模块 ID | 范围（文件 / 行数） | 职责一句话 | 产物 |
|-|-|-|-|-|
| 1 | [`entry-core`](subsystems/entry-core/) | `__init__.py`/`config_flow.py`/`diagnostics.py`/`services.py`（494 行） | 装配与生命周期：setup/unload、身份解析与迁移、遗留实体清理、三条配置流、两个服务、诊断导出 | [function_tree](subsystems/entry-core/function_tree.yml)・[boundaries](subsystems/entry-core/boundaries.md)・[design](subsystems/entry-core/module_design.md)・[risk](subsystems/entry-core/risk.md)・[4 原则](subsystems/entry-core/guidelines/)・[4 ADR](subsystems/entry-core/adrs/) |
| 2 | [`pjctl-transport`](subsystems/pjctl-transport/) | `api.py`（656 行，全仓最大） | 协议适配：Digest 认证、命令收发、四类失败语义、34 个命令方法、对焦闭环算法 | [function_tree](subsystems/pjctl-transport/function_tree.yml)・[boundaries](subsystems/pjctl-transport/boundaries.md)・[design](subsystems/pjctl-transport/module_design.md)・[risk](subsystems/pjctl-transport/risk.md)・[3 原则](subsystems/pjctl-transport/guidelines/)・[4 ADR](subsystems/pjctl-transport/adrs/) |
| 3 | [`state-coordinator`](subsystems/state-coordinator/) | `coordinator.py` + `const.py`（376 行） | 状态同步：快/慢双速轮询、快照装配与结转、声明式契约表与值域 | [function_tree](subsystems/state-coordinator/function_tree.yml)・[boundaries](subsystems/state-coordinator/boundaries.md)・[design](subsystems/state-coordinator/module_design.md)・[risk](subsystems/state-coordinator/risk.md)・[4 原则](subsystems/state-coordinator/guidelines/)・[4 ADR](subsystems/state-coordinator/adrs/) |
| 4 | [`entity-platforms`](subsystems/entity-platforms/) | 7 个平台文件（1 529 行） | 58 实体的投影：电源/遥控/画面/光学/环境光/诊断，含乐观写与不可读状态建模 | [function_tree](subsystems/entity-platforms/function_tree.yml)・[boundaries](subsystems/entity-platforms/boundaries.md)・[design](subsystems/entity-platforms/module_design.md)・[risk](subsystems/entity-platforms/risk.md)・[5 原则](subsystems/entity-platforms/guidelines/)・[4 ADR](subsystems/entity-platforms/adrs/) |
| 5 | [`verification-harness`](subsystems/verification-harness/) | `tests/`（1 020 行）+ `tools/`（24 文件）+ CI | 验证设施：离线单测、AST 契约测试、真机探测脚本、CI 门禁 | [function_tree](subsystems/verification-harness/function_tree.yml)・[boundaries](subsystems/verification-harness/boundaries.md)・[design](subsystems/verification-harness/module_design.md)・[risk](subsystems/verification-harness/risk.md)・[4 原则](subsystems/verification-harness/guidelines/)・[4 ADR](subsystems/verification-harness/adrs/) |

模块划分依据：按真实**文件夹/文件边界**切分（Folder Priority），5 个模块职责单向依赖、可独立演进。能力域视角（按固件命令族切分的 7 个功能域）见 [logical_view.md](architecture_views/logical_view.md) §"模块边界与功能域映射"——两者互补，不重复。

## 关键事实速查

| 事实 | 值 | 证据 |
|-|-|-|
| 生产代码规模 | 14 模块 / 3 055 行 | `custom_components/epson_ef/*.py` |
| 实体总数 | 58（button 30 / switch 11 / number 7 / select 6 / sensor 2 / binary_sensor 1 / media_player 1） | `strings.json` entity 段 57 键 + media_player 无 translation_key |
| 协议端点 | `POST http://<ip>:8080/api/pjctl`，Digest MD5 | `const.py:14`、`api.py:79` |
| `size` 字段语义 | params 的**空格分词数**（非字节长度） | `api.py:160-171`、`ADR-global-002` |
| 轮询预算 | 快轮 2 条；慢轮 35 条（含 7 条重复，去重后 28）；每 12 轮一次 | `coordinator.py:59-132`、`const.py:222` |
| 并发保护 | 全仓仅 1 把 `asyncio.Lock`（电源路径） | `media_player.py:73` |
| 传输层零调用者方法 | 12 个 | 实测 AST+grep（如 `geometry_zoom` `api.py:590`） |
| 测试 | 64 用例全离线；直跑两文件会漏 11 例 | `tests/`、`tests/test_api.py:507` |
| CI 门禁 | ruff F,E9（不含 tools/）+ unittest（不装 homeassistant） | `.github/workflows/tests.yml:18-24` |

## 本次流水线执行记录

| 阶段 | 状态 | 说明 |
|-|-|-|
| A1 全局视图生成 | ✅ 完成 | 4 个 Subagent 并行探索落盘四视图（Gap Backfill 通过）→ 主 agent 汇总合成 scenario / risk / 6 原则 / 6 ADR |
| A2 全局产物评审 | ⏭️ **用户跳过** | 全局产物**未经独立对抗式验证** |
| A3 模块分割与详细探索 | ✅ 完成 | 用户选定「方案 A：5 模块单层」+「全部分析」→ 5 个 Subagent 并行落盘 6 类产物 → 全局 function_tree 合并（业务去重 M1–M4、不合并 N1–N2）→ 回写 5 个模块的 boundaries/function_tree |
| A4 模块产物评审 | ⏭️ **用户跳过** | 模块产物**未经独立对抗式验证** |

**收尾自检（主 agent 执行，非独立验证）**：
- 全部 79 个文件 frontmatter 含 `base_commit: 52928ed9…` ✅
- 6 份 function_tree 全部通过 `validate-function-tree.mjs --strict`（R1–R7）✅
- 全局树 34 个 `function` 节点均具备 `module_refs` + `code_mapping` + `depends_on` ✅
- 无模板占位残留（`[Project Name]` / `[To be filled]` / 示例 commit）✅
- 已交叉核实并修正 2 处计数偏差：模块数 15→14（`deployment_view.md`）、tools 脚本数 26→20 py+1 sh+2 json（`logical_view.md`）
- 已补入 1 条跨模块风险 R-10b（慢轮 7 条重复请求，实测核实：6 条来自 diag∩settings 同命令 + 1 条 product 重复读）

## 版本漂移声明（2026-11-09）

本知识库的 `base_commit` 是 `52928ed`。此后仓库做了一次裁剪，**知识库本身未重新生成**：

**已移除的文件**

- `tools/` 下 20 个一次性探测与验收脚本，现仅保留 `diagnose_state.py`、`verify_full.py`、`enum_map.json`
- `docs/reference/`（`protocol.md`、`coverage-matrix.md`）
- `docs/reports/`（`v3.10.0-release-test.md`）
- `docs/research/`（`firmware-audit.md`、`reverse-methodology.md`）
- `SECURITY.md`

**已清理的内容**

本知识库内**不再保留**与设备固件的交互过程描述——包括开发轮次编号、固件组件的
静态分析工具与文件名、厂商应用的内部类名等。此类内容已从生产代码、测试与工具的
注释中移除，本知识库同步对齐。

**因此存在 163 处失效引用**

分布在 26 个文件，指向上述已删文件（主要是 `tools/probe_*.py`、`docs/reference/*.md`、
`docs/reports/*.md`）。这些引用描述的是 `52928ed` 时点的事实，**现已不可复现**。

**处理方式**

按 AGENTS.md §3.1，知识库不靠人工逐条改写补全——那会产生无法追溯的"粉饰"，比留着
失效引用更糟。正确做法是等下一次代码变更时重跑 `aet-project-analysis2` 流水线，
用新的 `base_commit` 重新生成。在此之前引用本知识库中与 `tools/`、`docs/reference/`
相关的结论时，请先确认所述文件是否仍存在。

## 未验证项（明示风险）

按用户选择跳过 A2/A4 评审，故以下均**未经独立 context 核查**：

1. 各处 `path/to/file:line` 的行号精度（主 agent 抽样核对了 5 处关键引用与 3 处计数，未逐条核对约 600 处引用）。
2. 跨模块口径一致性（已发现并核对 1 处：慢轮 HTTP 数 33 vs 35 系口径差异——33 为追加条数、35 为含探针总数，非矛盾）。
3. 各模块 Subagent 自报的定量结论（如「40 个实体裸拼命令串」「16 处 DeviceInfo」）只做了抽样复核。
4. 各产物中的 `[待确认]` 条目（合计约 20 条）需维护者裁决或真机验证。

建议后续如需要更高置信度，按 `aet-project-analysis2` 的 A2/A4 SOP 补跑对抗式验证（独立 context、try-to-refute 立场），重点核：证据行号真实性、结论越界、跨文档一致性。
