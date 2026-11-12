---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-entry-core-003
description: "setup 的步骤顺序是契约而非风格：身份解析 → 注册表迁移 → 遗留清理 → 协调器首刷 → 发布 hass.data → 转发平台。重排任何一步都会产生『用旧身份建新实体』或『实体先于状态源上线』的中间态；同时每条失败出口必须归还已获得的传输资源（session）。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
principle_category: 架构
principle_scope: module
---

# 装配顺序即契约，失败必须归还资源

## 原则详细描述

`async_setup_entry` 的 9 个步骤（`__init__.py:166-206`）不是任意排列，其中三步的顺序被身份治理强约束：

```
① 构造 api（:170） → ② 探测 product（:173） → ③ 解析身份（:178）
→ ④ 迁移注册表身份（:180） → ⑤ 清理遗留实体（:181）
→ ⑥ 解析轮询间隔（:184-187） → ⑦ 首刷（:190） → ⑧ 发布 hass.data（:195）
→ ⑨ 转发 7 平台（:203） + 注册服务（:204） + 挂监听（:205）
```

**为什么 ③④⑤ 必须在 ⑨ 之前**：平台模块在 `async_setup_entry` 里用 `data["base_id"]` 拼 `unique_id` 并建实体（`sensor.py:27-31`、`number.py:52-57`）。若迁移晚于平台转发，HA 会先用新身份创建一批"新实体"，再回头把旧实体改名——结果是同一设备出现两套实体。因此顺序是「先把注册表改对，再让平台照着新身份建」。

**为什么 ⑦ 在 ⑧⑨ 之前**：`hass.data` 一旦发布就成为 7 个平台的唯一数据源；首刷失败时若已转发平台，平台会在 `coordinator.data = None` 上建实体，随后 HA 又要回滚。代码把首刷放在发布之前，并在失败时 `api.close()` 后重抛 `ConfigEntryNotReady`（`:190-193`），使"未上线"成为一个干净状态。

**失败归还**：`EpsonApi` 持有一个懒创建的 aiohttp session（`api.py:87-97`）。已构造的 api 必须在失败出口 `close()`，当前只做到两处（`:175`、`:192`），其余出口存在泄漏（见 `risk.md` RISK-EC-04）。

## 为什么要这样

- **HA 的生命周期回调不允许"部分成功"**：`async_setup_entry` 返回 False / 抛异常，HA 认为条目未加载；但它不会替你回收你在失败前写入 `hass.data` 的对象，也不会关闭你建的 session。
- **注册表是全局共享的**：实体/设备注册表跨集成共享，迁移逻辑写错顺序造成的"两套实体"无法靠 reload 自愈——只会越来越多。
- **顺序错误的表现是静默的**：先建实体再迁移，日志里只有若干条 warning（`__init__.py:98-103`），不会报错，用户只看到"历史断了"。

## 适用范围

- `custom_components/epson_ef/__init__.py:166-221` 的 setup / unload 全流程
- 任何新增到 setup 阶段的装配步骤（新平台、新注册表治理、新服务）
- config flow 中创建条目后的首轮 setup（`config_flow.py:51-53`）同样走这条顺序

## 规则

- 身份解析（`:178`）→ 迁移（`:180`）→ 清理（`:181`）→ 平台转发（`:203`）的相对顺序不得改变。
- 新增步骤时先问「它读/写注册表吗」：写注册表的必须在 ⑨ 之前，只读 `hass.data` 的必须在 ⑧ 之后。
- 每条 setup 失败出口都必须 `await api.close()`；首选做法是 `entry.async_on_unload(api.close)` 一次性覆盖所有路径，而不是逐个 except 补。
- `hass.data` 的发布必须尽量靠后（当前在 ⑧），使前面的失败不留下悬挂上下文（第 ⑨ 步失败的残留见 `risk.md` RISK-EC-02）。
- unload 必须是 setup 的严格逆序：平台卸载 → pop 上下文 → 关 session → 末条目注销服务（`:214-220`）。

## 反模式 / 禁止项

- ❌ 在平台转发之后才做身份迁移或遗留清理。
- ❌ 首刷之前发布 `hass.data` 或转发平台。
- ❌ `except Exception: return False` 式吞异常后不关 session、不 pop 上下文。
- ❌ 在 unload 里先关 session 再卸载平台（卸载期间的实体调用会打到已关 session）。
- ❌ 依赖 `get_product_name` 抛错来做可达性门（`api.py:295-300` 已吞异常，`__init__.py:172-176` 因此是死代码）。

## 修改检查清单

- [ ] 新增步骤是否插在正确的相位（注册表写 → 发布 → 平台）？
- [ ] 新增的失败出口是否归还了 session？
- [ ] 第 ⑨ 步失败时 `hass.data` 是否被清理（当前未清理）？
- [ ] unload 顺序是否仍是 setup 的逆序？
- [ ] 若改动 `PLATFORMS`，是否确认新平台只依赖已发布的 `hass.data` 键？
