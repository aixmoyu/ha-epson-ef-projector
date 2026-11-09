---

project: "ha-epson-ef-projector"

type: adr

id: ADR-pjctl-transport-002

description: "电源状态判定采用『两级探针 + 单向异常重分类』：先用恒应答的 CONFIG_GET_PRODUCT_NAME 证活（失败→Unavailable，Timeout 升级为 Unavailable），再用仅唤醒态应答的 CONFIG_GET_LIGHT_TIME 判态（任何失败→off）。失败被分成四类异常，供上层分别决定重试/降级/上报。"

base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3

status: accepted

title: "两级探针判电源态 + 四类异常分类"

date: 2026-11-07

code_refs:
  - custom_components/epson_ef/api.py
  - custom_components/epson_ef/coordinator.py
  - custom_components/epson_ef/media_player.py
  - tests/test_api.py
  - tests/test_regressions.py

---

# ADR-pjctl-transport-002: 电源判定与异常分类

## 状态

accepted

## 1. 背景

**业务背景**：整个集成的一切行为都以"投影仪开还是关"为前提——实体可用性、诊断轮是否执行、写操作是否被允许、电源按钮该不该发盲 toggle 键。判错的代价很高：把待机判成不可达会让 58 个实体全部 `unavailable`；把断电判成"活着但卡住"会让 `turn_on` 静默无效。

**技术背景**：固件没有统一的 power-state 查询命令。实测（`api.py:225-233` 的 docstring）得到两条性质相反的观察：

- `CONFIG_GET_PRODUCT_NAME` 在**任何**电源态都立即应答 → 只能证活，不能判态。
- `CONFIG_GET_LIGHT_TIME` **仅在唤醒态**成功；普通待机返回 FAIL，深待机则"服务端直接断连"或挂住到客户端超时（`api.py:248-251`、`:229-233`）。

同时传输层会抛出至少三种性质不同的失败：网络不可达、命令被固件拒绝、命令超时。它们对"该重试还是该放弃"的答案完全不同。

**核心问题**：如何只用"恒应答命令 + 仅唤醒应答命令"这两类探针，稳定地把设备状态压缩成 on/off 两态，并且让上层不必自己解读底层异常？

**约束条件**：

- 待机是**常态**而非异常：投影仪大部分时间处于待机，轮询每 15s 触发（`const.py:11`）。
- 深待机的表现是"挂住/断连"，不是"返回错误码"，因此必须依赖超时（`api.py:248-251`）。
- 探针本身要快：诊断轮之外每轮只应花 1–2 条命令。

## 2. 决策驱动因素（Decision Drivers）

1. **待机不误报为故障**（重要性：高）：待机判成 unavailable 会让全部实体失效，是最严重的误判方向。
2. **断电不误报为"卡住"**（重要性：高）：断电必须可被 `turn_on` 流程正确识别，否则用户按下开机没有任何反馈。
3. **上层分支数最小**（重要性：中）：协调器、媒体播放器、传感器都消费这个结果，分支越少越不容易漂移。
4. **可离线断言**（重要性：中）：`ProbeTests`（`tests/test_api.py:165-215`）覆盖 6 种组合，必须能在无设备下跑。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|Option A：两级探针 + 单向归一化|先 `PRODUCT_NAME` 证活，再 `LIGHT_TIME` 判态；Timeout→Unavailable 升级，引擎失败→off 降级|待机零误报；断电可识别；每条轮询仅 1–2 条命令|依赖"某命令恒应答"这一实测前提，换机型需重新验证|顺序颠倒即失效（假阴性/假阳性互换）|
|Option B：只用 `LIGHT_TIME` 单探针|成功即 on，失败即 off|只需 1 条命令，最快|断电与待机不可区分——断电也会被报成 off，`turn_on` 会对着断电设备发盲键|无法区分故障与待机，诊断不可用|
|Option C：引入第三态 `unknown`|把"读不到"与"没醒"分开|语义更精细|三个消费方都要改；物理上本设备无法区分两者，第三态不可判定|制造"永远 unknown"的死状态|
|Option D：把原始异常直接上抛给上层|模块不做归一化，由协调器/实体各自解读|模块最简单，无信息损失|每个消费方都要重复实现"Timeout 算不算不可达"的判断（协调器 `coordinator.py:44-49`、媒体播放器 `media_player.py:150-176` 已在重复）|解读不一致即行为分叉|

### Option A：两级探针 + 单向归一化

第一步 liveness：`CONFIG_GET_PRODUCT_NAME`（`api.py:236-237`）。`EpsonApiUnavailable` 原样上抛；`EpsonApiTimeout` 捕获后**改写**为 `EpsonApiUnavailable`（`api.py:240-246`），因为"完全不应答的主机"（市电断、IP 错、防火墙 DROP）表现为超时而非连接错误。第二步判态：`CONFIG_GET_LIGHT_TIME` 带 `timeout=5.0`（`api.py:255-257`）；`Timeout`/`Unavailable` 直接 `return ("off", None)`（`api.py:258-259`），`CommandFailed` 在 `verify=True` 时重试一次（间隔 1.5s）后仍失败则 off（`api.py:260-264`）；成功则解析 `"hours extra"` 并返回 `("on", (h, extra))`（`api.py:265-275`）。若响应非空但不可解析，返回 `("on", None)`——能返回响应本身说明设备醒着（`api.py:273-274`）。

同时把失败分成四类异常（`api.py:40-61`）：`EpsonApiUnavailable`（端点不可达）、`EpsonApiCommandFailed`（端点应答但 `resultCode != 0`）、`EpsonApiTimeout`（端点存活但命令未答完）、`EpsonApiError`（bad JSON / HTTP 非 200/401 / 认证失败）。

### Option B：单探针

只用 `CONFIG_GET_LIGHT_TIME`。代价是断电与待机不可区分，而这恰好是两个方向上代价最高的误判（见驱动因素 1、2）。

### Option C：第三态

"读不到"与"没醒"在本设备上是同一物理事实（`LIGHT_TIME` 在深待机直接断连），引入第三态等于引入一个不可判定的分支。诚实性由实体层的 `stale`/`assumed_state` 承担（`sensor.py:118-120`、`switch.py:166-169`），不由传输层制造伪精度。

### Option D：原始异常上抛

协调器已经在做"`Unavailable` → UpdateFailed、`CommandFailed` → 当作 off"的映射（`coordinator.py:44-49`），媒体播放器又自己写了一遍（`media_player.py:150-176`）。若模块不做归一化，这段逻辑会在每个新消费方复制一遍，且极可能实现得不一致（例如漏掉 Timeout）。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

- 对因素 1、2：两级探针是唯一能同时满足"待机不误报"与"断电可识别"的结构；两个方向的重分类（升级/降级）正是为这两条服务的。
- 对因素 3：上层只需处理 `Unavailable`（设备离线）与 on/off 两态；`CommandFailed` 在探针路径上已被吸收成 off，只有显式调用 `get_light_time` 时才会看到。
- 对因素 4：六个用例覆盖了"灯时成功/失败/超时/断连/verify 开关/存活失败"（`tests/test_api.py:165-215`），另有回归测试专门断言"不可达必须报 Unavailable 而非 Timeout"（`tests/test_regressions.py:187-205`）。

### 4.1 实施要点

- **顺序不可交换**：先证活、后判态。交换后断电与待机不可区分。
- **归一化方向不可逆**：存活探针只允许出 `Unavailable`，引擎查询只允许出 off（详见 `guidelines/principle-pjctl-transport-002`）。
- **verify 默认开**：`probe_state(verify=True)`（`api.py:222`）对 FAIL 重试一次以避开电源切换瞬态；`wait_for_state` 内部用 `verify=False` 压低成本（`api.py:286`）。协调器调用未传参（`coordinator.py:43`），因此待机态每轮多付 1.5s + 一次查询（`risk.md` R-T10）。
- **解析失败判 on 而非 off**：`except (ValueError, IndexError): return ("on", None)`（`api.py:273-274`），因为能返回 HTTP 响应就说明设备在运行。
- **四类异常的捕获顺序有陷阱**：`EpsonApiTimeout` 是 `EpsonApiError` 的子类，任何 `except EpsonApiError` 都会吞掉它；写代码时必须先捕子类（对照 `api.py:238-246` 的顺序）。

## 备注

- 已知残留：`wait_for_state` 的 `timeout_s` 不是硬上限，因为 deadline 检查在探测之后（`api.py:284-293`），而单次探针最坏约 15s，超过调用方 `_POWER_CONFIRM_TIMEOUT=10.0`（`media_player.py:27`）。见 `risk.md` R-T06。
- 未归一化的 `EpsonApiError`（bad JSON、HTTP 500）不在协调器的两个 `except` 分支内（`coordinator.py:44-49`），会由 `DataUpdateCoordinator` 兜成 `UpdateFailed`，效果等同不可达——这是可接受的兜底，但语义上不如显式处理（`risk.md` R-T15）。
- 若未来适配其他机型，"`PRODUCT_NAME` 恒应答"这一前提必须重新实测；它是整个方案的地基，且无法从代码推导。
