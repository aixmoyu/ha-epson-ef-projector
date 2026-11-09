---

project: "ha-epson-ef-projector"

type: risk

description: "本模块范围（custom_components/epson_ef/api.py）的架构风险：传输层被绕过、34 个命令方法中 12 个在包内零调用者、对焦闭环的无锁与返回语义、认证状态的并发共享、session 生命周期缺口、等待语义非硬上限、明文 HTTP 与硬编码固件凭据。全局级风险见 architecture_views/risk.md，本文件只记录本模块 code_mapping 覆盖的故障域。"

base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3

---

# 模块架构风险分析

> 故障域限定于 `custom_components/epson_ef/api.py`（656 行）及其直接消费的 `const.py` 常量（`api.py:16-33` 的 16 个 import 名）。
> 编号前缀 `R-T`（T = transport）以避免与 `architecture_views/risk.md` 的 `R-01..R-NN` 冲突；重叠条目只在边界处引用，不重复展开。
> 概率/严重度取值：高/中/低；优先级 P0 阻断发布 / P1 应尽快 / P2 排期 / P3 观察。

## 模块内稳定性与并发风险

|风险 ID|场景|触发条件|影响范围（本模块内）|概率|严重度|优先级|当前缓解|建议缓解|状态|
|-|-|-|-|-|-|-|-|-|-|
|R-T01|公开命令方法大面积零调用者|任何人新增/修改方法时误以为它已被使用|34 个命令方法中 **12 个零调用者**：`get_light_time`、`get_distance_mm`、`get_light_output`、`get_picture_preference`、`set_picture_preference`、`get_color_gain`、`focus_step`、`geometry_zoom`、`geometry_shift`、`geometry_reset`、`get_auto_focus_state`、`get_auto_focus_target`（AST + 全包 grep 实测）|高|中|**P1**|仅 `tests/test_api.py` 覆盖其请求体（如 `:339-352`, `:429-470`, `:495-500`）；`tools/verify_round4.py:71` 是 `focus_step` 的唯一真实调用者|删除或标 `@deprecated`；或让平台层改走这些方法以收口（如 `button.py:68-82` 的几何按钮复用 `geometry_zoom/shift/reset`）|开放|
|R-T02|最小脉冲位移大于容差，存在窄振荡带|剩余量的绝对值落在 (3, 3.5) 单位区间（即容差 < \|r\| < 最小脉冲击中距离/2 的邻域）|对焦位置在目标两侧 ±3.5 内来回，直到 14 次预算耗尽|低|中|**P2**|`FOCUS_MIN_PULSE_S=0.10` × `FOCUS_TRAVEL_UNITS_PER_SEC=65` = 6.5 单位/脉冲；剩余量 r 走一步后变为 6.5−\|r\|，当 \|r\| ∈ (3, 3.5) 时新剩余量仍 >3，形成宽度 0.5 单位的振荡带（`api.py:566-571`, `const.py:124-129`）。因速度是实测近似值，带宽是估算|把 `FOCUS_MIN_PULSE_S` 降到 ≤ 6/65 ≈ 0.046s 使脉冲位移 ≤ 2×容差，或在 \|r\| < 6.5 时改用 `focus_step` 微调|开放|
|R-T03|取消路径上 OFF 不保证送达|`goto_focus` 的 sleep 被取消后，finally 内的 `await focus_move_stop()` 再次被打断|对焦马达持续运行直到撞限位|低|高|**P1**|`try/finally` 已保证首次取消场景下发送 OFF（`api.py:573-582`，意图注释 `:575-578`，回归测试 `tests/test_regressions.py:63-86`）；但 finally 内 `await` 未 `asyncio.shield`，二次取消仍会跳过|`await asyncio.shield(self.focus_move_stop())` 且捕获 `CancelledError` 后重发|开放|
|R-T04|认证状态跨协程共享且会被单个失败清空|并发命令中任一超时/ClientError|所有并发请求集体退化回 401 挑战链|中|中|**P2**|`_nc` 同步自增（`api.py:123-125`，注释声明并发安全）；但 `_update_auth` 归零（`:111`）、`_clear_auth` 被任一协程触发（`:208`, `:213`），无锁|把 auth 改为每请求携带的不可变快照，或对 `_auth`/`_nc` 整体加锁|开放|
|R-T05|session 生命周期三处缺口|卸载后迟到协程 / 外部 session 被关闭|连接泄漏、借用外部 session 时反而接管所有权|低|低|**P3**|`close()` 仅在有所有权时关闭并把 `_session=None`（`api.py:87-90`）；但 `_owns_session` 不复位（`:88-90`）、`_ensure_session` 无 `_closed` 标志会静默重建（`:93-97`）、外部 session 被关后会被替换并夺权（`:94-96`）|加 `_closed` 布尔，关闭后 `_ensure_session` 直接抛错；`close()` 复位 `_owns_session`|开放|
|R-T06|`wait_for_state` 的超时不是硬上限|单次 `probe_state` 本身耗时超过 `timeout_s`|电源确认实际耗时远超预算|中|中|**P1**|检查顺序是"先探测、后比 deadline"（`api.py:284-293`）；`probe_state(verify=False)` 最坏 = 10s（liveness）+ 5s（引擎查询）= 15s，而调用方预算 `_POWER_CONFIRM_TIMEOUT=10.0`（`media_player.py:27`）|改用 `asyncio.timeout(timeout_s)` 包住整个循环，或把 deadline 检查提前到循环入口|开放|
|R-T07|明文 HTTP + 固件硬编码凭据|同网段嗅探 / 仓库公开|凭据与全部命令可被旁路重放|中|中|**P2**|URL 硬编码 `http://`（`api.py:79`）；凭据为公开常量（`const.py:15-19`），注释明确"baked into projector firmware, not tied to a particular installation"；Digest-MD5 本身无信道保护|无代码内缓解（固件只暴露 HTTP）；应在文档声明"投影仪必须置于可信内网"|接受|
|R-T08|传输层可被绕过（门面有后门）|任意调用方直接 `api.request(cmd, params)`|危险命令与 size 协议全部可由上层拼错|高|中|**P1**|`request` 是公开方法，包内被 4 处直接调用：`button.py:138`、`select.py:107`、`switch.py:106`、`services.py:64`；`tools/` 26 个探测脚本与 `tests/` 亦直调|把 `request` 降为 `_request`，为需要透传的场景保留一个显式的 `raw_request()` 并加命令白名单|开放|
|R-T09|危险命令仅靠"不实现"来屏蔽|通过 `services.raw_request` 或任何新代码直接发参数|`SET_REFRESH "1"`（立即刷新并关机）、`SET_AMBIENT_LIGHT "255"`（恢复出厂）、光效槽 11、`GET_ESCVP` 工厂写命令|中|高|**P1**|模块内确实不产生这些参数：`set_refresh_timer` 只发 `"0 <h>"`（`api.py:481-488`）、氛围灯三方法只产生 3/4/5/9 号参数（`api.py:387-418`）、测试图案仅 `TEST_PATTERN_ON/OFF`（`api.py:428-430`, `const.py:182-183`）；`services.py:64` 无任何校验把任意 `id/params` 直送固件|在 `request()` 内加危险参数黑名单（至少 `SET_REFRESH`+`params=="1"`、`SET_AMBIENT_LIGHT`+`params=="255"`），命中即抛错|开放|
|R-T10|`probe_state` verify 分支的重试是阻塞式|待机态每次轮询都命中 FAIL|每轮多付 1.5s + 一条 5s 超时查询|中|低|**P3**|`probe_state()` 默认 `verify=True`（`api.py:222`），协调器调用未传参（`coordinator.py:43`），故每 15s 轮询都可能多花 1.5s+5s|协调器改为 `verify=False`，仅在电源切换后由 `wait_for_state` 做确认|开放|
|R-T11|`get_raw` 无超时参数，挂起命令会拖满预算|调用未映射子索引或深待机|单条命令占用 10s（默认）且无法缩短|中|低|**P3**|docstring 明示"some hang the HTTP thread… callers must apply a timeout"（`api.py:343-346`），但签名只有 `cmd_id, params`，无 `timeout`（`:340`）；实际缓解靠 `coordinator.py` 的 0.2s 间隔而非超时|给 `get_raw` 增加 `timeout` 透传参数（`request` 已支持）|开放|
|R-T12|`_TIMEOUT` 死常量误导|维护者以为默认超时由它控制|实际默认值是 `request` 内的字面量 `10.0`|高|低|**P3**|`api.py:37` 定义 `ClientTimeout(total=10)` 后从未引用；`api.py:160` 另写 `10.0`|删除常量，或改为 `_DEFAULT_TIMEOUT = 10.0` 并被 `:160` 引用|开放|

## 跨模块依赖风险

|风险 ID|依赖方向|依赖对象|失效/漂移场景|影响|当前保障|改进方案|
|-|-|-|-|-|-|-|
|R-T13|本模块→下游|`const.py` 的 16 个被 import 常量|常量改名/改值（尤其 `FOCUS_TRAVEL_UNITS_PER_SEC`、`QC_*_STEP`）|对焦定位与几何步进精度漂移，且不会有 ImportError（若仍是同名不同值）|`QC_ZOOM_STEP=104`/`QC_SHIFT_STEP=13` 被 payload 断言间接守护（`tests/test_api.py:429-465`）；其余仅弱守护：`tests/test_regressions.py:89-93` 只断言 `FOCUS_STEP_UNIT==1` 与 `FOCUS_TRAVEL_UNITS_PER_SEC>0`|为 65.0/3/14/0.45/0.10/2.0 加契约断言（改值必须同步改测试）；104/13 已覆盖，无需重复|
|R-T14|上游→本模块|`services.raw_request`（`services.py:56-71`）|任意 `id/params` 透传，且失败被 `except Exception` 吞成 `{"error": str(err)}`（`services.py:64-66`）|危险命令与 size 拼错的唯一外部入口；错误只进事件总线，用户可能漏看|`services.yaml:19` 有"谨慎使用"文字提示，无程序约束|命令/参数白名单 + 危险参数直接拒绝，见 R-T09|
|R-T15|上游→本模块|`coordinator.py` 的调用假设|协调器假定 `probe_state` 只会抛 `Unavailable`/`CommandFailed`（`coordinator.py:44-49`），但模块还可能抛 `Timeout`（已归一化，安全）与通用 `EpsonApiError`（未捕获）|未归一化的 `EpsonApiError`（如 bad JSON、HTTP 500）会穿透 `_async_update_data`，由 `DataUpdateCoordinator` 兜成 `UpdateFailed`，行为等同不可达|`EpsonApiError` 是三者共同基类；协调器只捕两个子类|协调器改为 `except EpsonApiError` 兜底，或在模块内把"端点答复了但无法解析"也归一化|
|R-T16|上游→本模块|`entity-platforms` 的 5 个平台文件|写动作直接调 `api.request`/`set_*`，不经过任何串行化|并发写交错（与全局 R-01 同源，此处只记传输层视角：模块内无写锁）|无|在 `EpsonApi` 上加写串行化锁（按命令族分组），对焦/几何优先|
|R-T17|本模块→下游|固件 `WebApiSrv`（单线程）|命令挂起或深待机|本模块只能超时放弃，无自愈能力|`ClientTimeout` + `probe_state` 归一化|无代码内方案（需设备侧重启）；维持"超时不重试"策略|

## 模块内性能瓶颈风险

|瓶颈 ID|位置（本模块内）|当前指标|阈值|触发条件|影响|突破方案|预计工作量|
|-|-|-|-|-|-|-|-|
|R-T18|`probe_state` 最坏路径|1 条 liveness（≤10s）+ 2 条引擎查询（各 ≤5s）+ 1.5s 退避 ≈ **21.5s**|调用方预算 10s（`media_player.py:27`）、轮询间隔默认 15s（`const.py:11`）|待机且网络半挂|`wait_for_state` 违约、轮询积压|见 R-T06/R-T10|小|
|R-T19|`goto_focus` 最坏耗时|14 轮 ×（1 次 POS 读 + 1 次起转 + ≤2.0s 脉冲 + 1 次 OFF + 0.45s 静置）：纯等待部分 ≈ 14×(2.0+0.45) ≈ **34s**，再加每轮 3 条 HTTP（正常各数十毫秒）|实体 setter 无超时包裹（`number.py:282-301`）；HA 服务调用侧超时值未在本仓库定义 [待确认]|目标远、速度估计偏低或落入振荡带|用户操作长时间无响应，服务调用可能先超时而后台马达仍在动|压低 `FOCUS_GOTO_MAX_ATTEMPTS`（如 6）；每轮用实测进度对速度做比例校正，缩短收敛轮数|小|
|R-T20|每条请求最多 2 次 HTTP|401 后重试一次（`api.py:180-204`）|—|nonce 被服务端轮换或缓存被清|命令延迟翻倍|换挑战后不必重发（当前实现是重发原命令，不可避免）；可接受|—|
|R-T21|每次请求重算 MD5|3 次 MD5（ha1/ha2/response，`api.py:128-131`）|微秒级|高频轮询（30+ 命令/诊断轮）|可忽略，但 ha1 只依赖 user/realm/password 却每次重算|缓存 ha1（realm 不变时可复用）|小|

## 模块内数据一致性风险

|风险 ID|场景|一致性级别|当前保障|潜在问题|概率|影响|改进方案|
|-|-|-|-|-|-|-|-|
|R-T22|`goto_focus` 返回值不携带收敛标志|无保证|返回"最终实测位置"（`api.py:584`），调用方只能拿它与目标比较|调用方 `number.py:293-297` 只判 `None`，不判偏差 → 预算耗尽时界面显示"设置成功"|高|中|返回 `(position, converged: bool)`，或模块内超差时抛 `EpsonApiCommandFailed`|
|R-T23|目标值不做行程钳制|—|`int(target)` 后直接使用（`api.py:565`）；行程 0–264 只在实体层限制（`number.py:237-238`）|绕过实体（服务/脚本）可让马达冲端点|低|中|在 `goto_focus` 入口按 `FOCUS_MIN/FOCUS_MAX` 钳制并记 warning|
|R-T24|`size` 与 `params` 可能不一致|—|`size = len(params.split(" "))`（`api.py:171`）|连续空格导致多算 token（`"a  b".split(" ")` → 3）；`params=""` 会算成 1；两者都让固件以 FAIL 拒绝|低|中|用 `len(params.split())` 或显式校验，并对空串走 `params=None` 分支|
|R-T25|`resultCode` 缺失被当作失败|—|`int(data.get("resultCode", -1)) != 0`（`api.py:195`）|若固件某命令成功但省略 resultCode，会被误判为 CommandFailed|低|低|维持现状（保守），但在 docstring 记录该假设|
|R-T26|灯时读数的双重语义|—|`get_light_time` 待机抛异常（`api.py:331-335`），而 `probe_state` 内部把同一命令的 FAIL 当作 off（`api.py:260-264`）|同一命令在两个入口有不同失败语义，若上层混用会得到相反的"是否可用"结论|中|低|明确 `get_light_time` 为"仅唤醒态可调用"，并在 docstring 交叉引用 `probe_state`|

**失败场景分析**

```
正常路径：request → 200 + resultCode==0 → 解析 → 返回 typed value
异常路径 1（首发无缓存）：401 → _update_auth 成功 → 重发（消耗 nc=1）→ 200
异常路径 2（401 无 nonce）：_update_auth 返回 False → EpsonApiError，不重试
异常路径 3（两轮都 401）：第二轮 attempt==1 → 落 HTTP 错误分支 → EpsonApiError("HTTP 401: ...")
异常路径 4（超时）：_clear_auth（全局失效）→ EpsonApiTimeout → probe_state 第一步改写为 Unavailable
异常路径 5（深待机命令挂起）：引擎查询 5s 超时 → probe_state 返回 "off"，不上抛
异常路径 6（对焦中途取消）：sleep 抛 CancelledError → finally 发 OFF → OFF 失败仅 warning → 原 CancelledError 继续传播
异常路径 7（对焦预算耗尽）：14 轮未收敛 → 静默返回最终位置 → 上层显示成功（R-T22）
```

## 模块可用性风险

|风险 ID|故障域（本模块内）|组件|当前可用性|目标可用性|SPOF|冗余策略|故障恢复策略|RTO / RPO|
|-|-|-|-|-|-|-|-|-|
|R-T27|认证会话|`_auth` 内存态|进程级单点；进程重启即需重新挑战|—|否（自愈：一次 401 即重建）|无|下一条请求自动重新挑战（`api.py:200-204`）|≈1 次往返|
|R-T28|HTTP 传输|`aiohttp.ClientSession`|单 session，连接池故障即全部命令失败|—|是（每 `EpsonApi` 实例一个）|无|`_ensure_session` 在 session 已关时重建（`api.py:93-97`）；但无 `_closed` 保护（R-T05）|≈1 次请求|
|R-T29|固件端点|`http://host:8080/api/pjctl`|端点挂起即本模块全部方法超时|—|是|无|仅 `close()`（`api.py:87-90`），无重连/熔断/退避|需人工重启设备|
|R-T30|凭据|`const.py` 硬编码|固件轮换密码即全体失效，且无法通过配置覆盖|—|是（全局单一凭据）|无|无（只能改代码发版）|发版级|

## 模块内技术债务

|区域（本模块）|债务类型|原因|严重度|偿还建议|
|-|-|-|-|-|
|12 个零调用者命令方法（`api.py:295-656`）|设计债|现场分析阶段"先打通再接线"，平台层用 `request` 直调绕过（`button.py:138` 等），方法被留在原地|严重|二选一：平台层改走语义方法，或删除并保留测试为"能力记录"|
|`request` 公开且被包内 4 处直调|设计债|早期只有 `request`，后来才长出语义方法，历史调用点未回收|严重|降级为 `_request` + 显式 `raw_request` 白名单，见 R-T08|
|危险变体靠"不实现"而非校验（`api.py:481-488`, `:411-418`）|设计债|现场分析方法论要求"不暴露未验证能力"，但缺少运行时闸门|中等|在 `request` 内加参数黑名单（R-T09）|
|`_TIMEOUT` 死常量（`api.py:37`）|代码债|重构遗留，默认值被内联成字面量 `10.0`（`api.py:160`）|轻微|删除或改为被引用的 `_DEFAULT_TIMEOUT`|
|`api.py:216` 的 `raise EpsonApiError("digest authentication failed")`|代码债|两轮循环内必 return 或 raise，该行不可达|轻微|改为 `assert False` 或删除|
|auth/session 状态无锁（`api.py:84-97`）|设计债|单协程轮询的历史假设，现已被并发写打破|中等|见 R-T04/R-T05|
|`goto_focus` 返回语义不含收敛标志（`api.py:584`）|设计债|返回值从"位置读数"演化而来，未随闭环算法升级|中等|见 R-T22|
|测试只覆盖请求体与解析，无并发/取消/超时集成测试|测试债|`tests/test_api.py` 用 fake session 顺序回放（`:64-97`），无法表达并发|中等|补 `asyncio.gather` 并发用例与 `cancel()` 用例（已有 `tests/test_regressions.py:63-86` 一个取消用例）|
