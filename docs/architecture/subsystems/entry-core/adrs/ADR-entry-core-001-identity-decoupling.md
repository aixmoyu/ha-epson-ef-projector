---
project: "ha-epson-ef-projector"
type: adr
id: ADR-entry-core-001
description: "设备身份从『始终等于 host:port』改为『固件序列号优先、地址兜底』，并把身份持久化到 HA Store 而非 config entry。破除写 entry 触发 update listener 的 reload 循环，代价是多一份 .storage 文件与一套注册表迁移逻辑。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "设备身份与网络地址解耦，持久化到 Store"
date: 2026-11-07
code_refs:
  - "custom_components/epson_ef/__init__.py:42-76"
  - "custom_components/epson_ef/__init__.py:79-120"
  - "custom_components/epson_ef/__init__.py:166-206"
  - "custom_components/epson_ef/api.py:302-315"
  - "custom_components/epson_ef/media_player.py:85-93"
  - "custom_components/epson_ef/sensor.py:40-47"
  - "README.md:87"
  - "README.md:156"
---

# ADR-001: 设备身份与网络地址解耦，持久化到 Store

## 状态

accepted（实现于 3.10.0；config entry 的 unique_id 仍保留 `host:port`，属本决策的未完成部分，见「实施要点」）

## 1. 背景

**业务背景**：投影仪通过 DHCP 获取局域网地址。用户网络重启、租约到期、换路由器都会让地址变化；集成提供 `reconfigure` 流供用户改地址（`config_flow.py:68-105`），README 明确承诺「IP 变化可通过『重新配置』更新，实体与历史保持不变（序列号作为稳定身份）」（`README.md:87`、`:156`）。

**技术背景**：早期实现把 `host:port` 直接当作 `base_id`，实体 `unique_id` 为 `f"{base_id}-{后缀}"`、设备 `identifier` 为 `(DOMAIN, base_id)`（`sensor.py:44-46`、`media_player.py:88-89`）。一旦地址变化，即使走 reconfigure 成功改了连接信息，`base_id` 仍是新地址，所有实体的 `unique_id` 随之改变 → HA 视为一批全新实体。

**核心问题**：在设备地址会漂移（DHCP）的前提下，如何让实体 `unique_id` 与设备 `identifier` 保持稳定，使历史数据、自动化引用、仪表盘在改地址后不失效？

**约束条件**：

- **不能写 config entry**：本模块注册了 `entry.add_update_listener(_async_update_listener)`，监听器只做 `async_reload`（`__init__.py:205`、`:209-210`）。在 setup 过程中写 entry 会让 HA 认为配置变化 → 触发 reload → 再 setup → 再写，形成循环。代码注释直接写明这一理由（`:48-51`）。
- **序列号不一定可得**：`CONFIG_GET_PJ_SNO` 需要 params=`"1"`，且待机状态下可能 FAIL；`get_serial_number` 在失败时返回 None（`api.py:302-315`）。
- **必须兼容存量安装**：升级用户的实体 `unique_id` 已按 `host:port` 形态存在，改身份必须同时改注册表，否则历史照样断。

## 2. 决策驱动因素（Decision Drivers）

1. **实体历史连续性**（重要性：高）：`unique_id` 一旦变化，HA 会新建实体，历史统计、`recorder` 数据、自动化里的 `entity_id` 引用全部断档。
2. **不得引入 reload 循环**（重要性：高）：身份写入必须是"不惊动 HA 配置条目"的操作。
3. **待机/不可达时也必须能 setup**（重要性：中）：设备可能处于深待机，序列号命令会失败；集成不能因此无法加载。
4. **存量升级零手工**（重要性：中）：不能要求用户删除重建条目。
5. **实现复杂度**（重要性：低）：允许有一定一次性复杂度（Store + 迁移函数），但不愿引入"身份状态机"。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|Option A|身份 = 序列号（取不到则钉死首个 host:port），存 HA Store|实体/设备身份与地址彻底解耦；不写 entry 故无 reload 循环；序列号可得时最稳定|多一份 `.storage` 文件与生命周期（删除条目后残留）；需要注册表迁移逻辑；待机首次添加会先钉地址|钉死后序列号后来可得，会出现一次身份切换（迁移只认当前地址，可能漏迁）|
|Option B|身份 = 序列号，写进 `entry.data["serial"]`|数据集中在一处，随 entry 备份/恢复；无需 Store|写 entry 触发 update listener → reload 循环，必须额外加"静默写"机制或移除监听器|高：任何绕过都会造成 reload 风暴|
|Option C|身份 = config entry 的 `entry_id`（HA 分配，天然稳定）|最简单，永不漂移；零迁移逻辑|`entry_id` 在删除重建后变化；它标识"这次配置"而非"这台设备"；同一设备重新添加会产生全新身份，与"设备身份"语义不符|中：换 HA 配置重建/恢复备份时身份丢失|
|Option D（现状基线）|身份 = `host:port`（地址即身份）|零成本，无额外存储|DHCP 换址即身份变化，reconfigure 后实体全部重建，README 承诺不成立|高：已实际造成历史断档|

### Option A：序列号优先 + Store 持久化（已采纳）

`_async_get_base_id` 依次尝试：`Store.serial` → `CONFIG_GET_PJ_SNO(params="1")` → `Store.identity`（钉死地址） → `f"{host}:{port}"`，命中即落库（`__init__.py:56-76`）。身份变更时 `_async_migrate_identity` 批量改写实体 `unique_id` 前缀与设备 `identifiers`（`:79-120`）。

### Option B：写进 config entry

把 `serial` 存为 `entry.data` 的一个键。看似更"正统"（配置与身份同生命周期），但与已有的 update listener 直接冲突：`_async_update_listener` 无条件 `async_reload`（`:209-210`），写 entry 立刻触发自身重载。要么删掉监听器（则选项/reconfigure 改动不再自动生效，`config_flow.py:87-89` 的 `async_update_and_abort` 依赖它），要么实现"静默写 entry"的旁路——两条路都增加了比 Store 更重的耦合。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**（对照驱动因素逐条）：

- 驱动 1（历史连续性）：序列号是设备的物理属性，换址不改变；配合一次性迁移，存量实体也能平滑改名（`:79-120`）。
- 驱动 2（无 reload 循环）：Store 完全在 HA 配置体系之外，写入不通知任何监听器；这正是代码注释给出的理由（`:48-51`）。
- 驱动 3（待机可 setup）：`get_serial_number` 失败返回 None，逻辑继续走地址兜底（`api.py:311-315`、`__init__.py:68-76`），不会让 setup 失败。
- 驱动 4（零手工升级）：迁移在每次 setup 无条件执行，幂等（`if old_id == new_id: return`，`:83-84`）。
- 驱动 5（复杂度）：只增加一个 Store 读写与两个纯函数，未引入状态机。

**被否决方案的关键否决点**：

- Option B 否决于驱动 2（与既有 update listener 互斥）。
- Option C 否决于语义：`entry_id` 标识配置实例而非设备，同一设备重新添加身份会变，与"身份与地址解耦"的目标偏离。
- Option D 否决于已发生的故障（实体历史断档）。

### 4.1 实施要点

1. 身份解析集中在 `_async_get_base_id`，返回 `(base_id, serial)` 二元组；序列号路径返回 `(serial, serial)`，兜底路径返回 `(pinned, None)`（`__init__.py:65-76`）。
2. Store 的 key 为 `f"{DOMAIN}.{entry.entry_id}.identity"`、version=1（`:53-55`）；内容为 `{"serial": ...}` 或 `{"identity": ...}`。
3. 迁移函数幂等：`if old_id == new_id: return`（`:83-84`）；按前缀替换（`uid.startswith(f"{old_id}-")` 保留后缀，`:91-92`）；冲突降级 warning（`:97-103`）。
4. 设备标识迁移用 `dr.async_entries_for_config_entry`，禁用已弃用的 `async_get_device(identifiers=...)`（`:106-116`，AST 测试 `tests/test_regressions.py:121-138`）。
5. 迁移/清理/身份解析三步在 setup 中位于平台转发之前（`:178-181` vs `:203`）。

### 4.2 已知未完成部分

- **config entry 的 unique_id 仍是 `host:port`**（`config_flow.py:47-49`），与实体身份不同源。因此"改了 IP 后重新添加（而非重新配置）"会造出第二个条目，两个条目争抢同一 `base_id` → 迁移时 `ValueError` → 静默降级（全局 `architecture_views/risk.md` R-16）。
- **迁移只认当前地址作旧身份**（`__init__.py:179` 恒传 `f"{host}:{port}"`），当历史身份是 Store 里钉死的更早地址时前缀不匹配（本模块 `risk.md` RISK-EC-12）。
- **无 `async_remove_entry`**，删除条目后 `.storage` 身份文件残留（`risk.md` RISK-EC-09）。

## 备注

- 全局层对应原则：`architecture_views/guidelines/principle-global-006-identity-over-address.md`（本 ADR 是其模块级展开）。
- 序列号子索引 `params="1"` 的固件事实来源：`api.py:302-310` 注释、`docs/reference/protocol.md:82`。
