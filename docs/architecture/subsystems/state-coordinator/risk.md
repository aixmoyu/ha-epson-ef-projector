---
project: "ha-epson-ef-projector"
type: risk
description: state-coordinator 范围内的架构风险：故障域限定在 coordinator.py 的轮询编排与 const.py 的契约常量。核心风险面是「契约表宽度漂移导致全集成不可用」「慢轮时长超出轮询间隔造成积压」「待机期间状态整段冻结被误读」「乐观值被真值覆盖时机不透明」「死常量与硬编码造成双份事实」。跨模块级（单线程固件挂起、部署拓扑）见 architecture_views/risk.md，本文件不重复。
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
---

# 模块架构风险分析

> 本模板聚焦**单一模块**范围内的风险：故障域限定于本模块 `code_mapping`，跨模块风险仅记录与本模块边界（见 `boundaries.md`）相关的依赖项。
> 全局级（跨模块、部署拓扑、SPOF 等）风险登记在 `architecture_views/risk.md`，本文件不重复其条目，仅在本模块边界处引用。

## 模块内稳定性与并发风险

|风险 ID|场景|触发条件|影响范围（本模块内）|概率|严重度|优先级|当前缓解|建议缓解|状态|
|-|-|-|-|-|-|-|-|-|-|
|R-SC-01|契约表宽度漂移|表定义加列但解包点未跟改（或反之）|`_async_update_data` 抛 `ValueError: too many values to unpack` → `last_update_success=False` → **全部 58 个 CoordinatorEntity 变 unavailable**，且只在慢轮（首轮或第 12 轮）爆发|中（历史上发生过一次，`tests/test_const_contract.py:1-9`）|严重|P0|`tests/test_const_contract.py:111-125` 用 AST 比对 4 个表（含 `BUTTON_DEFS`）的全部解包宽度；`:127-142` 锁定解包点位置|① 把 `AMBIENT_MODE_OPTIONS` 纳入 `TABLE_SOURCES`（当前不在，靠 `dict()`/`[0]` 消费故不被覆盖）；② 解包改成 `for row in table:` 再按索引取，从语法上消除宽度耦合|部分缓解|
|R-SC-02|慢轮时长超出轮询间隔|慢轮 35 条串行请求，每条最坏 10s（`api.py:160`），叠加 27 次 `sleep(0.2)`（`coordinator.py:88`, `:96`, `:101`）|`update_interval` 默认 15s（`const.py:11`）远小于慢轮最坏耗时（≈35×10+5.4 ≈ 355s）。HA 的 `DataUpdateCoordinator` 会在上一轮未完成时跳过本次调度，导致**快轮被饿死**——电源态感知延迟从 15s 恶化到分钟级|中（取决于固件响应速度与是否处于半死状态）|严重|P0|无内建超时预算；`api.py:160` 的 10s 是**单条**上限，不是整轮上限|① 为慢段加整轮时间预算（`asyncio.timeout`）并在超预算时提前 break（保留已读到的键）；② 慢段改分片（每轮只做 1/3 子表），把 32 条摊平到 3 轮；③ 把 `update_interval` 下限从 5s（`config_flow.py:63`）提到 ≥30s|未缓解|
|R-SC-03|待机期间状态整段冻结被误读|设备进入待机后，`if state == "on":`（`:61`）使 `diag`/`settings` 两个键**整段不刷新**|实体仍显示待机前的值（因 carry-forward），用户看到「亮度 100%」而设备已关机。`stale` 属性只覆盖 `light_hours`（`sensor.py:76`）与 `distance`（`sensor.py:118-119`），**未覆盖读 `settings` 的 13 个实体与读 `diag` 的 5 个消费点**|高（每次关机都发生）|中|P1|`sensor.py` 的两个传感器有 `stale`；`switch.py`/`select.py`/`number.py` 的 settings 类实体**没有任何陈旧标记**|① 给 `settings`/`diag` 加 `diag_measured_at`/`settings_measured_at` 时间戳并暴露；② 或在实体 `available` 属性里叠加 `state == "on"`（但会丢失「最后一次已知值」的价值）|部分缓解|
|R-SC-04|`_poll_count` 无上界且进程重启即归零|长时间运行后 `_poll_count` 单调递增（`:38`）|Python int 无溢出风险，但**重启后归零**意味着重启后的首轮必然执行完整慢轮（`:59` 的 `== 1`）。若 HA 频繁重启（或 config entry 频繁重载，`__init__.py:209-210`），每次都会打一轮 35 条请求|低|轻微|P3|无|可接受；但若出现「重启风暴」场景应改为「距上次慢轮超过 N 秒则执行」的时间判据而非计数判据|接受|
|R-SC-05|`light_measured_at` 契约曾漂移|该键被声明为 ISO 字符串（`:54-56` 注释明示「必须 JSON 可序列化」）|两个消费方都写成 `measured.isoformat() if hasattr(measured, "isoformat") else measured`（`sensor.py:80-82`、`media_player.py:118-120`）——**不信任契约**的防御性代码|—（已完成）|轻微|P3|消费方双侧兼容|契约本身正确；建议删除 `hasattr` 分支以恢复「只有一种表示」的清晰度，或反之在注释里说明为何保留|已缓解（带异味）|
|R-SC-06|`AMBIENT_MODE_OPTIONS` 无宽度护栏|该表不在 `tests/test_const_contract.py:27-32` 的 `TABLE_SOURCES` 内|`select.py:180` 用 `[label for label, _mode in ...]`（宽度 2 解包）、`:187` 用 `dict(...)`（宽度 2）。若将来加第三列，`:180` 立即 `ValueError`，而测试不会失败|中|中|P2|无|把该表加入 `TABLE_SOURCES`，或显式在表定义处加 `assert all(len(r) == 2 ...)`|未缓解|
|R-SC-07|死常量与重复事实|`FOCUS_STEP_UNIT`（`const.py:122`）零生产消费者，仅 `tests/test_regressions.py:91` 断言；`STANDBY_MODE_OPTIONS` 无外部消费者但仍公开导出|读者误以为改 `FOCUS_STEP_UNIT` 会改变对焦步进行为——实际 `api.py:516-529` 直接发 `INC`/`DEC`，步长完全由固件决定|高（认知陷阱）|轻微|P3|`FOCUS_STEP_UNIT` 的注释（`:121`）写了「仅用于需要微调一格时」，但代码里并没有「微调一格」的调用点|① 删除 `FOCUS_STEP_UNIT` 并把回归测试换成断言 `api.focus_step` 不发数值参数；② 或在常量旁明确标注「回归哨兵，无生产消费者」|未缓解|
|R-SC-08|`probes` 类型标注与实际宽度不符|标注为 `list[tuple[str, str, str, str \| None]]`（4 元素，`:63`），实际 17 行全部是 3 元素（AST 实测）；解包处是 `for key, cmd, params in probes`（`:84`）|类型检查器（mypy）会认为每行有 4 项而报错或给出错误提示；`test_const_contract` 不覆盖 `probes`，故无护栏|确定（标注就是错的）|轻微|P3|无|把标注改为 `list[tuple[str, str, str \| None]]`（3 元素）|未缓解|
|R-SC-09|`if state == "on":` 双重判定（`:61` 与 `:120`）|`:120` 位于 `:61` 块内部，且 `state` 在 `:43`/`:49` 之后到 `:133` 之间无重新赋值（AST 核验）|`:120` 恒为真 → 冗余但无害。危险在于「读者从冗余推断存在两条不同进入条件」，且若将来有人把 `product` 读取移出 `:61` 块，会引入待机轮多打 1 条请求|确定（代码事实）|轻微|P3|无|删除 `:120` 的判定（保持行为不变），或加注释说明「与 `:61` 同条件，保留为防御」|未缓解（刻意保留）|
|R-SC-10|首刷失败异常类型与捕获类型是否一致 `[待确认]`|`__init__.py:190` 调 `async_config_entry_first_refresh()`，`:191` 只捕获 `ConfigEntryNotReady`；本模块 `:45` 抛 `UpdateFailed`|若 HA 基类不把 `UpdateFailed` 转换为 `ConfigEntryNotReady`，则首次刷新失败会以未捕获异常冒泡，`api.close()`（`:192`）不执行 → 连接/session 泄漏 + entry 进入 setup_error 而非 retry|未知（本环境未安装 HA，无法核验基类实现）|中|P2|依赖 HA 框架行为|搭一个带 HA 的环境验证 `async_config_entry_first_refresh` 的异常语义；若确不转换，则应在 `__init__.py:189-193` 同时捕获 `UpdateFailed`|`[待确认]`|

## 跨模块依赖风险

|风险 ID|依赖方向|依赖对象|失效/漂移场景|影响|当前保障|改进方案|
|-|-|-|-|-|-|-|
|R-SC-11|本模块→下游|`api.py::probe_state` 的**返回值元组形状**|`probe_state` 返回 `(str, tuple[int,int]\|None)`（`api.py:222-223`）。`coordinator.py:43` 与 `:53` 直接 `state, light = ...` 与 `light_hours, light_extra = light`——若下游把 `light` 改成三元组或 `None` 语义变化，`:53` 立即 `ValueError`|每轮都执行 → 抛异常即全集成不可用（比 R-SC-01 更致命，因为不受 12 轮节流保护）|无显式契约测试；`tests/test_api.py` 覆盖 `probe_state` 但未见「元组宽度」断言|加 `tests/test_const_contract.py` 风格的 `probe_state` 返回形状断言；或让 `probe_state` 返回 dataclass/NamedTuple|
|R-SC-12|本模块→下游|`api.py` 的**异常类型语义**|`coordinator.py:44-49` 三分支：`EpsonApiUnavailable`→UpdateFailed；`EpsonApiCommandFailed`→当待机；其余 `EpsonApiError`（含 `EpsonApiTimeout`）**未捕获** → 冒泡成未处理异常|`api.py:240-246` 已把 liveness 的 timeout 归一为 unavailable，但 `get_raw` 等内部对 timeout 的处理（`api.py:349-350` 捕获 `EpsonApiError` 返 None）与此不一致；若 `probe_state` 将来漏归一某个 timeout，会变成未捕获异常而非 UpdateFailed|`api.py:240-246` 的归一化 + `tests/test_regressions.py::UnreachableProjectorTests`|在 `coordinator.py:44` 把捕获面扩大到 `EpsonApiError`（除 CommandFailed 外一律 UpdateFailed）|
|R-SC-13|本模块→下游|`api.py::get_raw` 的「失败返 None 不抛」约定|`coordinator.py` 全部慢段读取都依赖「None = 本状态下读不到」这一约定（9 处 `is not None` 守卫）|若某个 `get_*` 改为抛异常（如 `get_focus_range` 在 `api.py:495-504` 内部已捕获 ValueError 返 None，但外层 `get_raw` 才捕获 `EpsonApiError`），慢轮会在某条命令上整体中断，导致**该轮后半段的所有键都不刷新**|约定写在 `api.py:340-351` docstring|加一层「单条命令失败不影响后续」的 try/except（当前依赖的是 `get_raw` 内部已捕获，属隐式依赖）|
|R-SC-14|上游→本模块|`entity-platforms` 对 `settings` 的**原地写**|`switch.py:109-110`、`select.py:110-111`、`number.py:129-130`、`:216-217`、`:297-298`、`number.py:392-393` 用 `coordinator.data["settings"].setdefault(...)` 直接改快照|本模块 `:117` 的 `dict()` 浅拷贝会把旧快照里的乐观值一并带入，故乐观值天然跨轮存活——这是**未被文档化的隐式协作**。若将来本模块改成深拷贝或重建快照，乐观值立即消失，用户会看到「写完弹回」|无契约声明，仅靠 `principle-global-005` 的文字约定|把「写乐观值」定义成本模块的公开方法（如 `coordinator.set_optimistic(key, value)`），而不是让实体直接改 dict|
|R-SC-15|上游→本模块|`const.py` 4 张表的**第一列后缀**|该后缀同时是：`settings` 字典的键（`coordinator.py:95`）、`unique_id` 尾段（`switch.py:78`、`select.py:75`）、legacy 清理匹配依据（`__init__.py:146-150` 用 `f"{base_id}-{suffix}"`）|改后缀 = 同时改实体唯一 ID → **已有用户的实体变成新的、历史记录断裂**，且 legacy 清理表需同步|`tests/test_const_contract.py:127-142` 只校验解包点，不校验 ID 稳定性|在后缀旁加注释「改动会重命名实体」；或加 golden-file 测试锁定后缀集合|

## 模块内性能瓶颈风险

|瓶颈 ID|位置（本模块内）|当前指标|阈值|触发条件|影响|突破方案|预计工作量|
|-|-|-|-|-|-|-|-|
|P-SC-01|慢轮串行请求段（`coordinator.py:63-115`）|35 条串行，每条最坏 10s；正常固件约 ~0.1–0.5s/条|整轮应 < `update_interval`（默认 15s）|固件响应变慢或某条命令挂起|超过阈值后 HA 跳过后续调度 → 快轮饿死（同 R-SC-02）|整轮 `asyncio.timeout` 预算 + 分片|中|
|P-SC-02|固定 0.2s 节流（`:88`, `:96`, `:101`）|27 次成功读取 × 0.2s = **5.4s** 纯等待|—|每次慢轮|占满默认 15s 间隔的 36%，且该节流**只在读到值时才 sleep**——设备越健康、等待越多，是反直觉的|降为 0.05s，或改为「每 N 条 sleep 一次」|小|
|P-SC-03|表内 6 条命令 + product 重复读取|`CONFIG_GET_VMUTE`/`COLOR_TEMP`/`DYNAMIC_CONTRAST`/`MOVEMENT_SENSOR`/`SMART_EYE_PROTECTION`/`STARTUP_CORRECTION` 各被读 **2 次且参数相同**（诊断表 + 设置表）；`CONFIG_GET_PRODUCT_NAME` 2 次（probe 内 `api.py:237` + `:121`）|35 条中 **7 条冗余请求**（20%）|每次慢轮|多耗 7×RTT + 最多 7×0.2s sleep；去重后 35→28|一次读取同时写进 `diag` 与 `settings`（`:87` 与 `:95` 合并）；probe 的 product 名复用|小|
|P-SC-04|待机慢轮仍打 2–3 条|待机时 `probe_state` 的唤醒探针必 FAIL 并重试（`api.py:252-264` 的 `attempts=2` + `sleep(1.5)` `:262`）+ `get_standby_mode` 1 条|待机时每慢轮 **3 条 HTTP + 1.5s 等待**|每次待机慢轮|稳态待机下每 12×15=180s 打 3 条 + 1.5s 空转，收益仅 1 个 `standby_mode` 值|`probe_state(verify=False)` 用于「上一轮已确认待机」的场景；或把 `standby_mode` 降频到每 10 个慢轮|小|

## 模块内数据一致性风险

|风险 ID|场景|一致性级别|当前保障|潜在问题|概率|影响|改进方案|
|-|-|-|-|-|-|-|-|
|C-SC-01|乐观写入后被真值覆盖的时机不透明|最终一致（弱）|`:117-119` 的 `dict(old)+update(new)`（settings）与 `:125-127`（diag）|用户改亮度 → 实体立即显示新值（`number.py:129-131`）→ 但 coordinator 快照里的真值要等**下一次慢轮**才更新（最长 12×15=180s）。这期间若恰有一次慢轮读到了**旧值**（设备还没生效），会把乐观值覆盖回旧值，用户看到「刚调的值自己跳回去」|中|用户困惑|写后立即 `async_request_refresh()`（已做，`number.py:133`）可缩短窗口；根本解是慢轮读到与乐观值冲突时以乐观值优先一个周期|
|C-SC-02|`settings` 与 `diag` 对同一物理量的双份记录|弱一致|`diag["vmute"]` 与 `settings["vmute"]` 是**同一次命令结果**的两份拷贝（两条独立请求，`coordinator.py:65` 与 `const.py:64-65`）|两次读取不在同一时刻 → 若期间值变化，两个键会不一致。`media_player.py:122-127` 读 `diag["vmute"]`，而 `switch.py:91` 读 `settings["vmute"]`，同一设备状态在两处显示可能不同|中|UI 自相矛盾|合并读取（同 P-SC-03）|
|C-SC-03|`light_hours` 三元组与 `state` 的时序窗口|强一致（同轮）|`:52-56` 在 `probe_state` 返回后立即写入|`probe_state` 内部先判活性再判唤醒（`api.py:236-276`），两步之间设备可能已切换电源态 → 可能得到「state=on 但 light 失败」的组合。代码用 `if light:`（`:52`）处理，此时 `state` 仍为 `"on"` 但无灯时|低|`light_hours` 缺失一轮，由 carry-forward 补齐|可接受|
|C-SC-04|`state` 不结转 vs 其他键结转的不对称|弱一致|`:51` 无条件覆盖；`:135-153` 六条结转|若一次 `probe_state` 因瞬态返回错误态（`api.py:260-264` 的验证重试已部分缓解），`state` 立即反映错误值而其他键保留旧值 → 快照内部出现「电源态与设置值不属于同一时刻」。这是**设计上的取舍**（电源态必须新），但未在任何文档中显式声明|低|短暂误判|在快照里加 `state_measured_at`，让消费方可判断|

**失败场景分析**

```
正常路径：probe → 慢轮(每12轮) → merged.update(真值) → carry-forward → 快照
          写操作: 实体 set → API 成功 → 原地改 data["settings"] → async_write_ha_state()
                  → async_request_refresh() → 快轮(2条) → settings 不刷新 → 乐观值保留
                  → 下一个慢轮 → merged.update(真值) → 乐观值被覆盖

异常路径 A（读失败）：get_raw 返 None → 跳过键 → 末尾 carry-forward 用旧值 → 快照键存在但值是旧的
                       连续 N 轮失败 → 陈旧度无上界（只有 light_hours 有 measured_at 标记）

异常路径 B（慢轮抛异常）：表宽度错误 / probes 解包错误 → ValueError 冒泡出 _async_update_data
                          → HA 标 last_update_success=False → 全实体 unavailable
                          → 下一轮重新尝试，错误持续则永久 unavailable

异常路径 C（写后慢轮读到旧值）：乐观值 A → 慢轮读到设备旧值 B → merged.update 覆盖为 B
                                 → 用户看到值从 A 弹回 B（最长 180s 后）
```

## 模块可用性风险

|风险 ID|故障域（本模块内）|组件|当前可用性|目标可用性|SPOF|冗余策略|故障恢复策略|RTO / RPO|
|-|-|-|-|-|-|-|-|-|
|A-SC-01|契约表宽度|4 张表 + 6 个解包点|依赖 CI 护栏|高|是（一处不匹配 ⇒ 全集成不可用）|AST 测试（4/5 张表覆盖）|修正表或解包点后重载 entry|RTO=一次发布；RPO=期间所有状态与写操作不可用|
|A-SC-02|慢轮阻塞|`_async_update_data` 的 35 条串行请求|无超时预算|快轮不被阻塞|是|无|只能等慢轮自然结束或被 HA 取消|RTO=慢轮最坏时长；RPO=期间电源态不更新|
|A-SC-03|`probes` 元组形状|`:63-83` + `:84`|无护栏|高|是（解包错误即全不可用）|无|修代码|同 A-SC-01|

## 模块内技术债务

|区域（本模块）|债务类型|原因|严重度|偿还建议|
|-|-|-|-|-|
|`coordinator.py:120` 冗余的 `if state == "on":`|代码债|历史增量修改留下的同条件重复判定（与 `:61` 同条件，AST 已证 `state` 无重新赋值）|轻微|删除或加注释说明；改动零行为风险|
|`coordinator.py:63` 的 `probes` 类型标注|代码债|标注为 4 元组，实际 3 元组|轻微|改为 `list[tuple[str, str, str \| None]]`|
|`coordinator.py:92`/`:97` 与 `const.py` 表重复读取 6 条命令（+ product 1 条）|设计债|表驱动设计未做「一次读取多处消费」的抽象|中等|引入「读取计划」概念：一条命令 → 写入多个目标键|
|`const.py:122` `FOCUS_STEP_UNIT`|设计债|常量声明与被消费路径脱钩（生产代码零引用，仅测试断言）|中等|删除，或标注「回归哨兵」并在注释说明「改它不改变行为」|
|`const.py:27` `STANDBY_MODE_OPTIONS` 公开导出但无外部消费者|代码债|实现细节被当作公共契约暴露|轻微|改为 `_STANDBY_MODE_OPTIONS` 或保留但注释说明仅内部用|
|`const.py:53-66` 的 4 张表缺乏统一 schema|设计债|4 张表有 4 种不同宽度（4/6/4/2），且标签列形状不一致（`list[str]` vs `list[tuple[str,int]]` vs `dict`）|中等|统一为 `NamedTuple` 或 dataclass，宽度错误从运行时前移到导入时；顺带解决 R-SC-01/R-SC-06|
|`coordinator.py:105-115` 的后置命令不节流|代码债|只有三张表内的命令 `sleep(0.2)`，后置 6 条命令无间隔|轻微|统一为「所有慢段命令都走同一个节流器」|
|`const.py` 全文件无类型化契约文档|文档债|值域常量的证据等级散落在中文注释里，无机器可读标记（如 `# evidence: live-loop`）|中等|引入注释约定或用测试断言关键值域，防止注释与值脱节|
|`media_player.py` / `button.py` 硬编码 keycode，绕过 `KEYCODES`|设计债（跨模块，边界处）|表驱动未贯彻到消费侧|中等|实体改为 `KEYCODES["POWER"]` 等命名引用；顺带让 `KEYCODES` 成为真正的单一事实源|
