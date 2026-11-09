---

project: "ha-epson-ef-projector"

type: adr

id: ADR-pjctl-transport-001

description: "Digest 认证采用『被动挑战 + 进程内缓存 nonce + 每请求递增 nc + 401 单次重试』：首次请求裸发，收到 401 才解析并缓存三元组；此后每条命令直接带 Authorization 头；仅当 401（attempt 0）或传输层失败时重建/清空缓存。"

base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3

status: accepted

title: "Digest 认证缓存 nonce 并只重试一次 401"

date: 2026-11-07

code_refs:
  - custom_components/epson_ef/api.py
  - custom_components/epson_ef/const.py
  - tests/test_api.py

---

# ADR-pjctl-transport-001: Digest 认证的缓存与单次重试策略

## 状态

accepted

## 1. 背景

**业务背景**：集成每 15s 轮询一次（`const.py:11`），唤醒态的诊断轮一次要发 30+ 条命令（`coordinator.py:59-104`），每条命令都是一次 HTTP POST。用户写操作（亮度、对焦、几何）也各需一次往返。

**技术背景**：投影仪 `WebApiSrv` 只支持 HTTP Digest-MD5，且凭据是固件内置常量（`const.py:15-19`：user `EPSONICL`、realm `Secret Zone`、password 硬编码）。Digest 的 nonce 由服务端在 401 挑战中给出，客户端无权预取。`aiohttp` 自带 `DigestAuth`，但本模块刻意不 import HA 且希望认证行为可离线单测（`api.py:1-3`）。

**核心问题**：在"每条命令都要认证"的前提下，如何把认证开销压到最低，同时保证非会话 cookie 型认证在 nonce 轮换时不会永久卡死？

**约束条件**：

- 必须纯 aiohttp + stdlib，不得引入 HA 的 `aiohttp_client` 帮助器（`api.py:1-3`）。
- 认证状态是实例级共享的（`api.py:84-85`），多个协程会并发使用同一个 `EpsonApi`（协调器 + 实体写 + 服务透传）。
- 固件的 nonce 有效期未知，且深待机会挂断连接（`api.py:248-251`）。

## 2. 决策驱动因素（Decision Drivers）

1. **往返次数**（重要性：高）：诊断轮 30+ 条命令，每条多一次 401 就多 30 次往返，直接放大单线程固件的压力。
2. **并发正确性**（重要性：高）：Digest 要求同一 nonce 下 `nc` 单调递增且不重复，否则服务端可判为重放。
3. **可离线单测**（重要性：中）：认证逻辑必须能在无设备、无 HA 的环境断言（`tests/test_api.py:100-118` 直接断言 `nc=00000001/00000002`）。
4. **故障自愈**（重要性：中）：nonce 轮换、设备重启、深待机恢复后必须能自动恢复，不能要求重启集成。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|Option A：被动挑战 + 缓存 + 单次 401 重试|首次裸发，401 才取 nonce；缓存后每命令直接带 Authorization；401（attempt 0）换挑战重发一次，传输失败清缓存|正常路径零额外往返；实现小（约 45 行）；可离线断言 `nc` 序列|缓存无 TTL，nonce 轮换时每条命令仍要付一次 401（但只一次）；清缓存是全局的，一个协程失败影响其他协程|并发清缓存会短暂退化（risk R-T04）|
|Option B：每条命令完整挑战（不缓存）|每条请求先发一次无认证请求，收 401 后带认证重发|无需维护状态，天然免疫 nonce 轮换与并发污染|每条命令恒 2 次往返，诊断轮 30+ 命令 → 60+ 次 HTTP；对单线程固件压力翻倍|高负载下更容易触发固件挂起|
|Option C：用 `aiohttp.DigestAuth` 或 `httpx.DigestAuth`|把认证交给库|代码最少，nonce 管理由库负责|`aiohttp.DigestAuth` 每次请求仍走挑战（行为等价 B）；引入新依赖与 `manifest.json:requirements=[]` 冲突；认证行为不可断言 `nc` 序列|库行为黑盒，测试只能看最终请求头|

### Option A：被动挑战 + 缓存 + 单次重试

首次请求不带 `Authorization`（`api.py:182-184` 的 `_digest_header` 在 `_auth is None` 时返回 None）。收到 401 后解析 `WWW-Authenticate`，正则抓 `nonce`/`realm`/`qop` 三段（`api.py:101-110`），缓存三元组并把 `_nc` 归零（`api.py:111`）。重试时 `_digest_header` 同步自增 `_nc` 并格式化为 8 位十六进制 `nc`，配随机 `cnonce` 计算 `response`（`api.py:119-145`）。`for attempt in range(2)` 保证最多一次重试（`api.py:180`），第二次仍 401 则落普通 HTTP 错误分支（`api.py:205-206`）。超时与 `ClientError` 都清空缓存（`api.py:207-214`），下一次请求重新挑战。

### Option B：每条命令完整挑战

把 401 处理从循环里拿出，改为"先探一次 401 → 存 nonce → 正式请求"。实现更简单（无需 attempt 计数），但诊断轮的命令数翻倍。

### Option C：交给库

`aiohttp.DigestAuth` 内部行为与本项目 Option A 接近，但：(a) 它不暴露 `nc` 计数，`tests/test_api.py:105-113` 这类断言无法写；(b) 它会把凭据校验放在库内部，与本项目"凭据来自固件常量、必须可见"的定位不符。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

- 对因素 1（往返次数）：缓存后正常路径零额外往返，只有 nonce 轮换那一轮多一次；Option B 恒定翻倍。
- 对因素 2（并发正确性）：`_nc += 1` 是同步语句且位于 `_digest_header` 内（`api.py:123-125`），注释明确"concurrent coroutines can never send the same nonce-count twice"；没有 await 打断，asyncio 单线程下天然原子。
- 对因素 3（可离线单测）：`_update_auth`/`_digest_header` 是可直接调用的私有方法，测试断言到 `nc=00000001` 与 `qop=auth` 的粒度（`tests/test_api.py:105-113`），这是 Option C 给不了的。
- 对因素 4（故障自愈）：401 与传输失败两条路径都能重建（`api.py:200-204`, `:207-214`），无需人工干预。

### 4.1 实施要点

- 挑战解析必须**只认 nonce**：缺 nonce 直接返回 False 并抛 `EpsonApiError("401 without digest challenge")`（`api.py:102-103`, `:202-203`），避免把 Basic 挑战或空头当成有效认证。
- `realm` 缺省回退 `DIGEST_REALM`、`qop` 缺省回退 `"auth"`（`api.py:108-109`），保证畸形挑战下仍能算出与固件一致的摘要。
- 每次**重试都重新取头**（`api.py:182` 在循环内），因此换挑战后的请求使用新 nonce 与 `nc=1`；副作用是 `nc` 被多消耗一次，无功能影响。
- 循环末尾的 `raise EpsonApiError("digest authentication failed")`（`api.py:216`）在当前分支结构下不可达，属防御性兜底。
- 缓存无 TTL：`_update_auth` 只在 401 时调用，`_clear_auth` 只在异常时调用（`api.py:111`, `:116-117`）。

## 备注

- 已知残留：认证状态跨协程共享且"任一方失败即全局清空"，并发场景下会集体退化回挑战链（`risk.md` R-T04）。若未来引入并发写锁，可一并把 `_auth` 改为"每请求快照 + 原子替换"。
- 凭据为固件内置常量而非用户配置（`const.py:16-19`），因此不存在"每设备不同凭据"的分片需求——这也是可以安全使用单份实例缓存的隐含前提。若未来支持其他机型，该前提需重新评估。
- 明文 HTTP 使 Digest 只防"误操作"不防"嗅探"（`risk.md` R-T07）；这是固件限制，非本决策可解。
