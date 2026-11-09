---
project: "ha-epson-ef-projector"
type: risk
description: "verification-harness 的风险全部集中在『保障本身的可靠性』上：门禁范围不含 tools/ 与 HA 装配层，测试结构缺陷让直跑少跑 11 个用例，pyflakes 缺失时静默降级，真机脚本已有 1 个必崩的 NameError 且退出码不表达结论。全局级风险见 architecture_views/risk.md，本文件只记录本模块 code_mapping 覆盖的故障域与边界依赖。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
---

# 模块架构风险分析

> 本模块的故障域特殊：它不"宕机"，它**失效时表现为全绿**。因此下表的核心指标不是可用性，而是"保障覆盖率"与"失效可观测性"。

## 模块内稳定性与并发风险

| 风险 ID | 场景 | 触发条件 | 影响范围（本模块内） | 概率 | 严重度 | 优先级 | 当前缓解 | 建议缓解 | 状态 |
|-|-|-|-|-|-|-|-|-|-|
| VH-S01 | 直跑单文件导致用例静默漏跑 | `python3 tests/test_api.py` / `python3 tests/test_regressions.py` | `FocusAbsoluteTests`(3)、`ReadHelperTests`(4)、`PowerStateTests`(2)、`UnreachableProjectorTests`(2) 共 **11** 个用例不执行，且 rc=0 | 中（**没有任何文档推荐直跑**：`tests/test_api.py:3` 写的是 `python -m unittest discover -s tests -v`；但文件同时保留了 `if __name__ == "__main__"` 块，直跑是 IDE 与 `python tests/x.py` 的默认行为，且结果 rc=0 无任何提示） | 高 | P0 | 无。仅靠"记得用 discover/pytest" | 把 `unittest.main()` 移到文件末尾；或删除 `__main__` 块只留 discover | 未缓解 |
| VH-S02 | 伪包注入顺序依赖 | 同进程内多文件导入（discover/pytest） | `if "epson_ef" not in sys.modules` 使只有首个文件生效；后续文件复用同一伪包 | 低（当前 3 处注入内容一致） | 中 | P2 | 3 处注入逻辑逐字相同（`tests/test_api.py:23-26`、`test_const_contract.py:21-24`、`test_regressions.py:24-27`） | 提取 `tests/_bootstrap.py` 单一实现 | 已缓解（靠重复而非抽象） |
| VH-S03 | pyflakes 缺失导致静态检查静默消失 | 未装 pyflakes 的环境（CI 装了，本地不保证） | `tests/test_static.py` 唯一用例变 skipped，rc 仍 0 | 中 | 中 | P1 | 无。实测注入 ImportError 后 `skipped=1 / rc=0` | 改为 `self.fail("pyflakes missing")` 或直接依赖 ruff 的 F 规则 | 未缓解 |
| VH-S09 | 测试替身不支持并发 | 需要验证并发/乱序响应的场景 | `FakeSession` 是严格 FIFO 队列（`tests/test_api.py:69-77`），任何并发请求会错配响应 | 低（当前无并发用例） | 低 | P3 | 无 | 若要验并发，改用带 URL/载荷匹配的 Fake | 设计边界 |

## 跨模块依赖风险

| 风险 ID | 依赖方向 | 依赖对象 | 失效/漂移场景 | 影响 | 当前保障 | 改进方案 |
|-|-|-|-|-|-|-|
| VH-X01 | 本模块→下游 | `custom_components/epson_ef/api.py` | 方法改名/签名变化 | 工具脚本与 3/4 测试文件同时失效 | 测试会在收集阶段直接 ImportError（显式失败） | 可接受；工具脚本属一次性资产 |
| VH-X02 | 本模块→下游 | `const.py` 元组表 | 表名改名或从 `AnnAssign` 改成 `Assign` | 契约检查器抛 `AssertionError: <name> not found in const.py`（`tests/test_const_contract.py:52`） | 显式失败，可定位 | 让 `_table_arity` 也支持 `ast.Assign`（当前只认 `AnnAssign`） |
| VH-X03 | 本模块→下游 | `const.py` 表宽度 | 加/减列而消费者漏改 | 契约测试**成功拦截**（这是它存在的理由） | `test_every_consumer_unpacks_full_width`（`:111-125`） | 无需改进；这是本模块最可靠的保障 |
| VH-X04 | 本模块→下游 | **HA 装配层**（`__init__.py` + 7 平台 + `config_flow` + `services` + `diagnostics`） | 装配逻辑错误、迁移逻辑错误、平台注册错误 | **零覆盖**：CI 不装 homeassistant（实测 `import homeassistant` → rc=1），伪包又跳过 `__init__.py` | 无可执行保障；只有 3 个 AST 断言（清理表内容、实参顺序、coordinator 引用表名） | 引入 `pytest-homeassistant-custom-component` 做最小装配冒烟；或在 CI 装 homeassistant 后跑 import-smoke |





## 模块内性能瓶颈风险

| 瓶颈 ID | 位置（本模块内） | 当前指标 | 阈值 | 触发条件 | 影响 | 突破方案 | 预计工作量 |
|-|-|-|-|-|-|-|-|
| VH-P01 | 全量离线套件 | **0.177–0.20s**（实测 `Ran 64 tests in 0.177s` / `64 passed in 0.20s`） | 无实际压力 | — | 无 | 不适用 | — |
| VH-P02 | AST 契约扫描 | 4 张表 × 14 个生产文件，3 用例 0.085s（实测） | 文件数增长到数百时会线性变慢 | 生产包文件数增长 | 仍可接受 | 缓存 `ast.parse` 结果（当前每次调用都重新 parse，`_parse()` 无缓存，`tests/test_const_contract.py:35-36`） | 0.5 人日 |

> 注：`VH-P02` 的"无缓存"实测证据：`_parse(name)` 每次 `ast.parse((_PKG_DIR / TABLE_SOURCES[name]).read_text())`（`:36`），而 `test_tables_are_non_empty_and_rectangular` 对同一表调用两次 `_parse`（`:94` 的 `_table_arity` 与 `:95` 的 `ast.walk`）。

## 模块内数据一致性风险

| 风险 ID | 场景 | 一致性级别 | 当前保障 | 潜在问题 | 概率 | 影响 | 改进方案 |
|-|-|-|-|-|-|-|-|
| VH-D02 | `enum_map.json` 与生产代码枚举漂移 | 强一致但靠人工 | 无自动校验 | 生产代码用到的 46 个 CONFIG / 20 个 NOTIFY 当前全命中，但**没有任何测试保证这一点**；新增未映射命令不会失败 | 中 | 中 | 加一个 AST 测试：扫描代码里的 `"CONFIG_*"`/`"NOTIFY_*"` 字面量，断言都在 enum_map 中 |

**失败场景分析**

```
正常路径：真机脚本 → api.request → 设备返回 resultCode → 人工判读 → 改生产代码 → 加测试
异常路径 1（服务端无响应）：request 抛 EpsonApiUnavailable → 脚本 sleep 后继续 → 后续命令全部失败（无熔断）
异常路径 2（结论未固化）：脚本结论只留在终端输出 → 未写进注释与测试 → 下次需重跑
```

## 模块可用性风险

| 风险 ID | 故障域（本模块内） | 组件 | 当前可用性 | 目标可用性 | SPOF | 冗余策略 | 故障恢复策略 | RTO / RPO |
|-|-|-|-|-|-|-|-|-|
| VH-A01 | 离线验证 | `tests/` | 高（0.2s，无外部依赖除 aiohttp/pyflakes） | 高 | 否 | pytest 与 unittest 双通道 | 任一可用即可 | 秒级 / 无数据 |
| VH-A02 | 门禁 | `.github/workflows/tests.yml` | 高（唯一 workflow） | 高 | **是**（唯一 CI） | 无 | 本地手工跑同款两条命令 | 分钟级 / 无 |
| VH-A03 | 真机验证 | 真实投影仪 | 低（需设备在场 + 网络可达 + 服务未卡死） | 中 | **是**（唯一 HB71） | 无（无机型矩阵） | 断电重启 | 分钟级 / 无 |

## 模块内技术债务

| 区域（本模块） | 债务类型 | 原因 | 严重度 | 偿还建议 |
|-|-|-|-|-|
| `unittest.main()` 位置 | 测试债 | `tests/test_api.py:507-508`、`tests/test_regressions.py:183-184` 被放在文件中部的类之间；直跑少 11 个用例且 rc=0 | **严重** | 移到文件末尾，或统一删除 `__main__` 块 |
| HA 装配层零覆盖 | 测试债 | CI 不装 homeassistant（`.github/workflows/tests.yml:18`）+ 伪包跳过 `__init__.py`；14 个生产模块中 12 个 import HA | **严重** | 引入 `pytest-homeassistant-custom-component`；或至少 CI 装 HA 后做 `import custom_components.epson_ef` 冒烟 |
| `tools/` 无门禁 | 代码债 | CI lint 范围仅 `custom_components tests`（`:21`） | 轻微 | 把 `tools/` 纳入 lint 范围 |
| `pytest` 未声明 | 工程债 | 无 requirements/pyproject/tox；CI 只装 3 个包；README 却给出 `pytest tests/` 的跑法 | 轻微 | 加 `requirements-dev.txt`（aiohttp/pytest/ruff/pyflakes 并锁版本） |
| ruff 无配置无版本锁 | 工程债 | 无 `ruff.toml`/`pyproject.toml`；CI `pip install ruff` 浮动 | 轻微 | 加 `ruff.toml` 固定 `select`，CI 锁版本 |
