---
project: "ha-epson-ef-projector"
type: adr
id: ADR-verification-harness-003
description: "测试引导逻辑（伪包注入 4 行）选择在 3 个测试文件中逐字复制，而不是抽成 tests/_bootstrap.py 共享模块。理由是保持『每个测试文件可单文件执行、可单文件拷走』与 discover 顺序无关性；代价是三处必须手工同步，且已有一处（test_const_contract.py）注入了伪包却根本不 import 任何生产模块的死代码。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "测试引导逻辑复制而非共享模块"
date: 2026-11-01
code_refs:
  - "tests/test_api.py:17-33"
  - "tests/test_const_contract.py:19-24"
  - "tests/test_regressions.py:22-35"
  - "tests/test_static.py:1-42"
---

# ADR-003: 测试引导逻辑复制而非共享模块

## 状态

accepted（与 ADR-001 同时确立；`tests/test_const_contract.py` 与 `tests/test_regressions.py` 从 `tests/test_api.py` 复制该手法）

## 1. 背景

**业务背景**：`tests/test_api.py` 最早确立了伪包注入手法（`tests/test_api.py:23-26`）。随后新增的两个文件需要同样的能力：

- `tests/test_const_contract.py`：需要 `_PKG_DIR` 定位源码目录（`ast.parse` 用），但**不 import 任何生产模块**。
- `tests/test_regressions.py`：需要 `from epson_ef import const` 与 `from epson_ef.api import ...`（`:29-35`）。

**技术背景**：

- 仓库**没有** `tests/__init__.py`、`conftest.py`、`pytest.ini`、`tox.ini`（`ls` + `find` 实测均不存在）。
- CI 用 `python -m unittest discover -s tests -v`；本地还可能用 `python3 tests/test_api.py` 直跑、`python3 -m unittest tests.test_api`、`pytest tests/`。
- 已实测：`python3 -m unittest tests.test_api` → `Ran 51 tests`（不做 `__main__` 判定，跑满）；`python3 tests/test_api.py` → `Ran 42 tests`（执行 `unittest.main()` 后停止）。
- discover 时第一件事是"把 `tests/` 目录加入 `sys.path` 顶层"（无 `__init__.py` 时的 `top_level_dir` 行为），因此测试文件之间可以互相 import——但这会让"共享模块"在直跑时失效。

**核心问题**：这 4 行引导代码应该复制 3 份，还是抽成一个被 import 的共享模块？

**约束条件**：

- 每个测试文件都必须能在 `python3 tests/xxx.py` 直跑时正常工作（否则维护者会困惑）。
- 不能引入 `tests/__init__.py`（会让 discover 的 `top_level_dir` 语义变化）。
- 不能依赖 pytest 的 `conftest.py`（CI 不用 pytest）。
- 三个文件的引导内容**不完全相同**（`test_api.py` 多一行 `sys.path.insert`；`test_const_contract.py` 不需要 import）。

## 2. 决策驱动因素（Decision Drivers）

1. **单文件可执行**（重要性：高）：任一测试文件拷贝到别处、或直接 `python3` 运行都能工作。
2. **零配置**（重要性：高）：不引入 `__init__.py`/`conftest.py`，不改变 discover 语义。
3. **显式性**（重要性：中）：读者打开文件就能看到"这个测试如何绕过 HA"，不需要跳到另一个文件。
4. **DRY**（重要性：低）：4 行重复的维护成本极低。
5. **同步风险**（重要性：低）：引导逻辑几乎不会变（`api.py` 的模块路径是稳定的）。

## 3. 候选方案（Considered Options）

| 方案 | 简述 | 优点 | 缺点 | 风险 |
|-|-|-|-|-|
| **Option A** 逐文件复制 4 行 | 每个测试文件自带 `types.ModuleType` 注入 | 单文件可跑；零配置；引导逻辑自解释 | 3 处重复；一处已产生死代码 | 低 |
| **Option B** `tests/_bootstrap.py` | 抽共享模块，测试里 `import _bootstrap` | DRY；改一处生效 | 直跑时 `tests/` 不在 `sys.path`（无 `__init__.py`），`import _bootstrap` 会失败；discover 与直跑行为分叉 | **中**：正是本项目最怕的"两种跑法不一致" |
| **Option C** 加 `tests/__init__.py` + 包内相对导入 | 把 tests 变成包 | 规范化 | 改变 discover 的 `top_level_dir`，会让 `python -m unittest discover -s tests` 的导入名从 `test_api` 变成 `tests.test_api`，可能影响现有调用方式 | 中：改动 CI 命令语义 |
| **Option D** `conftest.py` | pytest 提供引导 | 优雅 | CI 不用 pytest；`conftest.py` 对 `python3 tests/x.py` 无效 | 高：只覆盖一种跑法 |

### Option A：逐文件复制（现状）

三处实现：

| 文件 | 行 | 内容差异 |
|-|-|-|
| `tests/test_api.py` | `:17-33` | `_REPO_ROOT` + `_PKG_DIR` + `sys.path.insert(0, str(_REPO_ROOT))` + 伪包 + `from epson_ef.api import ...` |
| `tests/test_const_contract.py` | `:19-24` | 只有 `_PKG_DIR` + 伪包；**不 import 任何生产模块** |
| `tests/test_regressions.py` | `:22-35` | `_PKG_DIR` + 伪包 + `from epson_ef import const` + `from epson_ef.api import ...` |
| `tests/test_static.py` | 无 | 不需要（用绝对路径 glob + 子进程） |

**已观测到的副作用（真实存在）**：

`tests/test_const_contract.py:21-24` 注入了伪包，但该文件全程只用 `ast.parse` 读源码（`:36`、`:72`），**从不 import `epson_ef`**。实测：把它整段删除，3 个用例依然全绿。这是一段纯粹的死代码——它的存在只会让读者误以为"本文件也导入了生产模块"。

### Option B：`tests/_bootstrap.py`

**已实测（在 `/tmp/vhcheck` 的仓库副本上做，未改动真实仓库）**：把 `tests/test_regressions.py` 的引导段替换为 `import _bootstrap; _PKG_DIR = _bootstrap._PKG_DIR`，三种跑法结果：

| 跑法 | 结果 |
|-|-|
| `python3 tests/test_regressions.py`（直跑） | `Ran 7 tests ... OK`，rc=0 |
| `python3 -m unittest tests.test_regressions`（模块路径） | **`ModuleNotFoundError: No module named '_bootstrap'`**，`FAILED (errors=1)`，rc=1 |
| `python3 -m unittest discover -s tests` | `Ran 64 tests ... OK`，rc=0 |

失败原因：模块式调用时 `sys.path` 顶层是 cwd，而不是 `tests/`，因此 `import _bootstrap` 找不到；直跑时 `sys.path[0]` 恰是脚本目录才侥幸成功。而"模块式调用"正是本项目唯一能跑满 51/9 个用例的路径（`python3 tests/x.py` 因 `unittest.main()` 位置缺陷只跑 42/7，见 `tests/test_api.py:507-508`、`tests/test_regressions.py:183-184`）。

**结论：Option B 会让"能跑满用例的跑法"直接失败**——这不是理论风险，是实测的 `ImportError`。

若改用 `from . import _bootstrap`，则直跑（`python3 tests/test_regressions.py`）会因无父包而失败；若加 `tests/__init__.py` 则退化为 Option C 的问题。

### Option C：`tests/__init__.py`

会改变 `discover` 的模块命名（`test_api` → `tests.test_api`），而 CI 命令固定为 `python -m unittest discover -s tests -v`，改变行为需要同步验证；同时 `python3 tests/test_api.py` 直跑时 `tests` 包不在 `sys.path`，相对导入会失败。收益（DRY）远小于风险。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**（逐文件复制），并**清理死代码**

**选择理由**：

- 驱动因素 1（单文件可执行）：A 是唯一在四种跑法（直跑、`-m unittest tests.x`、discover、pytest）下都一致工作的方案；Option B 已实测在模块式调用下 `ImportError`。
- 驱动因素 2（零配置）：A 不需要任何新文件，不改变 discover 语义。
- 驱动因素 3（显式性）：读者在 `tests/test_api.py:21-22` 能直接读到注释 "Import epson_ef.api without executing the package __init__, which pulls in Home Assistant"。
- 驱动因素 4/5：4 行重复、几乎不变，DRY 收益不成立。

**已知损失（随决策记录）**：

1. 3 处实现必须手工保持同步（若未来 `api.py` 路径变化）。
2. `tests/test_const_contract.py:21-24` 的注入是死代码——**已实测**：在仓库副本上删掉该 4 行块与 `import types`，`python3 -m unittest tests.test_const_contract -v` 仍 `Ran 3 tests ... OK`（rc=0）。建议删除（不影响本决策，属实现瑕疵）。
3. `tests/test_api.py:19` 的 `sys.path.insert(0, str(_REPO_ROOT))` 对手法本身并非必需（**已实测**：在 `/tmp` 下不插 `sys.path`、只设 `__path__`，`from epson_ef.api import EpsonApi` 依然成功，`epson_ef.__file__` 为 `None`），属历史遗留。

### 4.1 实施要点

- 新测试文件必须自带完整引导（复制 `tests/test_regressions.py:22-35` 为模板，因为它同时覆盖 `const` 与 `api` 的导入）。
- **只在真正需要 import 生产模块时才注入伪包**；纯 AST 测试只需 `_PKG_DIR`（避免重复 `test_const_contract.py` 的死代码）。
- 保持 `if "epson_ef" not in sys.modules` 幂等保护。
- 不得添加 `tests/__init__.py`（会改变 discover 的模块命名）。

## 备注

- 本决策与 ADR-001（用伪包隔离代替 mock HA）是同一套手法的两个侧面：ADR-001 决定"用什么手法"，本 ADR 决定"这段手法怎么共享"。
- 若未来迁移到 pytest（参照 ADR-001 的 Option D 路径），则应改为 `conftest.py`——那时本决策应被 supersede。
- 死代码清理是零风险的独立改动：删除 `tests/test_const_contract.py:16`、`:21-24` 后跑 `python3 -m unittest tests.test_const_contract -v` 应仍为 `Ran 3 tests ... OK`。
