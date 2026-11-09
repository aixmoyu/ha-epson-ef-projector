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
| VH-S04 | 真机脚本一跑即崩 | `tools/probe_retest_drops.py` 且 TCP 不通 | 整个脚本不可用；`NameError: name 'wait_online' is not defined`（实测 rc=1） | **必然**（只要 TCP 不通，`:77-79` 分支必走） | 中 | P1 | 无。ruff `F821` 已标出，但 CI 范围不含 tools/ | 修 `wait_online(120)` → `wait_server_ready(120)` 或补定义 | 未缓解 |
| VH-S05 | 探测结论词汇表分裂 | 多脚本写同一 `probe_results.json` | 消费者 `probe_retest_drops.py` 只认 `startswith("DROP")`，而生产者分别写 `OK*/FAIL/UNREACHABLE/HANG/ERR` | 已发生（现存 36 条中 DROP=0） | 中 | P1 | 无 schema、无枚举约束 | 定义 verdict 常量表并让所有脚本共用 | 已发生 |
| VH-S06 | 多脚本并发写存档互相覆盖 | 同时跑两个探测脚本 | `probe_results.json` 整体读取 → 修改 → 整体写回，非原子 | 低（实践中单人串行） | 低 | P3 | 依赖人工串行 | 写入用临时文件 + `os.replace` | 未缓解 |
| VH-S07 | 探测把固件服务器卡死 | 发未文档化命令组合 | NanoHTTPD 单线程挂起，8080 全不可用；只能重启设备或 ADB 解卡 | 中（README 明确警告，`tools/README.md:19-20`） | 高 | P0 | 脚本内健康探针（`probe_commands.py` `healthy()`）+ 破坏性升序 | 加全局熔断：连续 N 次 HANG 即整体中止 | 部分缓解 |
| VH-S08 | `standby_watch.sh` 跨平台参数不兼容 | 在 GNU netcat / 非 macOS 环境运行 | `nc -z -G 2`、`ping -W1`、`nc -u -w 1 -b` 语义不一致 → 采样值错误 | 中 | 低 | P3 | 无平台探测 | 启动时 `nc -h` 探测能力集 | 未缓解 |
| VH-S09 | 测试替身不支持并发 | 需要验证并发/乱序响应的场景 | `FakeSession` 是严格 FIFO 队列（`tests/test_api.py:69-77`），任何并发请求会错配响应 | 低（当前无并发用例） | 低 | P3 | 无 | 若要验并发，改用带 URL/载荷匹配的 Fake | 设计边界 |

## 跨模块依赖风险

| 风险 ID | 依赖方向 | 依赖对象 | 失效/漂移场景 | 影响 | 当前保障 | 改进方案 |
|-|-|-|-|-|-|-|
| VH-X01 | 本模块→下游 | `custom_components/epson_ef/api.py` | 方法改名/签名变化 | 20/20 工具脚本 + 3/4 测试文件同时失效 | 测试会在收集阶段直接 ImportError（显式失败） | 可接受；工具脚本属一次性资产 |
| VH-X02 | 本模块→下游 | `const.py` 元组表 | 表名改名或从 `AnnAssign` 改成 `Assign` | 契约检查器抛 `AssertionError: <name> not found in const.py`（`tests/test_const_contract.py:52`） | 显式失败，可定位 | 让 `_table_arity` 也支持 `ast.Assign`（当前只认 `AnnAssign`） |
| VH-X03 | 本模块→下游 | `const.py` 表宽度 | 加/减列而消费者漏改 | 契约测试**成功拦截**（这是它存在的理由） | `test_every_consumer_unpacks_full_width`（`:111-125`） | 无需改进；这是本模块最可靠的保障 |
| VH-X04 | 本模块→下游 | **HA 装配层**（`__init__.py` + 7 平台 + `config_flow` + `services` + `diagnostics`） | 装配逻辑错误、迁移逻辑错误、平台注册错误 | **零覆盖**：CI 不装 homeassistant（实测 `import homeassistant` → rc=1），伪包又跳过 `__init__.py` | 无可执行保障；只有 3 个 AST 断言（清理表内容、实参顺序、coordinator 引用表名） | 引入 `pytest-homeassistant-custom-component` 做最小装配冒烟；或在 CI 装 homeassistant 后跑 import-smoke |
| VH-X05 | 本模块→下游 | `select.py` / `switch.py` 的推导式解包形态 | 有人把推导式改成显式循环（或反过来） | 解包点位置的 `expected` 集合（`:129-136`）只校验文件名，不校验行号，因此**形式变化不会失败** | 位置锚定只到文件级 | 把 `expected` 升级为 (文件, 行号) 并接受白名单更新 |
| VH-X06 | 本模块→下游 | `button.py` 的 `BUTTON_DEFS` | 表从 `tuple` 改成 `list` | `_table_arity` 同时接受 `List`/`Tuple`（`:46`），不会失败 | 无风险 | — |
| VH-X07 | 上游→本模块 | 维护者手工执行 | 忘记跑测试就连真机 | 未验证的代码直接上设备 | CI 在 push/PR 兜底，但**不阻断**设备部署（无 CD、无 tag 流程） | 发布前 checklist（`docs/reports/v3.10.0-release-test.md` 已有实践） |
| VH-X08 | 本模块→下游 | 真实投影仪 / ADB | 设备不在、ADB 未开、换机型 | 20 个脚本全部无法产出结论；HB71 特定值（对焦 0–264、增益 81/90/100）被写进测试成为"事实" | 无 | 把机型相关值集中标注（现有注释已标 "HB71 实测"），新机型需重跑 `probe_*` |

## 模块内性能瓶颈风险

| 瓶颈 ID | 位置（本模块内） | 当前指标 | 阈值 | 触发条件 | 影响 | 突破方案 | 预计工作量 |
|-|-|-|-|-|-|-|-|
| VH-P01 | 全量离线套件 | **0.177–0.20s**（实测 `Ran 64 tests in 0.177s` / `64 passed in 0.20s`） | 无实际压力 | — | 无 | 不适用 | — |
| VH-P02 | AST 契约扫描 | 4 张表 × 14 个生产文件，3 用例 0.085s（实测） | 文件数增长到数百时会线性变慢 | 生产包文件数增长 | 仍可接受 | 缓存 `ast.parse` 结果（当前每次调用都重新 parse，`_parse()` 无缓存，`tests/test_const_contract.py:35-36`） | 0.5 人日 |
| VH-P03 | 真机探测脚本 | 每命令 1.5–8s，36 命令 × 4 参数变体 ≈ 数分钟；含 sleep 8/12/15s 的等待 | 固件卡死则永久挂起（无总超时） | 服务器被打挂 | 需要人工中断 + 重启设备 | 加脚本级总超时（`asyncio.wait_for(main(), total)`） | 0.5 人日 |

> 注：`VH-P02` 的"无缓存"实测证据：`_parse(name)` 每次 `ast.parse((_PKG_DIR / TABLE_SOURCES[name]).read_text())`（`:36`），而 `test_tables_are_non_empty_and_rectangular` 对同一表调用两次 `_parse`（`:94` 的 `_table_arity` 与 `:95` 的 `ast.walk`）。

## 模块内数据一致性风险

| 风险 ID | 场景 | 一致性级别 | 当前保障 | 潜在问题 | 概率 | 影响 | 改进方案 |
|-|-|-|-|-|-|-|-|
| VH-D01 | `probe_results.json` 与真实固件行为漂移 | 最终一致（人工） | 无 | 存档是某个固件版本的快照，换机型/升级固件后仍被当参考 | 高 | 中 | 每条目加 `firmware`/`date` 元数据 |
| VH-D02 | `enum_map.json` 与生产代码枚举漂移 | 强一致但靠人工 | 无自动校验 | 生产代码用到的 46 个 CONFIG / 20 个 NOTIFY 当前全命中，但**没有任何测试保证这一点**；新增未映射命令不会失败 | 中 | 中 | 加一个 AST 测试：扫描代码里的 `"CONFIG_*"`/`"NOTIFY_*"` 字面量，断言都在 enum_map 中 |
| VH-D03 | 存档 verdict 与消费者判定不一致 | 弱 | 无 | 见 VH-S05；当前 DROP=0 使 retest 脚本静默空转 | 已发生 | 中 | 见 VH-S05 |

**失败场景分析模板**

```
正常路径：probe 脚本 → api.request → 固件返回 resultCode → 映射 OK/FAIL → 写 probe_results.json → 人工读 → 改生产代码 → 加测试
异常路径 1（服务端崩溃）：request 抛 EpsonApiUnavailable → 映射 DROP/HANG → 脚本 sleep 后继续下一个命令 → 服务器持续不可用 → 后续命令全部 ERR（无熔断）
异常路径 2（词汇漂移）：probe_matrix 写 UNREACHABLE → post_retest 只找 DROP → 选出 0 个命令 → 打印"重跑 0 个 DROP 命令" → 退出码 0 → 人工误以为"没有待复测项"
异常路径 3（结论丢失）：脚本中途 Ctrl-C → 已写 json 的条目保留（每命令一次 json.dump），未写的丢失 → 重跑时 `_done` 保证幂等
```

## 模块可用性风险

| 风险 ID | 故障域（本模块内） | 组件 | 当前可用性 | 目标可用性 | SPOF | 冗余策略 | 故障恢复策略 | RTO / RPO |
|-|-|-|-|-|-|-|-|-|
| VH-A01 | 离线验证 | `tests/` | 高（0.2s，无外部依赖除 aiohttp/pyflakes） | 高 | 否 | pytest 与 unittest 双通道 | 任一可用即可 | 秒级 / 无数据 |
| VH-A02 | 门禁 | `.github/workflows/tests.yml` | 高（唯一 workflow） | 高 | **是**（唯一 CI） | 无 | 本地手工跑同款两条命令 | 分钟级 / 无 |
| VH-A03 | 真机验证 | 真实投影仪 | 低（需设备在场 + 网络可达 + 服务未卡死） | 中 | **是**（唯一 HB71） | 无（无机型矩阵） | 断电重启 / ADB 解卡 | 分钟级 / 无 |
| VH-A04 | 真机验证 | ADB :5555 | 低（默认关闭，需手动开启网络调试） | 低（按需） | 是（解卡唯一途径） | 无 | 物理重启 | 分钟级 / 无 |
| VH-A05 | 结论存档 | `tools/probe_results.json` | 高（普通文件） | 高 | 否（可重跑重建） | git 历史 | `git checkout` | 秒级 / 最近一次提交 |

## 模块内技术债务

| 区域（本模块） | 债务类型 | 原因 | 严重度 | 偿还建议 |
|-|-|-|-|-|
| `unittest.main()` 位置 | 测试债 | `tests/test_api.py:507-508`、`tests/test_regressions.py:183-184` 被放在文件中部的类之间；直跑少 11 个用例且 rc=0 | **严重** | 移到文件末尾，或统一删除 `__main__` 块 |
| HA 装配层零覆盖 | 测试债 | CI 不装 homeassistant（`.github/workflows/tests.yml:18`）+ 伪包跳过 `__init__.py`；14 个生产模块中 12 个 import HA | **严重** | 引入 `pytest-homeassistant-custom-component`；或至少 CI 装 HA 后做 `import custom_components.epson_ef` 冒烟 |
| `tools/` 无门禁 | 代码债 | CI lint 范围 `custom_components tests`（`:21`）；`ruff check .` 实测 3 errors | 中等 | `ruff check .` 进 CI，先修 2 个 F401 与 1 个 F821 |
| `wait_online` NameError | 代码债 | `tools/probe_retest_drops.py` 引用了属于 `probe_matrix.py` / `verify_full.py:41` 的函数 | 中等 | 改为已存在的 `wait_server_ready(120)`，或补 `async def wait_online(max_s)` |
| `STEP_UNIT = 10` 与生产矛盾 | 测试债（假事实） | `tools/verify_round4.py:24` 自复刻旧对焦假设，与 `const.py:122` `FOCUS_STEP_UNIT = 1`、`api.py:516-529`（`focus_step`）的纠正注释冲突 | 中等 | 删掉本地常量，改 `from const import FOCUS_STEP_UNIT` 或直接调 `api.goto_focus` |
| 内网 IP 默认值 | 文档/安全债 | `tools/probe_focus.py:28`、`probe_power_state.py:20`、`probe_refresh.py:28`、`verify_round4.py:23` 硬编码 `192.168.61.7`；`tests/test_regressions.py:192` docstring 含 `192.168.61.250` | 轻微 | 全部改为空默认 + `sys.exit` 用法提示（与 `tools/README.md:8` 声明一致） |
| 20 份 importlib 样板 | 代码债 | 无共用 helper，7 行 × 20 文件逐字重复 | 轻微 | 抽 `tools/_bootstrap.py`（但会破坏"脚本可单文件复制"的优点） |
| `pytest` 未声明 | 工程债 | 无 requirements/pyproject/tox；CI 只装 3 个包；文档却写 `pytest tests/`（`docs/reports/v3.10.0-release-test.md:32`） | 轻微 | 加 `requirements-dev.txt`（aiohttp/pytest/ruff/pyflakes 并锁版本） |
| ruff 无配置无版本锁 | 工程债 | 无 `ruff.toml`/`pyproject.toml`；CI `pip install ruff` 浮动 | 轻微 | 加 `ruff.toml` 固定 `select`，CI 锁版本 |
| 无退出码约定 | 设计债 | 12 处 `sys.exit` 全是用法错误；`verify_*` 逻辑失败 rc=0（`tools/verify_round4.py:79` 只 print） | 中等 | 逻辑失败统一 `sys.exit(1)`，让脚本可被自动化调用 |
| 存档无 schema/版本 | 数据债 | `probe_results.json` 字段随意扩展，verdict 字符串自由文本 | 中等 | 定义 `{name: {params: {verdict, response, ts, firmware}}}` |
