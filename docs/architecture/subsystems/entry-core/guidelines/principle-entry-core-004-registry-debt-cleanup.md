---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-entry-core-004
description: "删除或改型任何实体，都必须在 setup 阶段显式清偿注册表负债：把旧 unique_id 后缀登记进 legacy 清理表，并让清理同时匹配新身份与历史地址两种前缀。HA 注册表只增不删，漏登记的实体将永久以 unavailable 存在。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
principle_category: 治理
principle_scope: module
---

# 升级必须清偿注册表负债

## 原则详细描述

HA 的实体注册表是**持久且只增不减**的：上一版本创建过的实体，即使新版本不再创建，注册项仍留在 `.storage/core.entity_registry` 里，永久显示 `unavailable`。因此本模块在每次 setup 时执行一次"清偿"：

```python
legacy = {
    "sensor": ("standby_mode", "serial", "light_output"),
    "switch": ("setting-refresh",),
    "button": ("button-engine_refresh",),
}
wanted = {(platform, f"{base_id}-{suffix}"), (platform, f"{host}:{port}-{suffix}")}
for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
    if (entity.domain, entity.unique_id) in wanted:
        registry.async_remove(entity.entity_id)
```
（`__init__.py:146-163`）

三条硬约束：

1. **表是知识，不是推导**："历史上存在过哪些实体"无法从当前代码推出，只能手工登记。注释里逐条写明每个后缀的去向与原因（`__init__.py:132-141`）。
2. **双前缀匹配**：同一后缀要同时匹配新身份前缀 `{base_id}-` 与历史地址前缀 `{host}:{port}-`（`:154-155`），因为升级用户的 unique_id 可能还是地址形态。
3. **按条目遍历，不按 unique_id 反查**：用 `er.async_entries_for_config_entry` + 集合判定（`:160-161`），避免依赖 `async_get_entity_id(domain, platform, uid)` 的参数顺序（见下方事故）。

## 为什么要这样

- **这是真实发生过的故障，不是假想**：3.5.6 → 3.10.0 升级后，`switch.*_shua_xin`（`setting-refresh`）与 `button.*_engine_refresh_experimental` 变成永久 unavailable 的孤儿。更糟的是修复本身也曾静默失效——早期版本调用 `registry.async_get_entity_id(platform, DOMAIN, uid)`，而真实签名是 `(domain, platform, unique_id)`，两个实参都是字符串所以不报错，清理只是不生效（事故记录：`tests/test_regressions.py:1-11`、`:141-148`）。
- **升级路径不可测试**：CI 不安装 `homeassistant`，装配层零运行期覆盖，孤儿只能在真机升级后才被发现，代价是用户看到一堆不可用实体。
- **不能靠"删了实体就自动清"**：HA 没有实体"托管回收"机制；只有集成自己知道哪些 ID 是自己造的。

## 适用范围

- `custom_components/epson_ef/__init__.py:123-163` 的清理逻辑与 `:146-150` 的清理表
- 任何修改实体清单的发版：新增平台、改型实体（sensor→number/select）、删除按钮、重命名后缀
- `config_flow.py` 的 v1→v2 条目迁移（同样是"改写用户既有状态"，`__init__.py:31-39`）

## 规则

- 删除/改型实体时，同一提交内必须把旧后缀加入 `legacy[platform]` 元组，并在注释写清"改成什么了"或"为什么删"。
- 清理必须同时覆盖 `{base_id}-` 与 `{host}:{port}-` 两种前缀（`:154-155`）；若引入第三种身份形态，必须一并加入。
- 清理必须只作用于本条目实体（`async_entries_for_config_entry`），禁止全局扫描 `unique_id` 后删除（会误删别的条目）。
- 清理删除前不得依赖 `entity.entity_id` 的构造规则（`entity_id` 可能被用户手工改过，只有 `unique_id` 可信）。
- 回归测试 `tests/test_regressions.py:112-119` 的断言必须随表扩展（当前断言 5 条中的 4 条：`setting-refresh`、`button-engine_refresh`、`standby_mode`、`light_output`）。
- `legacy` 表只增不删：即便某天所有用户都升过级，删表也会让"长期未升级的用户"再次产生孤儿。

## 反模式 / 禁止项

- ❌ 只改实体代码、不改清理表就发版。
- ❌ 用 `async_get_entity_id(platform, DOMAIN, uid)`（参数顺序陷阱，历史已踩）。
- ❌ 用 `dr.async_get_device(identifiers=...)`（HA 2026.x 弃用、2027.8.0 移除，`__init__.py:106-109`，AST 测试守护 `tests/test_regressions.py:121-138`）。
- ❌ 把清理写成"删除所有不在当前实体清单里的注册项"——当前实体清单由 7 个平台动态生成，无法在 setup 时穷举，误删风险极高。
- ❌ 在 unload 里做清理（未加载的条目也应能被清理，清理必须落在 setup）。

## 修改检查清单

- [ ] 是否删除了或被新平台取代了某个实体？清理表是否同步？
- [ ] 表项注释是否写清了去向（改成什么/为什么删）？
- [ ] 历史地址前缀是否仍被匹配（`:155`）？
- [ ] `tests/test_regressions.py` 的表内容断言是否仍成立并已扩展？
- [ ] 清理是否仍限定在本 `entry_id` 范围内？
- [ ] README 的「从 x 升级」表格是否同步（`README.md:101-106`）？
