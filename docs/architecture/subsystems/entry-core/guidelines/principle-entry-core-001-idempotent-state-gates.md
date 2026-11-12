---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-entry-core-001
description: "装配与迁移必须靠『读取当前状态再决定』实现幂等，而不是引入迁移标记位。setup 会被 HA 在重启、reload、退避重试、多 entry 场景下重复调用；版本门禁（entry.version==1）、身份短路（old_id==new_id）、服务短路（has_service）三者共同保证重复执行为空操作。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
principle_category: 架构
principle_scope: module
---

# 幂等靠状态门禁，不靠标记位

## 原则详细描述

`async_setup_entry` 不是"执行一次"的函数，而是"HA 认为该条目该上线时就调用"的函数。它可能在 HA 重启、条目 reload、`ConfigEntryNotReady` 退避重试、以及多设备条目并存时被反复触发。因此本模块所有有副作用的分支都必须自带门禁：

1. **版本门禁**：`async_migrate_entry` 只在 `entry.version == 1` 时改写，改写后立刻把 `version=2` 写回（`__init__.py:33-38`）。第二次调用门禁不成立，函数只 `return True`（`:39`）。
2. **身份短路**：`_async_migrate_identity` 首行 `if old_id == new_id: return`（`__init__.py:83-84`）。由于调用方每次 setup 都无条件调用（`:180`），短路是唯一的幂等保障。
3. **清理可重入**：`_async_remove_legacy_entities` 是"遍历注册表 + 集合判定 + 删除命中的"（`__init__.py:160-163`），删过之后集合不再命中，天然可重入。
4. **服务短路**：`async_register_services` 用 `has_service` 判定（`services.py:40-41`），多 entry 时只注册一次。

反过来说，代码里**没有**任何"已迁移"标记字段、没有迁移状态机、没有 `entry.data["migrated"]=True` 之类的痕迹（全文件无此类键，`__init__.py:31-120`）。

## 为什么要这样

- **标记位会与真实状态脱节**：标记位写在 entry 里就要读写 entry，而写 entry 会触发 update listener 的 reload（`__init__.py:209-210`），在 setup 过程中写等于埋一个 reload 循环。Store 里写标记又引入第三个真相源，HA 的 entry 状态与 Store 标记可能不一致（用户手工改 entry、恢复备份、`[待确认: HA 快照恢复是否保留 Store]`）。
- **HA 的重复调用是常态而非异常**：`ConfigEntryNotReady` 的语义就是"稍后重试"（`__init__.py:191-193`），重试会完整重跑 setup，包括身份解析与注册表治理。
- **幂等门禁的失败模式是"空操作"，标记位方案的失败模式是"永不执行"或"永久重复执行"**：前者可恢复，后者需要人工干预。

## 适用范围

- `custom_components/epson_ef/__init__.py` 的 `async_migrate_entry`、`_async_get_base_id`、`_async_migrate_identity`、`_async_remove_legacy_entities`
- `custom_components/epson_ef/services.py` 的 `async_register_services`
- 任何新增的"每次 setup 都执行"的治理逻辑

## 规则

- 有副作用的 setup 辅助函数必须在入口处读取真实状态并短路，不得依赖"只会被调用一次"的假设。
- 状态判据必须来自 HA 自身维护的权威状态（`entry.version`、注册表内容、`hass.services.has_service`、Store 内容），不得新增自定义标记字段来"记住已做过"。
- 迁移函数必须"读旧值 → 写新值 → 让旧判据自然失效"形成闭环（如 `version=2` 使 `version==1` 门禁失效）。
- 门外版本必须显式拒绝：当前 `async_migrate_entry` 对 `version >= 3` 也 `return True`（`__init__.py:33-39` 无 else），这是本原则的**现存违例**，新增版本分支时必须补 `return False`。

## 反模式 / 禁止项

- ❌ 在 `entry.data` / `entry.options` / Store 里新增 `migrated_v2: true` 之类标记位。
- ❌ 假设 `async_setup_entry` 每次 HA 启动只跑一次。
- ❌ 让迁移函数依赖调用顺序（"因为前面已经迁移过，所以这里可以假设是新格式"）。
- ❌ 把幂等性寄托在"HA 会去重"上——HA 只对 `_abort_if_unique_id_configured` 这类显式声明去重，不对 setup 副作用去重（`config_flow.py:47-50`）。

## 修改检查清单

- [ ] 新增的 setup 辅助函数是否在入口处读取权威状态并短路？
- [ ] 迁移写回后，原判据是否自然失效（而不是靠另一个标记）？
- [ ] 若新增 config entry 版本分支，是否补了更高版本的 `return False`？
- [ ] 服务类副作用是否用 `has_service` 判定（且判定项与实际注册项一致）？
- [ ] 重试场景（`ConfigEntryNotReady` → 重跑 setup）下，已完成的副作用是否会被安全重放？
