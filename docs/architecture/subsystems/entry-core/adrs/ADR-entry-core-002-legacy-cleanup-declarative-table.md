---
project: "ha-epson-ef-projector"
type: adr
id: ADR-entry-core-002
description: "为遗留实体清理选择『声明式后缀表 + 按条目遍历注册表』，而不是按 unique_id 反查或全局扫描。直接动因是一次真实事故：async_get_entity_id 参数传反导致清理静默失效、孤儿实体长期残留。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "用声明式清理表 + 条目内遍历清偿注册表孤儿"
date: 2026-11-07
code_refs:
  - "custom_components/epson_ef/__init__.py:123-163"
  - "custom_components/epson_ef/__init__.py:146-155"
  - "custom_components/epson_ef/__init__.py:105-116"
  - "tests/test_regressions.py:96-171"
  - "README.md:101-106"
---

# ADR-002: 用声明式清理表 + 条目内遍历清偿注册表孤儿

## 状态

accepted（实现于 3.10.0 真机测试后的修复轮）

## 1. 背景

**业务背景**：集成在 3.5.x → 3.10.0 之间移除了若干实体、改型了若干实体：`switch.*_shua_xin`（`setting-refresh`）移除（`GET_REFRESH` 实为小时数，改用 `number.*_refresh_timer`）；`button.*_engine_refresh_experimental` 移除（NOTIFY 仅标记刷新确认应用启动）；`sensor.*_light_output` → `number.*_light_output`；`sensor.*_standby_mode` → `select.*`；`sensor.*_serial` 移除（`README.md:101-106`、`__init__.py:132-141`）。

**技术背景**：HA 的实体注册表是持久的。上一版本创建过、新版本不再创建的实体，注册项仍留在 `.storage/core.entity_registry` 中，永久显示 `unavailable`。集成没有"托管回收"机制，只能自己删除。

**核心问题**：在 58 个实体的固定清单、且历史版本曾创建过不同清单的前提下，如何在每次 setup 时安全、可重入地删除"本版本不再创建"的注册项，且不误删仍在用的实体？

**约束条件**：

- **必须可重入**：setup 会被反复调用（重启、reload、重试）。
- **身份形态不止一种**：升级用户的 `unique_id` 可能是 `{host:port}-{后缀}` 或 `{serial}-{后缀}`。
- **CI 无法验证**：CI 不安装 `homeassistant`（`.github/workflows/tests.yml:18`），清理逻辑只能在真机升级时暴露问题。
- **误删代价高**：删错会把在用实体的历史一起删掉。

## 2. 决策驱动因素（Decision Drivers）

1. **正确性优先于简洁性**（重要性：高）：宁可多写一张表，也不能留下永久 unavailable 的孤儿——这是用户可见的故障。
2. **误删风险必须可控**（重要性：高）：任何"推断哪些该删"的方案都可能删错。
3. **必须兼容已发生的两种身份形态**（重要性：中）：地址前缀与序列号前缀。
4. **可被静态测试守护**（重要性：中）：运行期不可测，至少要让表内容和 API 用法可被 AST 断言。
5. **实现成本**（重要性：低）：一次性维护成本可接受。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|---|---|
|Option A|声明式 `{platform: (suffix,)}` 表 + `async_entries_for_config_entry` 遍历 + 集合判定|精确、可重入、误删风险最低；表内容可被 AST 测试断言；不依赖任何 API 参数顺序|每删一个实体都要手工登记；表会随版本增长|漏登记 → 孤儿残留（可被测试部分缓解）|
|Option B|按 unique_id 反查：`registry.async_get_entity_id(domain, platform, uid)` 逐个判断后删除|代码短；O(表项) 次查找|依赖 API 参数顺序——历史上正是把 `(domain, platform, uid)` 传成 `(platform, DOMAIN, uid)`，两个实参都是字符串故不报错，清理静默失效|高：已实际发生，孤儿残留数版本|
|Option C|全局扫描：遍历整个实体注册表，删除所有属于本 domain 但不在当前实体清单中的项|无需维护表，理论上自动|当前实体清单由 7 个平台在 setup 时动态生成，setup 阶段无法穷举；会把"本条目暂未创建"的实体误删|高：误删在用实体|
|Option D|不做清理，让用户手工删除孤儿|零代码|用户体验差，README 承诺"自动清理"（`README.md:97-98`）|中：承诺不成立|

### Option A：声明式表 + 条目内遍历（已采纳）

```python
legacy = {"sensor": ("standby_mode", "serial", "light_output"),
          "switch": ("setting-refresh",),
          "button": ("button-engine_refresh",)}
wanted = {(p, f"{base_id}-{s}") for p in legacy for s in legacy[p]} \
       | {(p, f"{host}:{port}-{s}") for p in legacy for s in legacy[p]}
for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
    if (entity.domain, entity.unique_id) in wanted:
        registry.async_remove(entity.entity_id)
```
（`__init__.py:146-163`）

### Option B：按 unique_id 反查

`registry.async_get_entity_id(domain, platform, unique_id)` 是 HA 提供的精确查找。问题在于它的三个参数类型相近（都是字符串），顺序写错不会抛异常，只会返回 None → 清理逻辑变成"什么都没找到"的空转。事故记录明确写在 `tests/test_regressions.py:141-148`：「因为两者都是字符串，写反了不会报错」。此外反查是 N 次 O(1) 查找，而遍历是 1 次 O(n)——在 n=58 的量级上毫无性能差异，却多了一个易错点。

### Option C：全局扫描

需要一份"当前应有的实体清单"作为差集基准。但 7 个平台的实体是在 `async_forward_entry_setups`（`__init__.py:203`）期间由各平台动态 `async_add_entities` 创建的，setup 阶段拿不到这份清单；要拿到就得把实体构造逻辑复制一份到 `__init__.py`，等于制造第二真相源，且任何平台改动都会让差集算错并删除在用实体。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

- 驱动 1/2（正确性与误删控制）：只有 A 的删除集合是"显式白名单枚举"，任何未登记的东西都不会被碰。
- 驱动 3（双身份形态）：A 的两套前缀展开（`{base_id}-`、`{host}:{port}-`）天然覆盖两种历史形态（`:154-155`）。
- 驱动 4（可测试性）：表是模块级变量，可被 AST `literal_eval` 取出断言（`tests/test_regressions.py:99-119`）；"禁用 `async_get_device(identifiers=...)`"与"`async_get_entity_id` 实参顺序"同样用 AST 锁死（`:121-171`）。
- 驱动 5（成本）：表只有 5 项，维护成本可忽略。

**被否决方案的关键否决点**：B 否决于已发生的静默失效事故；C 否决于"无法在 setup 阶段获得完整现行清单"这一硬约束；D 否决于产品承诺。

### 4.1 实施要点

1. 表结构固定为 `dict[platform, tuple[suffix, ...]]`，新增项只在元组末尾追加，不改结构（`:146-150`）。
2. 每个后缀展开为两个完整 `unique_id`（新身份前缀、地址前缀），组成 `wanted` 集合（`:151-155`）。
3. 遍历必须限定在 `entry.entry_id` 内（`er.async_entries_for_config_entry`，`:160`），禁止全局扫描。
4. 判定用 `(entity.domain, entity.unique_id)` 二元组，不依赖 `entity_id`（用户可改 `entity_id`）。
5. 删除后记 info 日志并标注 `(superseded)`（`:163`）。
6. 每条表项的"历史去向"必须写进 docstring（`:132-141`），并与 README 升级表对齐（`README.md:101-106`）。
7. 三条 AST 护栏：清理表内容（`tests/test_regressions.py:112-119`）、禁用 `async_get_device(identifiers=...)`（`:121-138`）、`async_get_entity_id` 实参顺序（`:141-171`）。

### 4.2 已知局限

- **漏登记即孤儿**：表是手工维护的枚举，与实体定义无机械关联；新增/删除实体时忘记登记不会被任何测试发现（现有测试只断言 5 条表项中的 4 条还在表里——`serial` 未被断言——且不检测"是否该有更多条"）。缓解建议见本模块 `risk.md` 技术债务「`__init__.py:146-150`」。
- **历史前缀只取当前地址**：`{host}:{port}` 用本次 setup 的地址，不读 Store 里钉死的旧地址；换过址且未取到序列号的用户，其旧地址前缀孤儿不被覆盖（`risk.md` RISK-EC-13）。

## 备注

- 同源决策：`architecture_views/guidelines/principle-global-006-identity-over-address.md` 中「删除/替换任何实体时，必须把旧 unique_id 加入清理表」。
- `_async_migrate_identity` 采用同样的"遍历条目"策略，两处一致（`__init__.py:110`、`:160`）。
