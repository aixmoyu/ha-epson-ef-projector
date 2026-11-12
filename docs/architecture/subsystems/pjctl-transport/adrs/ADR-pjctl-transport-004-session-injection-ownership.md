---

project: "ha-epson-ef-projector"

type: adr

id: ADR-pjctl-transport-004

description: "HTTP 会话采用『构造可注入 + 懒创建 + 所有权标志』：外部可传入 ClientSession 以复用 HA 的连接池（单测则注入 fake）；未注入时首次请求懒建；close() 只关闭自己拥有的 session。该决策换来可测性与宿主复用，但留下三处生命周期缺口。"

base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c

status: accepted

title: "HTTP 会话可注入并显式标注所有权"

date: 2026-11-07

code_refs:
  - custom_components/epson_ef/api.py
  - custom_components/epson_ef/__init__.py
  - custom_components/epson_ef/config_flow.py
  - tests/test_api.py

---

# ADR-pjctl-transport-004: 会话所有权与生命周期

## 状态

accepted

## 1. 背景

**业务背景**：集成在三个不同时机创建 `EpsonApi`：配置流的一次性连通性探测（`config_flow.py:38-44`、`:76-82`）、setup 时的首检与身份解析（`__init__.py:170-192`）、以及运行期由协调器与实体长期共享的那一个实例（`__init__.py:195`）。

**技术背景**：`aiohttp.ClientSession` 的创建/销毁是有成本的（连接池、DNS 缓存、事件循环绑定），且 HA 推荐复用 `async_get_clientsession(hass)` 而非自建。但本模块**刻意不 import HomeAssistant**（`api.py:1-3`），因此拿不到那个帮助器；同时单测需要在无网络、无 HA 的环境下断言请求体（`tests/test_api.py:64-97` 的 `FakeSession`）。

**核心问题**：一个不能依赖 HA 的传输层，如何在"生产环境复用连接池"与"测试环境可伪造"之间取得平衡，同时不把 session 的所有权搞错？

**约束条件**：

- 不得 import homeassistant（`api.py:1-3`）。
- 单测必须在纯 Python 环境跑通（实测 `python3 -m unittest discover -s tests` → 64 tests OK）。
- 卸载路径必须释放资源（`__init__.py:213-221`），否则 HA 会报"未关闭的 session"。

## 2. 决策驱动因素（Decision Drivers）

1. **可测试性**（重要性：高）：会话必须能被 fake 替换，且替换后不改变任何请求构造逻辑。
2. **不泄漏连接**（重要性：高）：卸载、配置流失败、setup 失败三条路径都必须释放。
3. **不误关别人的 session**（重要性：中）：若宿主导入了共享 session，本模块不得替宿主关闭它。
4. **零 HA 依赖**（重要性：中）：保持 `api.py` 可被 `tools/` 脚本直接使用。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|Option A：构造注入 + 懒创建 + `_owns_session`|`session` 为可选构造参数；None 则首次请求懒建并置所有权为 True；`close()` 只关自己拥有的|可测性最好（注入 fake）；宿主可复用连接池；零 HA 依赖|三处缺口：所有权不复位、无 `_closed` 标志、借用 session 被关后会夺权|卸载后迟到协程静默重建（`risk.md` R-T05）|
|Option B：总是自建，不接受注入|构造时即 `ClientSession()`|所有权清晰，无歧义|无法复用 HA 连接池；单测只能 patch 模块内部，`FakeSession` 注入点消失（需 monkeypatch `aiohttp.ClientSession`）|每条命令一个新连接池，对单线程固件更不友好|
|Option C：用 `async_get_clientsession(hass)`|按 HA 最佳实践复用全局 session|连接池共享、HA 官方推荐|必须 import homeassistant，破坏 `api.py:1-3` 的解耦目标；`tools/` 脚本无法使用|传输层与 HA 生命周期耦合|
|Option D：每次请求新建 session（`async with ClientSession()`）|无状态|无生命周期问题|每条命令新建连接池，诊断轮 30+ 次建连；性能不可接受|高延迟 + 端口耗尽风险|

### Option A：构造注入 + 懒创建 + 所有权标志

`__init__` 接受 `session: aiohttp.ClientSession | None = None`（`api.py:71-85`），记录 `_owns_session = session is None`（`api.py:81`）。`_ensure_session()` 在 `_session is None or _session.closed` 时新建，并把 `_owns_session` 置 True（`api.py:93-97`）。`close()` 只在 `_owns_session and _session is not None` 时真正关闭，随后把 `_session` 置 None（`api.py:87-90`）。

调用方按场景选择：
- 生产：`EpsonApi(host, port)` 不传 session，由模块自建自管（`__init__.py:170`、`config_flow.py:38`、`:76`）。
- 单测：`EpsonApi(host, port, session=FakeSession(...))`（`tests/test_api.py:96-97`），完全不触网。

### Option B：总是自建

所有权绝对清晰，但 `FakeSession` 注入点消失——测试必须 monkeypatch `aiohttp.ClientSession` 类，而 `tests/test_api.py:64-97` 现有设计是"传一个记录了 calls 的假对象"，断言到 `session.calls[1]["json"]` 的粒度（如 `:235-245`）。改为 patch 类会让断言变脆。

### Option C：复用 HA 全局 session

HA 官方推荐做法，但与本模块"可脱离 HA 单测"的基石冲突（`api.py:1-3` 是文件头的显式声明，也是 `principle-global-001`）。此外 `tools/` 下 26 个探测脚本直接 `EpsonApi(host, port)` 构造使用，无法提供 `hass`。

### Option D：每请求新建

实现最简，但诊断轮 30+ 条命令 = 30+ 次连接池创建，对单线程固件与 HA 事件循环都是负担。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

- 对因素 1：注入点是本模块唯一的可替换接缝，且不改变请求构造路径——`request()` 只通过 `_ensure_session()` 拿 session（`api.py:176`），fake 与真 session 走完全相同的代码。
- 对因素 2：三条失败路径都显式 `close()`：setup 首检失败（`__init__.py:175`）、首轮协调器刷新失败（`__init__.py:192`）、卸载（`__init__.py:219`）；配置流用 `finally: await api.close()` 保证异常路径也释放（`config_flow.py:42-44`, `:80-82`）。
- 对因素 3：`_owns_session` 保证不会替宿主管 session（`api.py:88`）。
- 对因素 4：零 HA import 得以保持。

### 4.1 实施要点

- **懒创建而非构造时创建**：构造发生在配置流里，此时可能还没有运行中的事件循环（`config_flow.py:38` 在 `async_step_user` 内，其实有循环，但懒创建让构造保持同步、可用于 `tools/` 脚本的同步上下文）。实际效果是"第一次 `request` 才建 session"（`api.py:176`）。
- **`_ensure_session` 同时处理"未创建"与"已关闭"**：`_session is None or _session.closed`（`api.py:94`）。
- **`close()` 后把 `_session` 置 None**（`api.py:90`），使对象处于"可重新懒建"状态，而不是"永久死亡"。

### 4.2 已知缺口（本决策的代价，必须显式记录）

|缺口|代码事实|后果|
|-|-|-|
|所有权标志不复位|`close()` 只置 `_session=None`，`_owns_session` 保持 True（`api.py:87-90`）|第二次 `close()` 无害（`_session is None` 短路），但语义上"已关闭的借用者"仍宣称拥有权|
|无 `_closed` 标志|`_ensure_session` 不检查是否已 close（`api.py:93-97`）|卸载后迟到的协程会静默重建 session，连接泄漏且无法在日志中察觉|
|借用 session 会被夺权|外部传入的 session 若被外部关闭，`_ensure_session` 新建并置 `_owns_session=True`（`api.py:94-96`）|宿主以为自己在管 session，实际已被模块接管；未来若接 `async_get_clientsession` 会造成双重所有权|

缓解建议见 `risk.md` R-T05：加 `_closed` 布尔，`close()` 后 `_ensure_session` 直接抛错并复位 `_owns_session`。

## 备注

- 该决策与 `principle-global-001`（传输层与 HA 解耦）互为因果：正因为不 import HA，才需要自管 session；也正因为自管，才必须显式声明所有权。
- `close()` 是幂等的（`_session` 已 None 时短路），因此多条失败路径重复调用是安全的——这一点被 `config_flow.py:42-44` 的 `finally` 与 `__init__.py:175`/`:192` 的重复调用实际依赖。
- 未来若 HA 要求必须复用 `async_get_clientsession`，正确做法是在 `__init__.py` 侧构造并注入，而不是让 `api.py` 认识 hass——本决策的注入点已经为该演进预留了位置。
