---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-entity-platforms-001
description: 实体层写动作必须经由 api.py 的语义方法，不得在平台文件里拼固件命令串。当前 40/55 个可写实体（button 30、switch 6、select 4）仍走 api.request(cmd, params) 裸接口，导致同一份协议知识存在两份编码：api.py 的语义方法与实体层的字符串字面量，二者会独立漂移。
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
principle_category: 架构
principle_scope: module
---

# 写动作必须走语义方法，禁止实体层拼命令串

## 原则详细描述

`api.py` 是唯一被授权"知道固件命令长什么样"的地方——它把 `CONFIG_SET_AMBIENT_LIGHT "3 1 48"` 这类参数拼装、`size` 语义、错误分类都封在 `EpsonApi` 里。但实体层有三处绕过了它，直接用 `coordinator.api.request(cmd, params)` 下发裸命令：

- `button.py:138` —— 30 个按钮全部走裸串，命令与参数来自 `BUTTON_DEFS` 表（`button.py:17-83`）；
- `switch.py:106` —— 6 个配置开关，命令来自 `WRITABLE_SWITCHES`（`const.py:53-66`）；
- `select.py:107` —— 4 个档位选择器，命令来自 `WRITABLE_SELECTS`（`const.py:75-92`）。

合计 **40 个实体**走裸串，**15 个实体**走语义方法（`media_player` 1、`select` 2、`switch` 5、`number` 7）。

这不是"还没重构完"，而是**已经重构完却没用上**：`api.py` 为几何类命令提供了真机验证过的语义方法 —— `geometry_zoom`（`api.py:590-598`）、`geometry_shift`（`:600-608`）、`geometry_reset`（`:610-618`）—— 但生产调用者为 **0**；`button.py:68-82` 用 7 行裸串 `CONFIG_SET_QC` 覆盖了同样的功能。同类情况还有 `focus_step`（`:516-529`）、`get_light_output`（`:366-374`）、`get_color_gain`（`:432-447`）、`get_distance_mm`（`:353-361`）、`get_auto_focus_state`/`get_auto_focus_target`（`:634-656`）、`get_picture_preference`/`set_picture_preference`（`:380-385`）、`get_light_time`（`:330-338`）—— AST 实测 `api.py` 共 **14 个公开方法在 `custom_components/` 内零生产调用者**：其中 12 个连 `api.py` 内部也不调用（`focus_move_start`/`focus_move_stop` 由 `goto_focus` 内部使用，非死代码）；这 12 个里有 9 个属于"封装完备但被实体层裸串绕过"，另 3 个（`get_picture_preference`/`set_picture_preference`/`get_light_time`）连 tools 与 tests 也几乎不用。

后果是可度量的：`CONFIG_SET_QC` 的子命令表同时写在 `const.py:165-177`（注释）与 `api.py:590-618`（实现）与 `button.py:68-82`（实参）三处；改动步长常量 `QC_ZOOM_STEP` 时，`api.py` 侧自动生效，`button.py:69` 的 f-string 也引用常量因而同样生效，但**参数结构**（`"1 {step}"` vs `"1 104"`）的修改需要动三处。

## 为什么要这样

1. **协议知识的单点持有**。`api.py` 的 docstring 明示它零 HA 依赖、可离线单测（`api.py:1-3`），`tests/test_api.py` 有 60+ 用例覆盖每个方法的 payload。裸串路径完全没有测试——`tests/` 里没有任何平台层用例，所以 `button.py` 的 8 个 `CONFIG_SET_QC` 参数格式**没有任何自动化保护**，只能靠真机验证。
2. **错误语义统一**。语义方法内部 `await self.request(...)`，异常类型天然一致；裸串路径因为要自己 `try/except`，已经长出两种不一致写法：`switch.py:107`/`select.py:108` 用 `except Exception`（宽捕获），`button.py:139` 用 `except EpsonApiError`。
3. **可验证性**。`tools/` 下的现场分析脚本复用 `api.py` 方法做真机回环（如 `tools/verify_full.py`），若生产路径走裸串，脚本验证的东西与线上跑的东西不是同一段代码。

## 适用范围

- **强制**：所有新增的可写实体。
- **强制**：修改既有 `BUTTON_DEFS` / `WRITABLE_SWITCHES` / `WRITABLE_SELECTS` 的命令列时——要么改走语义方法，要么至少让命令串引用 `const.py` 常量而非字面量。
- **豁免**：`button.py` 中的纯通知类命令（`NOTIFY_APP_*` 等 21 个不同命令 ID），其参数为 `None` 或固定串，语义方法的价值仅在于命名；可保留裸串，但命令名仍需常量化。

## 规则

- 实体层的 `async_turn_on` / `async_set_value` / `async_select_option` / `async_press` **不得出现固件命令字符串字面量**。
- 新增固件命令时，先在 `api.py` 加语义方法 + `tests/test_api.py` 用例，再在实体层调用。
- 表驱动的 `(get_cmd, set_cmd)` 列若保留，必须引用 `const.py` 中定义的常量，不得内联 `"CONFIG_SET_X"`。

## 反模式 / 禁止项

- ✗ 在实体里写 `await self.coordinator.api.request("CONFIG_SET_QC", params=f"2 {dx} 0")`。
- ✗ 在 `BUTTON_DEFS` 里内联命令字符串（当前 `button.py:17-83` 全是内联）。
- ✗ 为"省一次跳转"而把 `size` 计算、参数拼装搬进实体。
- ✗ 用 `except Exception` 兜住裸串调用（见 `switch.py:107`、`select.py:108`）。

## 修改检查清单

- [ ] 新增写路径是否调用了 `api.py` 的语义方法？若否，理由是否记录在注释中？
- [ ] `api.py` 是否已存在同名/同族的零调用者方法（先用 `grep -n "\.<method>(" custom_components/` 确认）？
- [ ] 若必须裸写，命令名是否来自 `const.py` 常量？
- [ ] 异常捕获是否为 `EpsonApiError` 而非 `Exception`？
- [ ] 是否补了对应的 `tests/test_api.py` payload 断言？
