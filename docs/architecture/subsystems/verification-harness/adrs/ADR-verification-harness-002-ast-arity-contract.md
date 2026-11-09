---
project: "ha-epson-ef-projector"
type: adr
id: ADR-verification-harness-002
description: "配置表的结构契约选择『AST 静态解析 + 双向宽度比对』，而不是运行时断言、类型系统约束或纯人工 review。契约锚定到文件级消费位置而非行号，并要求表必须有消费者以防死代码。这条决策直接来自 3.10.0 的全实体 unavailable 事故。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "用 AST 双向宽度比对锁定表-消费者契约"
date: 2026-11-01
code_refs:
  - "tests/test_const_contract.py:1-146"
  - "custom_components/epson_ef/const.py:53-92"
  - "custom_components/epson_ef/const.py:138-142"
  - "custom_components/epson_ef/button.py:17-83"
  - "docs/reports/v3.10.0-release-test.md:16-29"
---

# ADR-002: 用 AST 双向宽度比对锁定表-消费者契约

## 状态

accepted（2026-11-01 事故后立即建立，`docs/reports/v3.10.0-release-test.md:29` 记载"已用'注入 bug → 测试必须失败'验证有效性"）

## 1. 背景

**业务背景**：3.10.0 发布前，`WRITABLE_SELECTS` 从 5 元组扩到 6 元组（新增 `options` 列，用于投影方式/投影偏好）。`select.py` 同步改了，**`coordinator.py:97` 漏改**。特征：

- 待机时该分支被 `if state == "on"` 跳过，因此"装上能启动、待机看着正常"；
- 一旦开机并到达诊断周期，每次轮询抛 `ValueError: too many values to unpack (expected 5, got 6)`；
- coordinator 每轮失败 → **58 个实体全部 unavailable**。

静态检查（当时只有 ruff F,E9）与既有单测都没抓到。真机部署后才暴露（`docs/reports/v3.10.0-release-test.md:16-22`）。

**技术背景**：4 张声明式元组表驱动 58 个实体中的大部分：

| 表 | 形状 | 消费者 |
|-|-|-|
| `WRITABLE_SWITCHES` | 6 × 4（`const.py:53-66`） | `coordinator.py:92`、`switch.py:38` |
| `WRITABLE_SELECTS` | 4 × **6**（`const.py:75-92`） | `coordinator.py:97`、`select.py:40` |
| `AMBIENT_LIGHT_SWITCHES` | 3 × 4（`const.py:138-142`） | `switch.py:45` |
| `BUTTON_DEFS` | 30 × 5（`button.py:17-83`） | `button.py:96` |

**核心问题**：如何在不运行 HA、不启动 coordinator、不部署真机的前提下，保证"任何一张表的元组宽度变化，都必然被所有解包点同步"？

**约束条件**：

- 消费者分布在 `coordinator.py`/`switch.py`/`select.py`/`button.py`，其中 3 个 import HA，无法在本环境导入。
- 必须覆盖两种语法形态：语句式 `for` 与列表/生成器推导式（`select.py:40`、`switch.py:38` 用的是推导式）。
- 必须在 CI（无 HA）中运行。
- 失败信息必须能让维护者立刻定位到"哪个文件的哪一行解包了几个名字"。

## 2. 决策驱动因素（Decision Drivers）

1. **不漏检**（重要性：高）：必须能拦住"改了表没改消费者"与"改了消费者没改表"两个方向。
2. **可在无 HA 环境运行**（重要性：高）：这是 CI 的硬约束。
3. **失败可定位**（重要性：高）：错误信息要含文件名、行号、两侧宽度。
4. **抗重构**（重要性：中）：正常重构（加注释、移动函数）不应导致误报。
5. **实现成本**（重要性：中）：不能引入新依赖。

## 3. 候选方案（Considered Options）

| 方案 | 简述 | 优点 | 缺点 | 风险 |
|-|-|-|-|-|
| **Option A** AST 双向宽度比对 | `ast.parse` 求表宽 + 扫全部解包点求宽度，两侧等值 | 无需 import；能跨 HA 依赖模块；失败信息可带文件:行号；零依赖 | 只看结构不看语义；需要维护 `TABLE_SOURCES` 与已知位置集合 | 动态构造的表无法检查 |
| **Option B** 运行时断言 | 在 coordinator 里对每行做 `assert len(row) == N` | 实现简单 | 只在运行到该分支时触发——正是本次事故的失效模式；且要在生产代码里塞测试逻辑 | 高：与事故同构 |
| **Option C** 类型系统（TypedDict / NamedTuple / dataclass） | 把元组换成带名字的结构 | 从根上消除"宽度"概念；IDE 可查 | 需重写 4 张表 + 6 个消费点；`NamedTuple` 会改变解包语义与性能；改动面大 | 中：大重构的风险高于收益 |
| **Option D** 人工 review checklist | 在 CONTRIBUTING 里写"改表要改所有解包点" | 零成本 | 仓库无 CONTRIBUTING、无 pre-commit、无 PR 模板；纯依赖记忆 | 高：本次事故正是"人没记住" |
| **Option E** mypy/pyright 类型检查 | 用类型检查器发现解包宽度不匹配 | 通用工具 | 仓库无类型检查配置，且 `list[tuple[str,str,str,str]]` 的注解本身就能被绕过；CI 未装 | 中：新增门禁依赖 |

### Option A：AST 双向宽度比对

```python
def _table_arity(name):        # 定义侧：找 AnnAssign，取首行 elt 个数
def _unpack_arities(name):     # 消费侧：扫全部 *.py，For + comprehension
```

三个用例：
1. `test_tables_are_non_empty_and_rectangular`（`:92-109`）：表非空、每行等宽。
2. `test_every_consumer_unpacks_full_width`（`:111-125`）：每个解包点宽度 == 表宽，且至少有一个消费者。
3. `test_known_consumer_locations`（`:127-142`）：6 个已知 (表, 文件) 组合不得缺失。

### Option C：类型系统重构

把 `WRITABLE_SELECTS` 的 6 元组改成 `NamedTuple` 后，解包宽度错误会变成属性访问错误——但只有在**运行到那一行**时才报。这与 Option B 同构，只是错误类型不同。要真正静态拦住，仍需 mypy（Option E）。

### Option E：mypy

`tuple[str, str, str, str]` 注解对"解包 6 个名字"会给 `error: Need more than 4 values to unpack`，确实能拦。但：仓库当前零类型检查配置；CI 只装 3 个包；`const.py:75` 的注解是 `list[tuple[str, str, str, str, list[str], dict[str, str] | None]]`——**注解本身可能与实际不一致**（`_table_arity` 的 docstring 明写"不信任 type annotation"，`:40`），而事故恰恰可能是"注解与实际脱节"。AST 直接读**实际的字面量**，绕过注解这一层不可信信息。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

- 驱动因素 1（不漏检）：A 是唯一同时覆盖"表→消费者"与"消费者→表"两个方向的方案；B/C/E 都只在运行到该行时触发。
- 驱动因素 2（无 HA）：A 只需 `ast`，而 `button.py`（含表定义）有 7 处 HA import，无法导入——AST 是唯一可行路径。
- 驱动因素 3（可定位）：A 的失败信息形如 `f"{filename}:{lineno} unpacks {name} into {width} names, but rows have {arity} fields — this raises ValueError at runtime"`（`:122-124`），直接给出四要素。
- 驱动因素 4（抗重构）：A 锚定**文件级**位置而非行号，正常重构不误报（代价见下）。
- 驱动因素 5（成本）：A 零新依赖，3 个用例实测 0.085s。

**已知损失（随决策记录）**：

1. **语义不检查**：两列含义写反但宽度不变，A 抓不到（如把 `options` 与 `labels` 互换）。
2. **位置锚定只到文件级**：有人把解包点从 `coordinator.py:97` 挪到 `coordinator.py:200` 不会被发现，挪到另一个已登记文件也不会。
3. **动态表逃逸**：表名不能用 `globals()[name]` 之类的动态构造，否则 `_table_arity` 找不到（抛 `AssertionError: not found`）。
4. **`*rest` 逃逸**：解包点若写成 `a, b, *rest`，`target` 不是纯名字元组，会被过滤器跳过（`:81-84`），从此不受保护。
5. **`STANDBY_MODE_OPTIONS` 不在覆盖内**：它是 dict 而非元组表，只能靠行为断言（`tests/test_api.py:282-296`）。

### 4.1 实施要点

- 扫描器必须**同时**匹配 `ast.For` 与 `ast.comprehension`（`:74-79`）——只认前者会漏掉 `select.py:40` 与 `switch.py:38` 两个真实消费者。
- `_table_ref` 要处理 `NAME` / `const.NAME` / `pkg.const.NAME` 三种引用形态，取最内层名字（`:55-61`）。
- 表必须登记在 `TABLE_SOURCES`（`:27-32`），值指向定义文件（`const.py` 或 `button.py`）。
- 已知消费位置必须登记在 `expected` 集合（`:129-136`），用**差集**断言缺失（`expected - actual == set()`），这样新增消费者不会失败、只有丢失才会失败。

## 备注

- 事故的完整记录与"注入 bug 验证有效性"的做法见 `docs/reports/v3.10.0-release-test.md:16-29`。
- 该决策与全局原则 `architecture_views/guidelines/principle-global-004-table-driven-with-contract-test.md` 一致：表驱动必须成对出现宽度契约测试。
- 若未来表数量显著增长，`_parse()` 的重复 `ast.parse`（每表每用例重新 parse，`tests/test_const_contract.py:35-36`）需要加缓存；当前 4 表 × 14 文件总耗时 0.085s，不值得优化。
