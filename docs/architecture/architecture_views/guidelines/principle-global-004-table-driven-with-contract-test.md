---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-global-004
description: "实体装配必须由 const.py 的声明式元组表驱动（而不是每个实体手写一遍命令名），且每张表必须配一条 AST 契约测试，锁死『表的元组宽度 == 所有消费者的解包宽度』。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
principle_category: 工程
principle_scope: global
---

# 表驱动实体装配 + 宽度契约测试成对出现

## 原则详细描述

本项目有 58 个实体，其中 30 个按钮、11 个开关、6 个选择器、7 个 number 全部由**元组表**生成，而不是逐个手写类：

| 表 | 位置 | 形状 | 消费者 |
|-|-|-|-|
| `WRITABLE_SWITCHES` | `const.py:53-66` | 6 × 4 元组 `(suffix, key, get_cmd, set_cmd)` | `switch.py:35-40`（装配）+ `coordinator.py:92-96`（回读） |
| `WRITABLE_SELECTS` | `const.py:75-92` | 4 × **6** 元组 `(suffix, key, get_cmd, set_cmd, options, labels)` | `select.py:38-41`（装配）+ `coordinator.py:97-101`（回读） |
| `AMBIENT_LIGHT_SWITCHES` | `const.py:138-142` | 3 × 4 元组 `(suffix, key, icon, kind)` | `switch.py:43-46` |
| `BUTTON_DEFS` | `button.py:17-83` | 30 × 5 元组 `(suffix, key, icon, cmd_id, params)` | `button.py:94-97` |

配套的契约测试不是"跑一遍看有没有异常"，而是**用 `ast` 静态比对宽度**：`tests/test_const_contract.py` 解析表定义与所有解包点，断言"表宽度 == 每个 `for a,b,... in TABLE` 的解包宽度"，并锁定已知消费位置不得漂移（`tests/test_const_contract.py:111-145`）。

## 为什么要这样

**因为一张表被多处解包，是最容易被静默破坏的耦合。** 项目里真实发生过 release-blocking 事故：

> `WRITABLE_SELECTS` 从 5 元组扩到 6 元组（新增 options），但 `coordinator.py` 仍按 5 元组解包 —— 静态测试与单元测试都没抓到，真机部署后每次诊断轮询都抛 `ValueError: too many values to unpack`，整个集成的所有实体变成 unavailable。
> — `tests/test_const_contract.py:1-15`（历史记录原文）

这类 bug 的特殊性：**Python 的元组解包错误只在运行到那一行时抛**，而该行只在"投影仪开机 + 第 12 次轮询"才执行——所以离线测试与真机待机状态都不会触发它（`docs/reports/v3.10.0-release-test.md` 记录的正是待机时不执行该分支而漏测）。AST 静态比对是唯一能在 CI 里拦住它的手段。

表驱动本身的收益：新增一个可写开关 = 在表里加一行（`const.py:53-66`），装配、轮询回读、翻译键、unique_id、raw_value 属性全部自动获得；若手写，会重复 6 处样板且极易漏掉回读注册。

## 适用范围

- `const.py` 的 4 张实体表
- `button.py` 的 `BUTTON_DEFS`
- 任何"一处定义、多处消费"的元组/列表结构

## 规则

- 新增可写设置：**只改表**，不改装配代码；若必须改装配代码，说明表结构不表达该需求，应先重构表。
- 表的形状变化（加/减列）时，**同一次提交内**必须更新所有解包点，并确认 `tests/test_const_contract.py` 仍通过。
- 每张被多处解包的表都必须登记到契约测试的 `TABLE_SOURCES` 中（`tests/test_const_contract.py:27`）。
- 表的每一列都应有注释说明语义（现有表均有，如 `const.py:49`、`:69-74`）。

## 反模式 / 禁止项

- ❌ 新增表但不加契约测试条目。
- ❌ 用 `*rest` 兜底解包"兼容"宽度变化——静默吞掉结构漂移，等于让契约失效。
- ❌ 在平台文件里重复定义命令名（应集中在 `const.py` 或使用 `api.py` 的语义方法）。
- ❌ 只跑 `pytest` 就认为表改动安全（真正拦住宽度错误的是 AST 契约测试，见 `tests/test_const_contract.py:127`）。

## 修改检查清单

- [ ] 改动表后，`python3 -m pytest tests/test_const_contract.py -v` 是否 3 个用例全绿？
- [ ] 是否所有解包点（装配 + coordinator 回读 + 翻译键）都同步更新了？
- [ ] 新表是否登记进 `TABLE_SOURCES`？
- [ ] 新列是否有注释说明语义？
