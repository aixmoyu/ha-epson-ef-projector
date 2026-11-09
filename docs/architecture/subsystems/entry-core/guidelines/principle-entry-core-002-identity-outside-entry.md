---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-entry-core-002
description: "设备身份必须写在 config entry 之外（HA Store），因为身份是物理事实而 entry 是用户配置；写 entry 会经 update listener 触发 reload。身份优先级为 serial → 已钉死的 host:port → 当前 host:port，且一旦落库不再随地址变化。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
principle_category: 架构
principle_scope: module
---

# 身份存 Store，不存 config entry

## 原则详细描述

`_async_get_base_id` 把设备身份（序列号，或序列号不可得时钉死的 `host:port`）写入 `Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.identity")`，并返回 `(base_id, serial)` 二元组（`__init__.py:42-76`）。决策顺序是：

```
Store.serial → 固件 CONFIG_GET_PJ_SNO(params="1") → Store.identity（钉死） → f"{host}:{port}"
```

三点含义：

1. **记忆优先于设备真值**：`stored.get("serial")` 命中即不再问设备（`:58-64`）。序列号一旦落库就是最终答案。
2. **兜底要钉死**：取不到序列号时把首次见到的 `host:port` 写进 Store（`:72-75`），之后地址再变也不改 `base_id`——否则"DHCP 换址"会同时改变身份，与解耦目标自相矛盾。
3. **entry 里绝不出现身份**：`entry.data` 只有 host/port/name/poll_interval 四键（`config_flow.py:56-65`），迁移后的 v2 也如此（`__init__.py:33-38`）。

## 为什么要这样

- **写 entry 会自触发 reload**：本模块注册的 update listener 就是 `async_reload(entry.entry_id)`（`__init__.py:209-210`）。若在 setup 中把序列号写进 `entry.data`，HA 会认为配置变了 → 触发 reload → 重新 setup → 再写 → 无限循环。这条理由直接写在代码注释里（`__init__.py:48-51`）。
- **身份是设备的，配置是用户的**：用户改的是"连到哪"，设备回答的是"我是谁"。把两者放在同一个可变容器里，任何一次地址变更都会波及身份。
- **Store 的 key 绑定 entry_id**：`f"{DOMAIN}.{entry.entry_id}.identity"`（`:54`）保证多设备各自独立，同时明确"这个身份属于这个条目"，删除条目后文件残留属已知缺口（见 `risk.md` RISK-EC-09）。

## 适用范围

- `custom_components/epson_ef/__init__.py:42-76`（身份解析）、`:79-163`（依赖身份的注册表治理）
- 所有实体的 `unique_id`（`{base_id}-{后缀}`：`sensor.py:44`、`number.py:90`、`switch.py:78`、`button.py:122`、`select.py:75`）
- 所有实体的 `DeviceInfo.identifiers`（`(DOMAIN, base_id)`：`media_player.py:89`、`sensor.py:46`、`number.py:93`）

## 规则

- 身份来源只能是 `_async_get_base_id` 的返回值；任何模块不得自行拼 `host:port` 当身份。
- 新的身份要素（如固件机型串）要进 Store 时，必须同时定义"旧身份 → 新身份"的迁移与门禁（参见 `principle-entry-core-001`）。
- 身份落库必须立即 `async_save(dict(stored))`（`:63`、`:74`），不能延迟到 setup 结束——setup 中途失败时身份不该丢。
- 删除或改型实体时，清理表要同时覆盖「新身份前缀」与「历史地址前缀」（`__init__.py:154-155`）。
- ⚠️ **现存违例（须在改动相关代码时修正）**：config entry 自身的 `unique_id` 仍是 `host:port`（`config_flow.py:47-49`），导致"改 IP 后重新添加"而非"重新配置"会造出第二个条目（全局 `architecture_views/risk.md` R-16）。

## 反模式 / 禁止项

- ❌ 把序列号写进 `entry.data` / `entry.options`。
- ❌ 在实体里用 `f"{host}:{port}"` 作为 `unique_id` 或 `identifier`。
- ❌ 每次 setup 都重新读序列号并覆盖 Store（会让身份随设备状态抖动；待机时读不到就退回地址，历史断档）。
- ❌ 在 reconfigure 流里改 entry 的 `unique_id`（`async_update_and_abort(entry, data_updates=...)` 只改 data，`config_flow.py:87-89`）。

## 修改检查清单

- [ ] 新增身份要素是否走 Store 且带迁移路径？
- [ ] 是否有任何代码用 `host:port` 当 `unique_id`/`identifier`（除清理表的历史前缀）？
- [ ] 身份落库是否与解析在同一步完成（失败不留半状态）？
- [ ] 若改了 Store 的 key 或结构，是否提供了旧 key 的读取兼容？
- [ ] reconfigure 后 `base_id` 是否保持不变（序列号路径下应不变）？
