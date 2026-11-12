---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-global-006
description: "设备身份必须与网络地址解耦：优先用固件序列号作实体/设备的唯一标识并持久化到 HA Store（不写进 config entry，以免触发 reload）；地址只是可『重新配置』的参数。身份变更时必须主动迁移注册表，而不是留给用户重建。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
principle_category: 架构
principle_scope: global
---

# 身份与地址解耦，升级必须自迁移

## 原则详细描述

投影仪的 DHCP 地址会漂移，因此身份不能建立在 `host:port` 上。三层实现：

1. **优先序列号**：`CONFIG_GET_PJ_SNO` params=`"1"` 取序列号（`api.py:302-315`，注释记录了无参数时返回空串的固件行为），取到即作为 `base_id` 并写入 `Store`（`__init__.py:58-66`）。
2. **⚠️ 存 Store 而非 config entry**：注释给出了明确理由——写进 config entry "would trigger a reload"（`__init__.py:48-51`）；Store key 为 `f"{DOMAIN}.{entry.entry_id}.identity"`（`__init__.py:53-55`）。序列号取不到时，把**首个** `host:port` 钉死（`__init__.py:68-76`），之后的地址变更不再改变身份。
3. **升级自迁移**：身份从旧 `host:port` 变到新 `base_id` 时，主动改实体 `unique_id`（保留后缀）与设备 `identifiers`（`__init__.py:79-120`），并删除被新版本取代的遗留实体（`__init__.py:123-163`）。

配套约束：实体 `unique_id` 一律以 `base_id` 为前缀 + 固定后缀（如 `{base_id}-setting-{suffix}`、`{base_id}-number-focus_position`），设备统一用 `identifiers={(DOMAIN, base_id)}`（`media_player.py:88-93`）。

## 为什么要这样

- **身份漂移的代价是历史断档**：HA 用 `unique_id` 关联实体历史、自动化引用、仪表盘。identity 变了而 unique_id 没跟着变，用户的历史数据与自动化全部失联。
- **"升级后出现永久 unavailable 孤儿"是真实发生过的故障**：上一版创建过的 `setting-refresh` / `button-engine_refresh` 在新版本不再创建，但注册表里还留着，永远显示 unavailable。因此必须在 setup 阶段显式删除（`__init__.py:123-142` 的 legacy 映射表），并配上回归测试（`tests/test_regressions.py:96-120`）。
- **不写 config entry 是为了避免自触发 reload**：写 entry 会触发 update listener（`__init__.py:209-210` 就是 `async_reload`），在 setup 过程中写会造成 reload 循环。

## 适用范围

- `__init__.py` 的身份解析/迁移/清理
- `config_flow.py` 的 unique_id 策略
- 所有实体的 `unique_id` 与 `DeviceInfo`
- 任何新增/删除实体的版本升级

## 规则

- 实体 `unique_id` 必须以 `base_id` 起头，后缀稳定且全局唯一。
- 设备 `identifiers` 必须用 `(DOMAIN, base_id)`，禁止用 `host:port`。
- 身份来源变更时，必须提供一次性迁移函数，且**幂等**（`if old_id == new_id: return`，`__init__.py:83-84`）。
- 删除/替换任何实体时，必须把旧 `unique_id` 加入清理表，并附回归测试（现有表见 `__init__.py:146-150`）。
- 迁移/清理使用 `er.async_entries_for_config_entry` + `dr.async_entries_for_config_entry`，**不要用** `device_registry.async_get_device(identifiers=...)`（已弃用，注释记录 HA 2027.8.0 移除，`__init__.py:106-109`；回归测试 `tests/test_regressions.py:121-139` 用 AST 拦截）。
- `async_get_entity_id` 的参数顺序是 `(domain, platform, unique_id)`，历史上传反导致清理静默失效（回归测试 `tests/test_regressions.py:141-171` 用 AST 锁死）。

## 反模式 / 禁止项

- ❌ 用 `host:port` 作为实体 `unique_id` 或设备 identifier。
- ❌ 把序列号写进 `entry.data`（会触发 reload 循环）。
- ❌ 删除实体时不清理注册表（留下永久 unavailable 孤儿）。
- ❌ 迁移时用 `async_get_device(identifiers=...)`（已弃用且签名假设脆弱）。
- ❌ ⚠️ **现存偏差**：config entry 自身的 `unique_id` 仍是 `host:port`（`config_flow.py:47-49`），与实体身份不同源。因此"改 IP 后重新添加（而非重新配置）"会产生第二个 entry 并撞 unique_id（详见 `architecture_views/risk.md` R-16）。改动配置流时应修正此点。

## 修改检查清单

- [ ] 新实体 `unique_id` 是否 `{base_id}-...` 形式？
- [ ] 若删除了任何实体，清理表 + 回归测试是否同步？
- [ ] 身份迁移函数是否幂等？
- [ ] 是否避开了 `async_get_device(identifiers=...)`？
- [ ] 序列号读取失败（待机）时是否有 `host:port` 兜底且不再改变？
- [ ] 改 IP 场景走"重新配置"（`config_flow.py:68-105`）而非重新添加，文档是否说清？
