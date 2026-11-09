---
project: "ha-epson-ef-projector"
type: risk
description: "entry-core 模块内风险：装配失败路径的资源不归还与半装配残留、身份迁移只认当前地址导致的历史身份盲区与孤儿漏清、迁移对未知版本静默放行、服务装卸的 TOCTOU、Store 身份文件无生命周期、以及 setup 首轮必然触发全量慢诊断。全局级风险（身份双源 R-16、服务无 admin R-23、轮询积压 R-09、HA 零覆盖）见 architecture_views/risk.md，本文件不重复。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
id: entry-core
---

# 模块架构风险分析

> 本模板聚焦**单一模块**范围内的风险：故障域限定于 `__init__.py` / `config_flow.py` / `services.py` / `diagnostics.py` 四个文件。
> 全局级（跨模块、部署拓扑、SPOF 等）风险登记在 `architecture_views/risk.md`，本文件仅在边界处引用其编号（R-xx），不重复条目。

## 模块内稳定性与并发风险

|风险 ID|场景|触发条件|影响范围（本模块内）|概率|严重度|优先级|当前缓解|建议缓解|状态|
|-|-|-|-|-|-|-|-|-|-|
|RISK-EC-01|服务装卸 TOCTOU|条目 A unload 与条目 B setup 并发；A 执行 `if not hass.data[DOMAIN]`（`__init__.py:218`）时 B 尚未写入 `setdefault`（`:195`）|两个服务被注销，而 B 已上线 → 该条目的 `send_key`/`raw_request` 调用全部落到 `"No loaded epson_ef entry found"`|低|中|P2|判定与注销之间无锁；B 的下一次 reload 会重新注册（`services.py:40-41`）|把「判空 + 注销」包进 `async_create_task` 或加 domain 级锁；或改由 `hass.data` 引用计数|开放|
|RISK-EC-02|装配第 9 步失败后上下文残留|平台转发（`__init__.py:203`）任一平台 `async_setup_entry` 抛异常|`hass.data[DOMAIN][entry_id]` 已发布（`:195`）但 setup 返回失败；HA 视为未加载，条目上下文成为无人认领的悬挂 dict，含打开中的 aiohttp session|低|中|P2|无清理；`api.close()` 未被调用（第 9 步无 try/except）|用 `try/except` 包住转发段，异常时 pop 上下文并 `api.close()`|开放|
|RISK-EC-03|unload 直接下标取 hass.data|`hass.data[DOMAIN]` 已被前一次失败流程摘除，而 HA 仍调用 `async_unload_entry`|`__init__.py:216` 抛 `KeyError`，unload 崩溃；session 不关闭|低|低|P3|`unload_ok` 前置判断（`:215`）只保证平台卸载成功，不保证域键存在|改 `hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)`|开放|
|RISK-EC-04|装配失败不归还传输资源|身份解析（`:178`）、注册表迁移（`:180`）、遗留清理（`:181`）、平台转发（`:203`）任一步抛非 `ConfigEntryNotReady` 异常|已构造的 `aiohttp.ClientSession` 永不关闭（`api.close()` 仅在 `:175`、`:192` 两条出口）|中|中|P1|仅两条失败出口有 `close()`；其中 `:175` 还是死代码（见 RISK-EC-06）|把 `api` 的释放改为 `entry.async_on_unload(api.close)`，覆盖全部路径|开放|
|RISK-EC-05|注册表同步操作阻塞事件循环|实体/设备注册表规模大（多集成共存）时，`:87`、`:110`、`:160` 三处同步遍历|setup 期间事件循环停顿；迁移 + 清理各遍历一遍全条目|低|低|P3|仅在 setup 阶段执行，非热路径|可接受；若条目实体数增长到数百再考虑批量 API|开放|

## 跨模块依赖风险

> 仅记录与本模块边界相关的依赖风险（强依赖失效、契约漂移），与 `boundaries.md` 上下游表对应。身份双源（config entry unique_id vs 实体 serial）已登记为全局 R-16，此处只记录本模块侧的成因。

|风险 ID|依赖方向|依赖对象|失效/漂移场景|影响|当前保障|改进方案|
|-|-|-|-|-|-|-|
|RISK-EC-06|本模块→pjctl-transport|`EpsonApi.get_product_name` 的异常契约|该方法内部 `except EpsonApiError: return None`（`api.py:295-300`），而调用方 `__init__.py:172-176` 期待它抛错|setup 的"可达性门"是死代码；配置流的 `cannot_connect` 无法区分「解析失败/超时/深待机/不可达」，用户得到同一句"连接失败"|无；`config_flow.py:41-42` 与 `:79-80` 同样依赖 None 语义|二选一：让 `get_product_name` 抛错（调用方已写好 try），或在 `__init__.py` 删掉该 try 并注释真实门在首刷；配置流改用 `probe_state` 区分失败原因|
|RISK-EC-07|本模块→state-coordinator|`Ef50Coordinator.async_config_entry_first_refresh` 的首轮开销|首刷固定执行完整慢诊断（`coordinator.py:59` 的 `_poll_count == 1`）|每次 HA 重启 / 每次 reload 都必然付出 33–35 次串行 HTTP（含 `sleep(0.2)`），setup 时长可达分钟级|无超时预算；HA 侧无 setup 超时保护|给首刷加总预算（`asyncio.timeout`），或让首刷只做快探针、慢诊断留给后续轮次|
|RISK-EC-08|本模块→HA 注册表 API|`async_get_entity_id` 签名与 `async_get_device(identifiers=...)` 弃用|前者历史上实参传反（静默失效）；后者 HA 2027.8.0 移除|孤儿实体长期残留 / 未来 HA 升级直接报错|AST 静态测试（`tests/test_regressions.py:121-171`）锁死两处用法|保持测试；迁移逻辑只允许用 `async_entries_for_config_entry`|
|RISK-EC-09|本模块→HA Store|身份文件的生命周期|全仓库不存在 `async_remove_entry`（`grep -rn async_remove_entry custom_components/` 无命中）|删除 config entry 后 `.storage/epson_ef.<entry_id>.identity` 永久残留；重新添加同一设备会生成新 entry_id → 新文件，旧文件成为垃圾|无|实现 `async_remove_entry` 删除 Store 文件|

## 模块内性能瓶颈风险

|瓶颈 ID|位置（本模块内）|当前指标|阈值|触发条件|影响|突破方案|预计工作量|
|-|-|-|-|-|-|-|-|
|RISK-EC-10|`async_setup_entry` 串行步骤|固定 3 次网络往返（product name `:173`、serial `:60`、首刷 `:190`）+ 2 次 Store I/O（`:56`、`:62-63` 或 `:73-74`）|HA setup 无硬超时，但 reload 期间实体不可用|每次重启/reload；待机时 serial 命令可能超时 10s（`api.py:160`）|setup 变慢直接推迟实体上线；待机设备首次添加尤其明显（序列号取不到 → 先等超时再钉地址）|serial 探测用短超时（现无 `timeout=` 参数，`__init__.py:60` 走默认 10s）；或先钉地址后台补探序列号|小|
|RISK-EC-11|`_async_remove_legacy_entities` 遍历|每次 setup 遍历本条目全部实体（`:160`）|实体数 58（`architecture_views/risk.md` 容量表）|每次 setup|O(n) 同步遍历，58 条可忽略|无需优化|—|

## 模块内数据一致性风险

|风险 ID|场景|一致性级别|当前保障|潜在问题|概率|影响|改进方案|
|-|-|-|-|-|-|-|-|
|RISK-EC-12|迁移只认「当前 host:port」作旧身份|最终一致（无收敛保证）|`legacy_id = f"{host}:{port}"` 由调用方恒传当前地址（`__init__.py:179`），迁移函数不查 Store|若历史身份是 Store 里钉死的旧地址（`stored["identity"]`），本次 setup 的 `old_id` 与之不符 → 前缀不匹配、实体保持旧 unique_id，而平台会用新 `base_id` 另建一套实体 → 同一设备出现两套实体（一套永久 unavailable）|中|高|迁移前先读 Store 的 `identity` 作为候选 `old_id`，对 `{pinned, host:port}` 逐个尝试迁移|
|RISK-EC-13|遗留清理的历史前缀固化|最终一致|清理时同时匹配 `{base_id}-{suffix}` 与 `{host}:{port}-{suffix}`（`__init__.py:154-155`）|同样只取当前 host:port；换过址且未取到序列号的用户，其历史孤儿（旧地址前缀）不会被清理|中|中|与 RISK-EC-12 同解：把 Store 中的历史 identity 一并加入 `wanted` 集合|
|RISK-EC-14|迁移冲突静默降级|无一致性保证|`except ValueError` → warning 并跳过（`:97-103`、`:117-120`）|部分实体迁移成功、部分保持旧 identity，注册表进入混合状态；调用方无从得知（函数返回 None），上层不会告警也不重试|中|中|返回迁移结果（成功/冲突计数），setup 时若有冲突记 warning 汇总|
|RISK-EC-15|条目版本迁移对未知版本放行|无|仅 `if entry.version == 1`（`:33`），其余一律 `return True`（`:39`）|v3+ 条目（未来版本降级、或测试残留）被当作已迁移，随后用 v2 语义解释 v3 数据，可能写出错误 host/port 或丢弃新字段|低|中|补 `else: return False`（HA 会拒绝加载并提示版本不支持）|

**失败场景分析模板**

```
正常路径（首次添加，设备唤醒）：
  user flow 探测 product → 创建 entry(v2, unique_id=host:port) → setup
  → Store 空 → CONFIG_GET_PJ_SNO 返回序列号 → 落库 {serial}
  → base_id=serial → 迁移(旧=host:port 无实体, 空转) → 清理(表内 5 条, 无命中)
  → 首刷成功 → 发布 hass.data → 7 平台用 serial 前缀建 58 实体

异常路径 1（待机中首次添加）：
  user flow 探测成功（product name 在待机也可读）→ 创建 entry
  → CONFIG_GET_PJ_SNO 待机 FAIL → serial=None → 钉死 {identity: "host:port"}
  → base_id = "192.168.x.x:8080"（地址成了身份）
  → 之后设备换址：base_id 仍是旧地址串（不再变），实体 unique_id 前缀与地址脱钩但仍不是序列号
  → 若某天序列号可读：Store 已有 identity 但无 serial → 再走一次 serial 探测 → 落库 serial
     → base_id 变 serial → 迁移(旧=当前 host:port ≠ 旧地址) → 前缀不匹配 → 实体不迁移（RISK-EC-12）

异常路径 2（3.5.6 → 3.10.0 升级）：
  旧版本留下 switch.setting-refresh / button.button-engine_refresh
  → 新版本 setup → 清理表命中 → async_remove 删除 → 无孤儿
  → 但若这两个实体的 unique_id 前缀是更早的地址（非当前 host:port）→ 不命中 → 孤儿残留（RISK-EC-13）

异常路径 3（迁移目标已被占用）：
  用户先"重新添加"同一设备（新 entry，同序列号）→ 两个 entry 争抢同一 base_id
  → 第二个 entry setup 时 async_update_entity 抛 ValueError → warning → 该实体保持旧 identity
  → 两套实体并在（与全局 R-16 同源，本模块侧表现为静默降级）
```

## 模块可用性风险

|风险 ID|故障域（本模块内）|组件|当前可用性|目标可用性|SPOF|冗余策略|故障恢复策略|RTO / RPO|
|-|-|-|-|-|-|-|-|-|
|RISK-EC-16|身份文件损坏/不可读|HA Store（`.storage/epson_ef.<id>.identity`）|`async_load` 无异常保护（`__init__.py:56`）|—|是（每条目一个身份文件）|无|Store 解析异常直接冒到 setup → 条目加载失败，无降级到"重算身份"的路径|需人工删除/修复该 JSON 文件后 reload|
|RISK-EC-17|序列号读取失败|`CONFIG_GET_PJ_SNO` params="1"（待机可能 FAIL，`api.py:302-315`）|返回 None 即兜底钉地址|—|否（有兜底）|地址兜底|兜底后身份不再是序列号，README 承诺的"实体与历史保持不变"在换址后失效|无需干预，但身份语义降级|
|RISK-EC-18|诊断导出时条目未加载|`diagnostics.py:16` 用 `.get(DOMAIN, {}).get(entry_id, {})`|两段全 None 而非报错|—|否|有（get 链）|下载到空载荷，无提示|低|
|RISK-EC-19|无自动发现/重新认证入口|`config_flow.py` 只有 user 与 reconfigure；无 `async_step_zeroconf`/`dhcp`/`import`/`reauth`（grep 无命中）|IP 变更必须用户手工进入"重新配置"|—|是|无|纯人工|RTO=人工分钟级|

## 模块内技术债务

|区域（本模块）|债务类型|原因|严重度|偿还建议|
|-|-|-|-|-|
|`__init__.py:172-176`|代码债|依赖 `get_product_name` 抛错的死分支；与 `api.py:295-300` 的实现直接矛盾|中等|删除该 try 并注释真实门在首刷，或改 `get_product_name` 抛出|
|`__init__.py:195-201`|设计债|`hass.data` 五键字典无类型（非 `TypedDict`/dataclass），9 处消费靠约定|中等|定义 `RuntimeContext(TypedDict)`，各平台 `cast` 后取值|
|`__init__.py:179`|设计债|`legacy_id` 硬编码为当前 `host:port`，把"上一个身份是什么"的知识放在了调用方而非 Store|中等|身份解析函数同时返回"候选旧身份列表"（见 RISK-EC-12）|
|`__init__.py:203-205`|代码债|平台转发 / 服务注册 / 监听挂载三步无 try/except，与前面步骤的防御风格不一致|中等|统一失败出口（见 RISK-EC-02、RISK-EC-04）|
|`__init__.py:33-39`|代码债|迁移无未知版本守卫|中等|补 `return False`|
|`__init__.py:146-150`|设计债|清理表是手工维护的枚举，与实体定义表（`WRITABLE_SWITCHES` 等）无机械关联；测试只断言 5 条表项中的 4 条存在（`serial` 未断言），不检测"新删除的实体是否漏登记"|中等|把「被删除实体」的登记做进平台模块（如 `REMOVED_ENTITY_SUFFIXES` 常量），由测试聚合校验|
|`services.py:65-66`|代码债|`except Exception` 把编程错误与设备错误混同为 `{"error": str(err)}`，且带 `noqa: BLE001` 显式豁免了 lint|中等|收窄到 `EpsonApiError`，其余记 `_LOGGER.exception` 后仍发事件但标注 `"fatal": True`|
|`services.py:67-70`|设计债|事件载荷无 correlation id / 无调用者标识，并发调用无法与响应配对；`entity_id` 原样回传的是 list 形态（未像 handler 内部那样取 [0]）|轻微|载荷加 `call.context.id` 或自增 request_id；`entity_id` 与解析时使用同形态|
|`services.py:40-41`|代码债|幂等探测只查 `send_key`，与注册两个服务的事实不匹配|轻微|改为分别检查两个服务名|
|`diagnostics.py:22`|设计债|`dict(entry.data)` 全量导出，无字段白名单；安全性完全依赖"凭据不进 entry"这一外部前提|轻微|改为显式白名单键（host/port/name/poll_interval）|
|`diagnostics.py:35-37`|代码债|`getattr(api, "_host"/"_port"/"_auth")` 读传输层私有字段，重命名会静默退化为 None|轻微|传输层暴露 `describe()` 只读方法|
|`config_flow.py:23`、`:108`|代码债|类名 `Ef50*` 与 domain `epson_ef`、覆盖 HB7x 全系脱节（与全局命名债同源，本模块内影响新增机型时的命名一致性）|轻微|新代码用 `Epson*`|
|`config_flow.py:58-59`|代码债|host 只约束 `str`、port 只约束 `int`，无 IP 格式/端口范围校验；空 host 也能通过 schema|轻微|`vol.All(str, vol.Match(...))` 或 `cv.string` + 非空校验；port 加 `vol.Range(min=1, max=65535)`|
|`config_flow.py:26`|文档债|无 `MINOR_VERSION`，但 `async_migrate_entry` 未按 minor 处理；未来补 minor 迁移时需注意 HA 的版本比较语义|轻微|保持 version 单维；若引入 minor 需同步迁移函数|
|本模块整体|测试债|4 个文件的运行期行为零自动化覆盖（环境无 `homeassistant`，`import homeassistant` → `ModuleNotFoundError`）；现有测试只用 AST 校验源码字符串（`tests/test_regressions.py`）|严重|引入 `pytest-homeassistant-custom-component`，为 setup/unload/migrate/两条服务写最小装配测试|
