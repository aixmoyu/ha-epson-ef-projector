---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-entity-platforms-004
description: unique_id 必须落在四套既有约定之内（平台前缀式 / setting 式 / 裸后缀式 / 直接使用 base_id），且 setting 式后缀同时是 coordinator settings 的键、strings.json 的翻译键、legacy 清理表的匹配段。新增或改名实体时必须四处同步，并由测试守护"当前实体集与 legacy 表不相交"。
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
principle_category: 工程
principle_scope: module
---

# unique_id 四套约定不可扩张，suffix 是四方共用的主键

## 原则详细描述

本模块 58 个实体的 unique_id 全部由 `{base_id}` 加一个后缀构成，但后缀的拼法有**四套并存**的约定（这是历史演化的结果，不是设计）：

|约定|模式|实例数|证据|
|-|-|-|-|
|① 平台前缀式|`{base_id}-<platform>-{key}`|30 button + 7 number（含 color_gain ×3）+ 1 select-ambient_mode = 38|`button.py:122`、`number.py:90/169/248/338/424`、`select.py:189`|
|② setting 式|`{base_id}-setting-{suffix}`|11 switch + 4 select = 15|`switch.py:78/148/210/270`、`select.py:75`|
|③ 裸后缀式|`{base_id}-{suffix}`|connectivity、standby_mode、light_hours、distance = 4|`binary_sensor.py:41`、`select.py:130`、`sensor.py:44`|
|④ 直接 base_id|`{base_id}`|media_player = 1|`media_player.py:68`|

**suffix 是四方共用的主键**，这是本模块最容易被低估的耦合：

1. `coordinator.data["settings"][suffix]` —— 读写都按 suffix 索引（`coordinator.py:95`, `:100`；`switch.py:91`；`select.py:94`）；
2. `strings.json` 的 `entity.<platform>.<translation_key>` —— 而 `translation_key` 当前恒等于 suffix（15/15 个 setting 项实测 `suffix == key`，`const.py:53-92`、`:138-142` 与 `button.py:17-83` 的 30 行全部满足）；
3. unique_id 尾段 —— `{base_id}-setting-{suffix}`；
4. `__init__.py` 的 legacy 清理表 —— 按 suffix 拼出 `(platform, unique_id)` 精确匹配（`__init__.py:146-163`）。

于是"改一个 suffix"= 同时改四处 + 迁移已注册用户的 unique_id。历史教训：`setting-refresh` 与 `button-engine_refresh` 被删除时未同步清理逻辑，升级用户留下永久 `unavailable` 的孤儿实体（`tests/test_regressions.py:1-9` 的开篇即记录此缺陷）；而清理函数本身还曾把 `async_get_entity_id` 的前两个参数传反，导致清理静默失效（`__init__.py:157-159` 的注释）。

**当前的三处具体风险**：

- `standby_mode` 在 legacy 表里登记为 `("sensor", "{base_id}-standby_mode")`（`__init__.py:147`），但当前它是 **select**（`select.py:130`）。键不同故当下安全；若将来搬回 sensor，**每次 reload 都会删掉活着的实体**。
- `setting-` 前缀被 switch 与 select 共用：当前后缀交集为空（switch 11 个 vs select 4 个），且 HA 注册表的唯一性作用域是 `(platform, unique_id)`，所以**不是注册表冲突**；但真正的冲突面在数据层——`settings` 是扁平字典，两张表若有同后缀会静默互相覆盖（`coordinator.py:92-101`）。
- 没有任何测试守护上述两点。`tests/test_const_contract.py` 只校验表**宽度**与解包点位置，不校验跨表后缀唯一性，也不校验实体集与 legacy 表不相交。

## 为什么要这样

1. **unique_id 是写入用户设备的持久标识**，改名即等同"删除旧实体 + 新建实体"：历史记录（recorder）、自动化引用、仪表盘卡片全部断链。本项目的 `_async_migrate_identity`（`__init__.py:79-120`）正是为一次 identity 迁移写了几十行补偿代码，且仍可能失败（`ValueError` 时只记 warning）。
2. **suffix 一旦跨层复用，就变成了隐式主键**：`settings` 键与 unique_id 尾段看似无关，实则由同一个字符串控制。加一行表项时若只看 `const.py`，很容易漏掉 `strings.json`（缺键不报错，只显示原始键名——静默劣化）或 legacy 表。
3. **四套共存已经让"猜 unique_id"变得不可能**：想通过 entity_id 反查实体，必须知道它属于哪套约定。这是可读性债务，但在不改动 58 个已注册 unique_id 的前提下**不可清理**——所以只能约束"不再新增第五套"。

## 适用范围

- 全部 7 个平台模块的新增/改名/删除实体。
- 特别是表驱动路径：`const.py:53-142` 与 `button.py:17-83`。

## 规则

- 新实体必须复用四套约定之一；**禁止第五套**。
- setting 类实体的 suffix 必须三处同名：`settings` 键、`translation_key`、unique_id 尾段。
- 后缀必须全局唯一（跨 switch/select/ambient/button 全表），因为 `settings` 是扁平字典。
- 删除或改名实体时，必须同时把旧 suffix 追加进 `__init__.py:146-150` 的 `legacy` 表，并覆盖 `host:port` 形态的旧 identity（表中已做双写，`__init__.py:152-155`）。
- 新增 `strings.json` 键时，`translations/en.json` 与 `translations/zh-Hans.json` 必须同步（实测三文件键集当前完全一致，共 57 键）。

## 反模式 / 禁止项

- ✗ 为"语义更清楚"给某平台单独造 `{base_id}-cfg-{suffix}` 之类的新前缀。
- ✗ 只改 `const.py` 的 suffix 而不迁移 unique_id 与 legacy 表。
- ✗ 让 `translation_key` 与 `suffix` 分叉（当前 15/15 全等，一旦分叉，`settings` 键与翻译键的对应关系就得靠脑记）。
- ✗ 删除实体后不登记 legacy（历史 bug，`tests/test_regressions.py:96-119` 有回归用例）。

## 修改检查清单

- [ ] 新 suffix 是否与现有 15 个 setting 后缀、30 个 button 后缀、4 个裸后缀全部不重复？
- [ ] `settings` 键 / `translation_key` / unique_id 尾段 是否三处同名？
- [ ] `strings.json` + `translations/en.json` + `translations/zh-Hans.json` 三处键集是否仍一致？
- [ ] 若是删除或改名：legacy 表是否已登记旧 suffix（含 `host:port` 形态）？
- [ ] 是否存在"当前实体 suffix"与 legacy 表的重叠（会删掉活实体）？
