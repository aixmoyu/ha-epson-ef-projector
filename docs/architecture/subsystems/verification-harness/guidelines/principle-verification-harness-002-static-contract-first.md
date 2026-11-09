---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-verification-harness-002
description: "凡是『导入即拉入 Home Assistant』或『只在特定运行时分支才抛错』的耦合，一律用 ast 静态解析去断言，而不是 import 后跑一遍。契约必须双向（定义侧宽度 ↔ 全部消费侧宽度）、必须覆盖两种语法形态（for 语句与推导式）、必须锚定消费位置。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
principle_category: 工程
principle_scope: module
---

# 静态契约优先于运行时断言

## 原则详细描述

本仓库有两个"跑一遍测不出来"的耦合：

| 耦合 | 为什么跑不出来 | 静态契约 |
|-|-|-|
| 元组表宽度 ↔ 解包点宽度 | `ValueError: too many values to unpack` 只在执行到那一行时抛；而 `coordinator.py:97` 的解包只在"投影仪开机 + 第 12 次轮询"才执行——待机时整个分支被 `if state == "on"` 跳过 | `tests/test_const_contract.py:111-125` |
| 注册表 API 的实参顺序 | `async_get_entity_id(platform, DOMAIN, uid)` 两个参数都是字符串，写反不报错、只静默查不到 | `tests/test_regressions.py:150-170` |

面对这类问题，项目**不引入 HA 运行时**，而是读源码文本：`ast.parse(path.read_text())`（`tests/test_const_contract.py:36`、`tests/test_regressions.py:101`、`:123`、`:151`、`:178`）。这让 CI 在没有任何 HA 的情况下，仍能对 12 个 import HA 的模块施加结构性约束。

契约的四个必备性质（每条都有代码对应）：

1. **双向**：既由定义求宽度（`_table_arity`，`:39-52`），又由用法求宽度（`_unpack_arities`，`:64-88`），两侧等值才算通过。
2. **双形态**：同时匹配 `ast.For` 与 `ast.comprehension`。注释写明"只认 `ast.For` 会漏掉 `select.py` / `switch.py` 里的推导式"（`:66-69`）——实测这四张表的 6 个解包点里有 2 个是推导式（`switch.py:38`、`select.py:40`）。
3. **非空**：表不能是空列表（`:103`），消费者也不能为空——否则报 "table may be dead code"（`:116-118`）。
4. **位置锚定**：`test_known_consumer_locations` 用集合差集断言 6 个已知 (表, 文件) 组合不得缺失（`:127-142`），防止"改了表却把解包点挪到测试看不到的地方"。

## 为什么要这样

- **真实事故驱动**：`tests/test_const_contract.py:1-9` 记录了唯一一次 release-blocking 事故——`WRITABLE_SELECTS` 从 5 元组扩到 6 元组，`select.py` 同步改了，`coordinator.py:97` 漏改。静态检查（当时的 ruff F,E9）与既有单测**都没抓到**，因为待机时该分支不执行。真机部署后每次诊断轮询抛 `ValueError`，58 个实体全部 unavailable（`tests/test_const_contract.py` 的 docstring 有记录）。
- **AST 比 import 更"抗环境"**：`test_const_contract.py` 的 `TABLE_SOURCES` 里 `BUTTON_DEFS` 定义在 `button.py`，而 `button.py` 有 7 处 `homeassistant` import——根本无法导入。AST 让它照样能被契约覆盖。
- **有效性已被反证过**：测试有效性通过"注入 bug → 测试必须失败"的方式验证过。这是全项目唯一有记录的测试有效性验证。
- **静态契约的成本是显式的**：它只看结构，不看语义。表里某列的**含义**（如 `WRITABLE_SELECTS` 第 5 列是 options、第 6 列是 labels）它不知道，因此"两列写反但宽度不变"是它抓不到的——这个缺口必须靠行为用例补。

## 适用范围

- `tests/test_const_contract.py`（表宽度契约）
- `tests/test_regressions.py` 的 `LegacyEntityCleanupTests` / `EntityRegistryApiTests` / `CoordinatorContractTests`（装配约束）
- 任何"被检查对象 import 成本高"或"错误只在罕见分支暴露"的场景

## 规则

- **断言对象必须是结构不变量**（宽度、实参位置、名字存在性），不是实现细节（行号、变量名）。
- **必须同时覆盖语句式与推导式**两种语法形态；新增语法形态（如 `match`、`async for`）时要同步扩展扫描器。
- **必须断言"至少存在一个消费者"**，否则表被删空或改名后契约会静默通过。
- **必须锚定已知消费位置**（文件级即可，见 ADR-002 的取舍）。
- **失败信息必须能指向修法**：`tests/test_const_contract.py:122-124` 的格式（`f"{filename}:{lineno} unpacks {name} into {width} names, but rows have {arity} fields — this raises ValueError at runtime"`）是范例。
- **typing 注解不可信**：`_table_arity` 的 docstring 明写"由源码求得表项元组宽度(不信任 type annotation)"（`:40`）。因为注解是 `list[tuple[str, str, str, str]]`，与实际的 `len(first.elts)` 可能不一致。

## 反模式 / 禁止项

- ❌ 用 `*rest` 或 `_` 兜底解包"兼容"宽度变化——会让该解包点从扫描结果中消失（`target` 必须是纯名字元组，`:81-84`），契约从此对它失明。
- ❌ 只检查 `ast.For`，漏掉 `ast.comprehension`。
- ❌ 把表名改成动态构造（如 `for name in [...]: globals()[name] = ...`）——`_table_arity` 只认 `AnnAssign` + `Name` target，动态表无法被检查，等于退出契约覆盖。
- ❌ 断言行号（会因无关改动频繁失败）。位置锚定只到文件级。
- ❌ 用 `assertTrue(len(x) > 0)` 代替 `assertEqual(width, arity)`——前者只能发现"空"，发现不了"不等宽"。
- ❌ 认为契约测试通过就等于表语义正确——它只保证宽度同构。

## 修改检查清单

- [ ] 改表宽度时，是否同一次提交内更新了全部解包点，并确认 `test_every_consumer_unpacks_full_width` 仍绿？
- [ ] 新增表时是否登记进 `TABLE_SOURCES`（`tests/test_const_contract.py:27-32`）？值是否指向正确的源文件？
- [ ] 新增表的消费位置是否加进了 `test_known_consumer_locations` 的 `expected` 集合（`:129-136`）？
- [ ] 是否有测试证明契约真的会失败？（"注入 bug → 测试必须失败"）
- [ ] 扫描器是否仍能覆盖新的语法形态（推导式/异步推导式/match）？
- [ ] 失败信息里是否包含 `文件:行号` 与"为什么这是运行时错误"？
