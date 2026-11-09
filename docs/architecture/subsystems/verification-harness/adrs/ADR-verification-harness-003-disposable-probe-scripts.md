---
project: "ha-epson-ef-projector"
type: adr
id: ADR-verification-harness-003
description: "真机探测脚本选择『每轮现场分析一个一次性脚本 + importlib 直载生产 api.py』的组织方式，明确不做共享 helper 库、不做 pytest 化、不接入 CI。收益是脚本可单文件复制粘贴、生命周期与现场分析轮次对齐；代价是 20 份 7 行样板重复、无退出码约定、已有 1 个必崩的 F821 未被门禁发现。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "一次性探测脚本而非共享工具库"
date: 2026-11-01
code_refs:
  - "tools/diagnose_state.py:11-22"
  - "tools/probe_commands.py:52-120"
  - "tools/probe_retest_drops.py:64-121"
  - "tools/probe_round5.py:30-56"
  - "tools/verify_round4.py:23-26"
  - "tools/README.md:1-55"
  - ".github/workflows/tests.yml:21"
---

# ADR-003: 一次性探测脚本而非共享工具库

## 状态

accepted（贯穿 2026-11-01 至 2026-11-07 的六轮现场分析，脚本时间戳从 `probe_round2.py` 00:40 到 `probe_round6b.py` 09:05）

## 1. 背景

**业务背景**：解出一台投影仪的私有 HTTP 协议（pjctl）需要大量"试探—观察—记录"循环。每一轮现场分析（round2…round6b）针对一组新命令，脚本的**生命周期只有那一轮**：跑完、得出结论、结论写进 `api.py`，脚本本身不再演进。

**技术背景**：

- 20 个 .py 脚本，2005 行，`git ls-files tools` 共 24 项（含 1 个 .sh、2 个 .json、1 个 README）。
- 全部 20/20 用同一段 7 行样板 `importlib` 直载生产 `api.py`（`awk` 实测每文件 7 行）。
- 无共享模块、无 `__init__.py`、无 requirements（`tools/` 目录下只有 `__pycache__`）。
- CI 的 lint 范围是 `custom_components tests`（`.github/workflows/tests.yml:21`），**不含 `tools/`**。
- 实测 `ruff check .` → 3 errors，rc=1，全部落在 `tools/`：
  - `tools/diagnose_state.py:5:8` F401 `json` 未使用
  - `tools/probe_phase23.py:5:8` F401 `json` 未使用
  - `tools/probe_retest_drops.py:79:15` F821 `wait_online` 未定义

**核心问题**：这 20 个脚本应该被工程化成"共享库 + 统一 CLI + 门禁覆盖"，还是保持"一次性、可复制、不入门禁"？

**约束条件**：

- 每个脚本都必须在**设备可能已被打挂**的状态下独立可用（不能依赖共享库的初始化顺序）。
- 脚本会改变设备状态（关机、改几何、动马达），不能进 CI。
- 维护者只有一人，现场分析节奏以"小时"计，不能花时间做抽象。
- 结论必须能被外部读者理解（`tools/README.md` 面向"想研究 pjctl 协议的用户"）。

## 2. 决策驱动因素（Decision Drivers）

1. **独立性**（重要性：高）：任一脚本必须能单独拷走运行，不依赖仓库内其他工具文件。
2. **现场分析速度**（重要性：高）：新脚本从"想到"到"能跑"的延迟要尽可能低。
3. **结论可追溯**（重要性：中）：脚本与 `api.py` 的对应关系必须清晰（探测的就是集成将要发的东西）。
4. **可维护性**（重要性：低）：脚本用完即弃，维护成本可以接受。
5. **门禁一致性**（重要性：低）：是否被 lint 覆盖不影响其价值判断。

## 3. 候选方案（Considered Options）

| 方案 | 简述 | 优点 | 缺点 | 风险 |
|-|-|-|-|-|
| **Option A** 一次性脚本 + importlib 直载 | 每轮一个新文件，7 行样板复制，无共享代码 | 单文件可拷走；零抽象成本；与现场分析轮次一一对应 | 20 份样板重复；无退出码约定；F821 已存在 | 中：脚本腐化，如 `wait_online` |
| **Option B** 共享 helper 库 + 统一 CLI | `tools/_bootstrap.py` + `tools/cli.py`，脚本只写探测逻辑 | 消除重复；可统一重试/超时/退出码 | 破坏"单文件可拷走"；新增脚本需理解库；布线成本 | 中：抽象层本身成为新的失效点 |
| **Option C** pytest 化（把探测写成参数化测试） | 用 pytest + `--device` 标记跑真机 | 可复用断言与报告 | pytest 未声明为依赖；真机测试不能进 CI；仍需设备 | 高：与现有 unittest+CI 结构冲突 |
| **Option D** 脚本纳入 CI lint | 把 `:21` 改成 `ruff check .` | 3 个已知错误立刻被拦住 | 立即变红，需先还债（2 个 F401 + 1 个 F821） | 低：成本明确可控 |

### Option A：一次性脚本（现状）

每轮现场分析产生新文件，样板逐字复制：

```python
ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "epson_ef"
pkg = types.ModuleType("epson_ef"); pkg.__path__ = [str(ROOT)]; sys.modules["epson_ef"] = pkg
spec = importlib.util.spec_from_file_location("epson_ef.api", str(ROOT / "api.py"))
mod = importlib.util.module_from_spec(spec); sys.modules["epson_ef.api"] = mod
spec.loader.exec_module(mod)
HOST = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EPSON_HOST", "")
if not HOST: sys.exit("用法: ...")
```

20/20 脚本命中（`tools/diagnose_state.py:11-22`、`tools/probe_retest_drops.py` 等）。

**实证的腐化**：

- `tools/probe_retest_drops.py` 调 `await wait_online(120)`，但该文件只定义了 `tcp_online`/`wait_server_ready`/`safe_probe`/`main`。`wait_online` 的真实定义在 `probe_matrix.py`（三参数 `host, port, max_s`）与 `verify_full.py:41`（单参数 `max_s`）——**两个签名不同**，抄错了。实测：`python3 tools/probe_retest_drops.py 127.0.0.1` → `NameError: name 'wait_online' is not defined. Did you mean: 'tcp_online'?`，rc=1。
- 4 个脚本硬编码默认 IP `192.168.61.7`（`probe_focus.py:28`、`probe_power_state.py:20`、`probe_refresh.py:28`、`verify_round4.py:23`），与 `tools/README.md:8` 声称的"不提供默认值"矛盾。
- 4 个 round 脚本用 `else ""`（`probe_round3.py`、`probe_round5.py`、`probe_round6.py`、`probe_round6b.py`），既不读 env 也不校验，无参运行构造 `http://:8080/api/pjctl`（实测 `EpsonApiUnavailable: http://:8080/api/pjctl` + `Unclosed client session`，rc=1）。
- 3 个脚本从不 `api.close()`（`probe_commands.py`、`probe_notify_sweep.py`、`probe_remaining.py`）。

### Option B：共享 helper

可消除 7 行 × 20 的重复，统一 HOST 解析与退出码。但代价是：`tools/` 从"一堆可独立拷走的脚本"变成"一个小型应用"。对单人现场分析项目而言，抽象的投资回报为负——每个脚本平均只跑一次。

### Option D：纳入 CI lint

技术上最优、成本最低的改进（3 个错误都是机械可修的）。但**未被采纳**，原因记录如下：门禁范围是一个显式决策（见 ADR-004 与 `principle-verification-harness-004`），当前刻意把 `tools/` 排除在外，因为脚本会快速迭代、且其正确性只能靠真机验证而非静态检查。**这是一个已知缺口，不是疏忽**——但它确实导致 `wait_online` 这个必崩错误长期存在。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**（保持一次性脚本），并**明确记录**其代价

**选择理由**：

- 驱动因素 1（独立性）：A 是唯一保证"任一脚本可单独拷走"的方案。B 会引入仓库内耦合。
- 驱动因素 2（速度）：A 的"新脚本成本"= 复制 7 行；B 需要先理解库 API。
- 驱动因素 3（可追溯）：A 通过"importlib 直载生产 `api.py`"保证脚本探测的字节与集成发送的字节完全一致——这一点 B 也能做到，但 A 做在同一屏代码里，更直观。
- 驱动因素 4（可维护性）：明确接受"脚本是一次性资产"。`tools/README.md:26-48` 已给出 20 个脚本的用途表，说明维护方式是"文档化"而非"抽象化"。

**已知损失（随决策记录）**：

1. 20 份 7 行样板重复，任何引导逻辑变更要改 20 处（实际从未改过）。
2. 无统一的重试/超时/退出码约定：超时 4/6/8/10s 并存，重试 2/3/4 次并存，逻辑失败一律 rc=0（`verify_round4.py:79` 只 `print("RESULT: ...")`）。
3. `tools/` 无门禁 → 已存在 1 个必崩错误与 2 个未使用导入。
4. 结论存档 `probe_results.json` 的 verdict 词汇表由各脚本自由定义（`DROP(...)` / `UNREACHABLE` / `HANG(...)`），消费者 `probe_retest_drops.py` 只认 `DROP` 前缀，而现存 36 条中 DROP 为 0 条。

### 4.1 实施要点

- 新脚本必须复制完整 7 行样板（不要试图 `from tools._bootstrap import ...`）。
- HOST 解析必须走 `sys.argv[1]` 优先、`EPSON_HOST` 次之、**无默认值**（`tools/README.md:8` 的承诺；现存 4 个违规脚本不应被模仿）。
- 必须 `await api.close()`（现存 3 个脚本违反）。
- 危险命令必须在文件头写禁止说明（范例：`tools/probe_refresh.py:9-11` 绝不发送 `SET_REFRESH "1"`）。
- 探测结论必须写进 `probe_results.json` 或脚本头的 docstring，不能只留在终端输出里。

## 备注

- 若未来要偿还该债务，最小改动是：修 `wait_online`（改调已存在的 `wait_server_ready(120)`）、`ruff --fix` 清 2 个 F401、把 `:21` 扩为 `ruff check .`。预计 30 分钟。
- 该决策与 `principle-verification-harness-003`（真机结论三步固化）互补：脚本是一次性的，但**结论**必须固化进 `api.py` + `tests/`。
- `tools/README.md` 本身也属"文档"，已发现与代码冲突两处（默认 IP、ADB 依赖范围），以代码为准（Doc Distrust）。
