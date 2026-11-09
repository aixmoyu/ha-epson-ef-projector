---
project: "ha-epson-ef-projector"
type: adr
id: ADR-entry-core-004
description: "服务结果通过 epson_ef_response 事件回传，而不是让服务抛错或返回数据——因为 HA 服务调用没有返回通道。代价是错误被软化为事件载荷、无 correlation id、且错误分类丢失。raw_request 也因此成为无白名单、无 admin 门禁的任意命令面。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "raw_request 以事件回传结果，异常折为错误载荷"
date: 2026-11-01
code_refs:
  - "custom_components/epson_ef/services.py:56-94"
  - "custom_components/epson_ef/services.py:15-35"
  - "custom_components/epson_ef/api.py:147-216"
  - "custom_components/epson_ef/services.yaml:12-31"
  - "README.md:83"
  - "README.md:111"
---

# ADR-004: raw_request 以事件回传结果，异常折为错误载荷

## 状态

accepted（3.10.0；`require_admin` 至今未实现，见「已知局限」）

## 1. 背景

**业务背景**：集成需要两个"绕过实体"的入口——`send_key` 让用户在自动化里发任意遥控按键，`raw_request` 让高级用户在固件命令尚未被封装的阶段直接下发 pjctl 命令（README 定位为"调试用"，`README.md:83`）。

**技术背景**：pjctl 是一条请求-响应协议，`EpsonApi.request()` 返回解析后的 JSON dict，并在 `resultCode != 0` 时抛 `EpsonApiCommandFailed`（`api.py:147-216`）。HA 的服务（`hass.services.async_register`）没有返回值通道：`async def handler(call) -> None`，调用方（自动化 `service:` 动作、前端开发者工具）拿不到任何返回体。

**核心问题**：如何把一个"有响应体"的协议操作暴露成 HA 服务，让调用方能感知成功与失败？

**约束条件**：

- HA 服务处理器签名固定为返回 `None`（`services.py:43`、`:56`）。
- 该功能面向调试/高级场景，允许比实体路径更"裸"。
- 事件总线是 HA 唯一的异步回传通道。

## 2. 决策驱动因素（Decision Drivers）

1. **调用方必须能拿到设备响应**（重要性：高）：否则 `raw_request` 对调试毫无价值（看不到返回值）。
2. **失败不能让自动化炸掉**（重要性：中）：自动化里调用未知命令是常态，抛异常会中断整个自动化脚本。
3. **实现要小**（重要性：中）：一个调试用服务不值得引入 response 对象或 correlation 框架。
4. **与 `send_key` 的语义可区分**（重要性：低）：`send_key` 是"发出去就行"，`raw_request` 是"我要看结果"。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|---|---|---|---|---|
|Option A|结果（含异常）统一发 `epson_ef_response` 事件，服务本身不抛错|调用方总能收到回执；自动化不会因命令失败中断；实现只需 3 行|无 correlation id，并发调用无法配对；错误被软化为 `{"error": str}`；事件总线无权限过滤|中|
|Option B|异常直接向上抛，成功不发事件|语义最直白；错误能触发自动化 `on_error`|成功时调用方看不到响应体；自动化在命令失败时整体中断|中|
|Option C|注册 HA 的 `SupportsResponse`（service 声明 `supports_response=ONLY/OPTIONAL`）返回 dict|类型化、原生、可被 `response_variable` 接收|本项目最低 HA 版本 2024.4（`hacs.json:3`），`SupportsResponse` 是 2023.7 引入、`OPTIONAL` 语义在后续版本演进；需要同时改服务注册与文档；且本模块已按"调试用"定位设计|低（但改动面更大）|
|Option D|提供配套的 `epson_ef.raw_request_with_response` 双服务|兼容旧行为|两个几乎相同的服务，维护翻倍|中|

### Option A：事件回传 + 异常软化（已采纳）

```python
async def raw_request(call: ServiceCall) -> None:
    api = _resolve_api(hass, entity_id)
    command_id = call.data["id"]
    params = call.data.get("params")
    try:
        result = await api.request(command_id, params=params)
    except Exception as err:            # noqa: BLE001
        result = {"error": str(err)}
    hass.bus.async_fire(f"{DOMAIN}_response",
        {"entity_id": entity_id, "id": command_id, "response": result})
```
（`services.py:56-70`）

### Option B：抛错不广播

`send_key` 走的就是这条路——`await api.send_keyevent(keycode)`，异常冒泡（`services.py:54`），只有"未知键"才主动抛 `HomeAssistantError`（`:53`）。这条路径语义干净，但**无法满足驱动 1**：`raw_request` 的主要用途恰恰是"看看这条命令返回什么"，而 `request()` 的成功响应体是一个 JSON dict（如 `CONFIG_GET_PRODUCT_NAME` 返回产品名），不广播就等于丢弃。

### Option C：SupportsResponse

HA 在服务注册时支持 `supports_response=SupportsResponse.OPTIONAL`，调用方可用 `response_variable` 接收。这是"更正确"的现代做法，但：① 需要 `async_register(..., supports_response=...)` 并确认最低 HA 版本（`hacs.json:3` 声明 2024.4）；② 服务文档与 README 需同步；③ 与本模块"调试入口"的定位相比属于超出需求的改造。保留为未来演进方向。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

- 驱动 1：成功与失败都以事件携带 `response`（`services.py:67-70`），调用方订阅即可拿到设备原始返回。
- 驱动 2：`except Exception` → `{"error": str(err)}`（`:65-66`），自动化不会因未知命令中断。
- 驱动 3：三行代码，无新抽象。
- 驱动 4：`send_key` 保持"只发不看"的简单语义（`:54` 异常冒泡），与 `raw_request` 形成对照。

**被否决方案的关键否决点**：B 否决于驱动 1；C 否决于"超出需求 + 最低 HA 版本约束"；D 否决于维护成本。

### 4.1 实施要点

1. 事件名固定为 `f"{DOMAIN}_response"` → `epson_ef_response`（`services.py:68`）。
2. 载荷三键：`entity_id`（原样回传请求入参，若是列表则仍是列表）、`id`（命令 id）、`response`（dict，可能为 `{"error": ...}`）（`:69`）。
3. 事件**无论成败都发**，不存在"失败不发"的分支（`:63-70` 无 else）。
4. 服务 schema：`{id: str 必填, params: str 可选, entity_id: str 可选}`（`:87-93`），params 恒为字符串，token 数与 `size` 的换算在传输层完成（`api.py:163-172`）。
5. 目标条目解析：entity_id → 实体注册表 → `config_entry_id` → `hass.data[DOMAIN][entry_id]["api"]`；无 entity_id 时取首个带 `api` 的条目（`services.py:15-35`）。
6. 注册幂等：`has_service` 短路（`:40-41`）；注销随末条目卸载（`__init__.py:218-220`、`services.py:97-102`）。

### 4.2 已知局限（安全与语义）

- **无 `require_admin`**：全仓库不存在该装饰器（`grep -rn require_admin custom_components/` 为空），任何可调用服务的 HA 用户/自动化都能下发任意 `CONFIG_SET_*`，包括"立即开始刷新并关机"这类破坏性命令（`api.py:481-488` 自身有警示注释）。README 仅作文字提醒（`README.md:111`）。全局风险登记见 `architecture_views/risk.md` R-23。
- **无命令白名单、无参数校验**：只在 schema 层约束类型为 `str`。
- **异常捕获过宽**：`except Exception` + `noqa: BLE001`（`services.py:65`）把编程错误（`KeyError`/`AttributeError`）与设备错误混为一谈，事件里只剩一个字符串。
- **无 correlation id**：并发调用只能靠 `id` 字段区分，响应与请求无法严格配对。
- **`_resolve_api` 的"首个条目"兜底**：多设备场景下语义含糊（`services.py:31-35`，全局风险登记为容量风险）。

## 备注

- 两个服务的 UI 字段说明见 `services.yaml:1-31`；README 用法见 `README.md:82-83`、安全提示 `:111`。
- 若未来改为 `SupportsResponse`，需同步本 ADR 与 `module_design.md` 的外部接口表。
