---

project: "ha-epson-ef-projector"

type: adr

id: ADR-pjctl-transport-003

description: "命令封装采用『语义方法 + 通用 request 透传』双轨：为已验证的命令提供具名方法（参数模板固化、返回类型化、失败语义明确），同时保留公开的 request() 作为未验证命令与服务透传的出口。代价是语义门面存在后门，且已产生 12 个零调用者方法。"

base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3

status: accepted

title: "命令封装双轨制：语义方法 + 通用透传"

date: 2026-11-07

code_refs:
  - custom_components/epson_ef/api.py
  - custom_components/epson_ef/services.py
  - custom_components/epson_ef/button.py
  - custom_components/epson_ef/select.py
  - custom_components/epson_ef/switch.py

---

# ADR-pjctl-transport-003: 语义方法与通用透传并存

## 状态

accepted

## 1. 背景

**业务背景**：集成需要覆盖 7 个功能域（电源、光源、图像、几何、对焦、氛围灯、诊断）。其中一部分能力已被完整现场分析并做过真机回环验证（如 `CONFIG_SET_FOCUS "MAX"/"MIN"/"OFF"`、`CONFIG_SET_QC "5"`），另一部分只知道命令存在、参数语义未确认（如 `CONFIG_GET_QC` 的八 token 响应、若干 `NOTIFY_*` 触发）。

**技术背景**：本项目是现场分析工程的产物，"哪些命令可用"是**逐步发现**的，不是设计文档给定的。这导致两件事同时发生：

1. 已经验证的能力需要稳定的、可被实体安全调用的入口；
2. 未验证的能力需要一条不改代码就能试的通道（`services.raw_request`，`services.py:56-71`），否则每验证一条命令都要发版。

现状：`api.py` 里 34 个语义方法（14 个 `get_*` + 10 个 `set_*` + 10 个行为方法），同时 `request()` 保持公开并被包内 4 处直接调用（`button.py:138`、`select.py:107`、`switch.py:106`、`services.py:64`），另有 `tools/` 探测脚本与 `tests/` 直调。

**核心问题**：在"能力边界持续被发现"的语境下，传输层应该只暴露通用 `request(cmd, params)`，还是为每条命令提供具名方法，还是两者并存？

**约束条件**：

- 无代码生成、无 schema 注册表；所有方法手写。
- 平台层是"表驱动 + 直接调用"混合风格（`const.py:53-92` 的表驱动 vs `number.py:126` 的直接方法调用）。
- 方法 docstring 是唯一的知识载体，承载"真机实测结论"（如 `api.py:163-170`、`:519-527`）。

## 2. 决策驱动因素（Decision Drivers）

1. **参数模板正确性**（重要性：高）：`size` 必须等于 token 数（`api.py:171`），参数形状错一个字符就会被 MCU 以 FAIL 拒绝。让每个调用方自己拼 `params` 是把这条协议规则复制 N 份。
2. **失败语义可预判**（重要性：高）：读要返回 None、写要上抛（见 `principle-pjctl-transport-001`），这个二分只有具名方法能表达；通用 `request` 只能一律抛错。
3. **探索自由度**（重要性：高）：未验证命令必须能不经过代码评审就试，否则现场分析节奏被发版周期锁死。
4. **类型化返回**（重要性：中）：`"191 2971"` → `(191, 2971)` 的解析与容错（token 数校验、`int()` 异常）应在模块内做一次，而不是在每个消费方做。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|Option A：双轨（语义方法 + 公开 request）|已验证命令写成具名方法；`request()` 保持公开供透传|参数模板只写一次；读写失败语义分明；探索不受阻|门面有后门，危险命令可绕过；没有调用者的方法会变成死代码|门面与后门并存导致"平台层绕过语义方法"（已发生，`button.py:138` 等 4 处）|
|Option B：只保留通用 request|传输层只提供 `request(cmd, params)`|最薄、最灵活、无死代码|`size` 协议与参数形状在每个调用方重写；读写失败语义无法表达；类型解析分散到各平台|协议错误会以"某实体偶发失效"的形式出现，极难定位|
|Option C：只保留语义方法（request 私有）|`request` → `_request`；每条命令一个方法，未验证命令不暴露|门面完整、无后门|现场分析探索被锁死——任何新命令都要改代码；与 `services.raw_request` 的设计意图冲突|探索效率骤降；`tools/` 探测脚本无法复用客户端|
|Option D：数据驱动命令表（表 + 通用执行器）|用 `dict[cmd_id] -> (params_template, parser, failure_policy)` 描述全部命令|新增命令零代码；参数模板集中|表需要可执行逻辑（解析器、失败策略），Python 里最终仍要写函数；异常语义更难表达|表逐渐变成"半个解释器"，调试栈变深|

### Option A：双轨并存

- 语义方法把**命令 ID + 子索引 + 参数模板 + 解析 + 失败策略**五件事封装在一次（例：`set_ambient_light` 的 `kind` 分支产生三种参数形状，`api.py:387-398`；`get_color_gain` 要求恰好 3 token，`:441-447`）。
- `request()` 保持公开且是唯一底座，所有语义方法与 4 处包内直调都经过它（`api.py:147-216`）。
- `services.raw_request` 把 `id/params` 从 HA 服务原样透传（`services.py:56-71`），结果经 `epson_ef_response` 事件回传（`services.py:67-70`），失败被吞成 `{"error": ...}`。

### Option B：只保留通用 request

最薄的传输层。代价是 `size` 计算点从 1 处变成 N 处，读写失败契约消失，`"R G B"` 三 token 校验在每个消费方重写。本项目平台层已有 5 个平台在写动作上直调 `request`，说明这条路在实践中确实"够用"——但它让类型与语义无处安放。

### Option C：request 私有化

理论上最干净，但与 `services.raw_request`（调试服务）和 `tools/`（26 个现场分析脚本）直接冲突：两者都需要任意命令透传。

### Option D：数据驱动命令表

与 `const.py` 现有的声明式表（`WRITABLE_SWITCHES`/`WRITABLE_SELECTS`，`const.py:53-92`）风格一致，且已经证明可行——但那两张表只描述"0/1 与固定档位"，参数形状简单的场景。像 `set_ambient_light`（三种形状）、`goto_focus`（含控制流）无法表驱动，最终会变成"表 + 特例函数"的混合体，比双轨更难维护。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

- 对因素 1、4：`size` 与参数模板只在 `request`/语义方法里各写一次；解析与容错集中在模块内。
- 对因素 2：语义方法让"读→None / 写→上抛"的二分成为可检查的代码事实（`api.py:298-299` vs `api.py:325`）。
- 对因素 3：`request` 公开 + `services.raw_request` 保留了完整探索能力，`tools/` 26 个脚本全部依赖它。

### 4.1 实施要点

- 双轨的边界必须明确：**语义方法用于已验证、需要在实体上稳定暴露的能力；`request` 用于未验证命令、一次性探测与调试透传**。
- 新增命令时先问"是一次性验证还是要长期暴露"：前者用 `request` 或 `tools/`；后者才写语义方法，并同时在平台层接线。
- 语义方法的 docstring 必须承载实测结论与危险变体警告（`api.py:481-487` 是最完整的范例）。
- 参数模板必须唯一确定：一个写方法只产生一组参数形状，不接受自由字符串（`set_ambient_light` 用 `kind` 分支而非裸 params）。

## 备注

**这条决策的实际代价（必须记录，否则会被误读为"设计良好"）：**

1. **12 个零调用者方法**：`get_light_time`、`get_distance_mm`、`get_light_output`、`get_picture_preference`、`set_picture_preference`、`get_color_gain`、`focus_step`、`geometry_zoom`、`geometry_shift`、`geometry_reset`、`get_auto_focus_state`、`get_auto_focus_target` 在 `custom_components/epson_ef/` 内无任何调用者（AST + 全包 grep 实测）。其中 `geometry_zoom/shift/reset` 的功能被 `button.py:68-82` 用 `request` + 内联参数重新实现了一遍——即"语义方法被写出来，然后平台层绕过了它"。
2. **`request` 是后门**：`button.py:138`、`select.py:107`、`switch.py:106` 三处平台层直调意味着参数模板在 `const.py`/`button.py` 里又存在了一份（`button.py:17-83` 的 `BUTTON_DEFS` 表内联了 `f"1 {QC_ZOOM_STEP}"` 等参数串）。
3. **危险命令只能靠"不实现"屏蔽**（`principle-pjctl-transport-003`），而 `request` 的公开性使该约定可被绕过。
4. 缓解方向：把 `request` 降为 `_request`，另设显式的 `raw_request()` 作为唯一透传口并加命令/参数黑名单；同时把平台层的直调点回收成语义方法调用（`risk.md` R-T01/R-T08）。
