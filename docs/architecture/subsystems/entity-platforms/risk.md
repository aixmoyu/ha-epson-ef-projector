---
project: "ha-epson-ef-projector"
type: risk
description: entity-platforms 的风险集中在四处：①写通道分裂（40 个实体绕过 api.py 语义方法直接拼命令串），使同一份协议知识存在两份编码；②乐观写回填共享字典，无锁无版本，三通道增益与对焦闭环在并发下会丢更新/交错驱动；③无读通路的 7 个实体只靠本地记忆，静音甚至未声明 assumed_state，用户无法识别假设值；④unique_id 四套约定 + settings 扁平键 + legacy 清理表三者叠成的命名耦合，改名或迁移任一处都可能删掉活着的实体。跨模块级风险（固件单线程挂起、慢轮超时预算）见 architecture_views/risk.md，本文件仅在边界处引用。
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
---

# 模块架构风险分析

> 本模板聚焦**单一模块**范围内的风险：故障域限定于本模块 `code_mapping`，跨模块风险仅记录与本模块边界（见 `boundaries.md`）相关的依赖项。
> 全局级（跨模块、部署拓扑、SPOF 等）风险登记在 `architecture_views/risk.md`，本文件不重复其条目，仅在本模块边界处引用。
> 概率/严重度取值：高/中/低；优先级 P0 阻断、P1 尽快、P2 排期、P3 观察。

## 模块内稳定性与并发风险

|风险 ID|场景|触发条件|影响范围（本模块内）|概率|严重度|优先级|当前缓解|建议缓解|状态|
|-|-|-|-|-|-|-|-|-|-|
|R-EP-01|色彩增益三通道"读-改-写"丢更新|用户/自动化几乎同时设置 R 与 G（或 B）|`Ef50ColorGainNumber` ×3；结果是某个通道的写入被另一通道基于旧三元组的整串覆盖|中|中|P1|无。写前读 `_triple()`（`number.py:381`），但读与写之间无锁、无版本校验；写完回填 `diag["color_gain"]`（`:392-393`）也只覆盖本地缓存|三通道共享一把类级 `asyncio.Lock`；或改为"写后立即 `get_color_gain()` 回读再比较"|未缓解|
|R-EP-02|对焦闭环可被并发重入打断|两个协程同时设置对焦目标（前端拖两次、自动化 + 手动）|`Ef50FocusNumber` + 固件对焦马达；两套 MAX/MIN…OFF 交错驱动，最终位置不可预测|低|高|P0|无锁：`goto_focus` 循环内无 `async with`（`api.py:561-583`），实体 setter 亦无锁（`number.py:282-301`）。对照电源路径有锁（`media_player.py:73`）|给 `Ef50FocusNumber` 加实例锁；`goto_focus` 入口加 busy 拒绝|未缓解|
|R-EP-03|取消路径上马达可能不停|HA 关停/任务被二次取消|对焦马达（可能顶到限位）|低|高|P1|`try/finally` 保证发 `OFF`（`api.py:573-582`），但 `await focus_move_stop()` 未 `asyncio.shield`；finally 内 `sleep` 被取消时 OFF 请求本身也可能被取消|`asyncio.shield(focus_move_stop())`|部分缓解|
|R-EP-04|共享字典就地 mutate|实体写与协调器 merge 交错（事件循环内仍是同一 dict 对象）|全部读 `settings`/`diag` 的实体|中|中|P1|无版本号、无快照替换；**18 个实体实例经 8 处代码**直接改 `self.coordinator.data`（`number.py:129/216/297/392`、`switch.py:109`、`select.py:110/162`、`media_player.py:162`）|实体写改为"复制后整体替换"，或协调器改用不可变快照|未缓解|
|R-EP-05|电源锁只覆盖单一实体实例|`services.send_key(key="POWER")` 或 `raw_request` 与本实体 turn_on/off 并发|电源状态与设备真值分叉（盲 toggle 被翻两次）|中|中|P1|锁在 `__init__` 内创建（`media_player.py:73`），作用域=本实例；`services.py:48-54` 与 `services.py:64` 不经过它|把锁上提到 entry 级共享对象（`hass.data[DOMAIN][entry_id]["power_lock"]`）|未缓解|
|R-EP-06|按钮实体永不 unavailable，离线点击得到通用错误|设备断电时用户点 30 个按钮中任意一个|`Ef50Button` ×30；错误文案为 `按钮 {key} 执行失败: {err}`，未区分"不可达"与"命令被拒"|高|低|P2|有意设计（`button.py:132-134` 注释：保留按钮以便给出明确错误）|文案按 `EpsonApiUnavailable`/`CommandFailed` 分类，与 `media_player.py:142-149` 对齐|接受|
|R-EP-07|写-only 实体状态漂移且无提示（静音）|用户用实体遥控器静音/取消静音|`Ef50MediaPlayer.is_volume_muted`；界面显示与设备相反，且**没有** `assumed_state` 徽标|中|中|P1|仅 `RestoreEntity` 本地记忆（`media_player.py:75-81`）+ 目标态比对去重（`:212-213`）|增加 `assumed_state` 返回 True（与氛围灯三件套一致，`switch.py:166-169`）；或在属性里加 `mute_assumed: true`|未缓解|

## 跨模块依赖风险

|风险 ID|依赖方向|依赖对象|失效/漂移场景|影响|当前保障|改进方案|
|-|-|-|-|-|-|-|
|R-EP-08|本模块→state-coordinator|`coordinator.data` 的 8 个键（`state`/`light_hours`/`light_extra`/`light_measured_at`/`settings`/`diag`/`product`/`standby_mode`）|协调器重命名或删除任一键：实体静默显示 None / 属性消失（**无 KeyError、无日志**，因为全部走 `.get()`）|58 实体中 55 个受影响|无 schema 声明、无类型定义、无契约测试；键名以字符串字面量散落在 7 个文件|在 `const.py` 定义 `DATA_KEY_*` 常量，或加一个 TypedDict + 静态检查|
|R-EP-09|本模块→state-coordinator|`settings` 是**扁平字典，键 = suffix**|两张表的 suffix 若出现交集，后写者静默覆盖前者的值，表现为两个实体联动|可写设置类实体（15 个）|当前 15 个 suffix 实测互不重复（`const.py:53-66` vs `:75-92` vs `:138-142`）；但**无测试守护**这一点（`test_const_contract.py` 只查宽度，不查跨表后缀唯一性）|把"跨表 suffix 唯一"加入 AST 测试|
|R-EP-10|本模块→state-coordinator|`diag` 与 `settings` 有 6 个同名键（`vmute`/`color_temp`/`dynamic_contrast`/`movement_sensor`/`smart_eye_protection`/`startup_correction`）|慢轮对同一 GET 命令发两次请求（`coordinator.py:63-83` 与 `:92-101`），每次诊断轮多 6 次 HTTP；两份副本可能不同步（一份成功一份失败）|慢轮时长、`media_player` 属性 vs 开关值不一致|无去重|诊断白名单剔除与设置表重复的项，`media_player` 属性改读 `settings`|
|R-EP-11|本模块→pjctl-transport|`request(cmd_id, params)` 裸接口|命令名/参数格式变更时，`api.py` 的语义方法会同步改，而 40 个实体的裸串不会——两套编码各自漂移|`button` 30 + `switch` 6 + `select` 4 = 40 实体|`const.py` 提供了命令名常量，但 `BUTTON_DEFS` 里的命令串仍是字面量（`button.py:17-83`）|把 `BUTTON_DEFS` 的命令列改为引用 `const.py` 常量，或全部改走 `api.py` 语义方法|
|R-EP-12|本模块→entry-core|`hass.data[DOMAIN][entry_id]["base_id"]` / `["coordinator"]`|entry 卸载期实体仍持有 coordinator 引用并调用 `async_request_refresh`（写后刷新时机与卸载竞态）|卸载瞬间的告警/异常日志|`async_unload_entry` 先卸平台再 pop `hass.data`（`__init__.py:213-220`）|可接受；`async_request_refresh` 在 coordinator 已停时由 HA 内部吞掉|

## 模块内性能瓶颈风险

|瓶颈 ID|位置（本模块内）|当前指标|阈值|触发条件|影响|突破方案|预计工作量|
|-|-|-|-|-|-|-|-|
|R-EP-13|电源动作的同步阻塞链|一次 turn_on/off = 1 次 `probe_state`（2 次 HTTP，默认 10s + 5s 超时）+ 1 次发键 + `wait_for_state` 最多 10s（每秒 2 次 HTTP）|用户等待体感 ~1s；最坏 25s+|设备断电/半挂起|该协程阻塞、`_power_lock` 被长期占用（后续 turn_on 排队）|`wait_for_state` 收敛为指数退避（1s→2s→4s）；或改用"发键后直接交给协调器下一轮"|小|
|R-EP-14|`Ef50ConfigSwitch` 写后不刷新|写完成到固件真值确认的间隔 = 下一轮慢诊断轮，最坏 12×15s=180s|用户期望 <5s|每次开关切换|开关可能在 3 分钟内显示与设备不一致的值|补 `await self.coordinator.async_request_refresh()`（与 number/select 对齐）|极小|
|R-EP-15|对焦闭环的最坏耗时|14 轮 ×（2.0s 脉冲 + 0.45s 停稳 + POS 读）≈ 35s+|前端 set_value 的服务调用超时|远距离目标 + 固件响应慢|服务调用超时但马达仍在动，用户重试 → 触发 R-EP-02|降 `FOCUS_GOTO_MAX_ATTEMPTS` 或在前端提示长耗时|小|

## 模块内数据一致性风险

|风险 ID|场景|一致性级别|当前保障|潜在问题|概率|影响|改进方案|
|-|-|-|-|-|-|-|-|
|R-EP-16|有读通路设置写入 → 固件真值|最终一致|乐观写 `settings` + 慢轮回读覆盖（`coordinator.py:116-119`）|收敛窗口最坏 180s；期间固件回绝（待机写入）不会被发现——实体的 `is_on`/`current_option` 显示的是**未生效的值**|高|中|写后立即 refresh（6 个 switch 缺失）；或写后回读单条命令校验|
|R-EP-17|待机期间读值陈旧但无标记|最终一致|仅 `sensor` 两个实体有 `stale` 属性（`sensor.py:76`, `:118-119`）|其余 **14 个**读 `settings` 的消费点（switch 6 + select 4 + number 3 + media_player 属性聚合 1）与 **6 个**读 `diag` 的消费点（sensor.distance、number.color_gain×3、number.focus 的 focus_range、media_player 的 16 键白名单）**没有任何陈旧标记**；对焦 `available` 覆写与基类等价（`number.py:258-260`），待机时仍显示旧位置|高|中|给 `settings`/`diag` 加 `measured_at`；或在 `available` 里叠加 `data["state"] == "on"`（对只在唤醒有效的项）|
|R-EP-18|unique_id 命名耦合导致"删掉活实体"|强一致（注册表操作不可逆）|`legacy` 清理表（`__init__.py:146-150`）按 `(platform, unique_id)` 精确匹配；`tests/test_regressions.py:112-119` 只断言两个历史后缀在表内|`standby_mode` 当前是 select（`select.py:130`），清理表却登记 `("sensor", "...-standby_mode")`，若搬回 sensor 则每次 reload 删除活实体；反向风险：改名忘登记 legacy → 永久 unavailable 孤儿。**无测试断言"当前实体集 ∩ legacy 表 = ∅"**|低|高|加一条测试：当前所有 unique_id 后缀与 legacy 表求交集必须为空|

**失败场景分析模板**

```
正常路径（有读通路）：
  用户写 → 乐观回填 settings + 本地属性 → write_ha_state → refresh → 慢轮真值覆盖 → 收敛

异常路径 A（写成功但固件未生效，如待机写入回 SUCCESS 而无效果）：
  乐观回填 → 界面显示新值 → 慢轮读到旧值 → 覆盖回去（用户看到"自己弹回"，无错误提示）

异常路径 B（无读通路）：
  写成功 → 本地翻转 → 永无回读 → 用户用遥控器改动 → 永久漂移，仅 assumed_state 徽标提示

异常路径 C（并发读改写）：
  A 读三元组(81,90,100) → B 读三元组(81,90,100) → A 写(50,90,100) → B 写(81,20,100)
  结果：A 的 R 通道修改被 B 基于旧值的整串覆盖丢失
```

## 模块可用性风险

|风险 ID|故障域（本模块内）|组件|当前可用性|目标可用性|SPOF|冗余策略|故障恢复策略|RTO / RPO|
|-|-|-|-|-|-|-|-|-|
|R-EP-19|平台 setup 期表解包异常|`switch`/`select`/`button` 的表驱动推导|若某张表新增列而解包点未改 → `ValueError` → **该平台 setup 失败**（HA 记 "Error while setting up platform"，该平台全部实体消失）|该平台实体全部创建|否（可回滚版本）|`tests/test_const_contract.py` 用 AST 比对 4 张表宽度与全部解包点（含推导式）|修版本；实体注册表保留 unique_id，重启后自动复现|一次 HA 重启|
|R-EP-20|单实体写失败|任一词写实体|该实体上抛 `HomeAssistantError`，状态不翻转（`switch.py:174-177` 等）|单实体降级，其余不受影响|否|各实体独立 try/except；失败不回填缓存|用户重试|即时|
|R-EP-21|协调器不可用|全部 58 实体|56 个随 `last_update_success` 变 unavailable；`binary_sensor` 与 `button` 恒可用（`binary_sensor.py:50-52`、`button.py:132-134`）|离线可诊断|是（单一轮询源）|无第二数据源|HA 自动重试轮询；`binary_sensor` 报 off|≤1 个轮询周期|

## 模块内技术债务

|区域（本模块）|债务类型|原因|严重度|偿还建议|
|-|-|-|-|-|
|写通道分裂（40 实体裸串 vs 15 实体语义方法）|设计债|历史上"先打通命令、后补封装"，封装补齐后实体层未回迁；`api.py` 14 个公开方法在 `custom_components/` 内零生产调用者（其中 12 个连 api.py 内部也不调用）|严重|把 `BUTTON_DEFS` 与 `WRITABLE_*` 的写路径改走 `api.py` 语义方法，删除裸串分支|
|`except Exception` 宽捕获（`switch.py:107`、`select.py:108`）|代码债|推测为"担心固件抛出非 `EpsonApiError`"，但会把编程错误包装成"写入失败"|中等|改回 `except EpsonApiError`；确需兜底则单独 `except Exception` 并 `_LOGGER.exception` 留栈|
|16 处 `DeviceInfo` 重复构造（简报口径 11 处，实测构造点 16）|代码债|每类各写一遍，`identifiers/name/manufacturer/model` 四个字段逐字重复；`model` 回退文案硬编码 `"Epson EF Projector"` 而非引用 `DEFAULT_NAME`|中等|抽 `_device_info(coordinator, base_id, name)` 帮助函数（放 `const.py` 之外的新 `entity.py`，避免污染契约层）|
|15 处 `self._entry = entry` 零读取|代码债|模板复制|轻微|删除赋值；`Ef50ConfigSwitch`/`Ef50ConfigSelect` 的 `_get_cmd` 同理|
|`light_measured_at` 的 `hasattr(..., "isoformat")` 防御分支|代码债|类型曾从 `datetime` 改为 ISO 字符串，消费点未清理|轻微|既然统一存字符串（`coordinator.py:56`），直接去掉 `hasattr` 分支|
|`available` 覆写 7 处与基类等价|代码债|注释声称"只在唤醒状态有效"，但实际只跟随在线状态|中等|对"仅唤醒可读"的项改为 `last_update_success and data["state"]=="on"`，或删掉误导性覆写与注释|
|无读通路实体的漂移无告警|设计债|`assumed_state` 只是 UI 徽标，不产生事件|中等|在 `extra_state_attributes` 里加 `last_written_at` 时间戳，便于自动化判断"多久没确认过"|
|`settings` 键名以字符串字面量散落|代码债|无集中 schema|中等|`const.py` 加 `SETTINGS_KEY_*` 常量或 TypedDict|
|模块级单测缺失|测试债|`tests/` 无任何平台层测试（无 `test_media_player.py`/`test_switch.py`）；`test_const_contract.py` 只覆盖表宽度，`test_regressions.py` 只覆盖对焦/清理/注册表 API|严重|补平台层单测：mock coordinator + mock api，断言"写 → 回填键 → 是否 refresh"三件事|
