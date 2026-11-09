---
project: "ha-epson-ef-projector"
type: adr
id: ADR-entity-platforms-003
description: "43/58 个实体（button 30、switch 6+3、select 4）采用『一个类 + 一张 const 元组表 + 一次推导』的表驱动装配，而非逐类/逐实体建模。理由是这些实体只在命令名、值域、图标、翻译键上不同，逐类建模会产生 43 个近乎相同的类；代价是元组宽度成为跨文件契约，且表驱动项无法承载个体差异行为。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "以 const 元组表驱动 43/58 个同构实体"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/const.py
  - custom_components/epson_ef/button.py
  - custom_components/epson_ef/switch.py
  - custom_components/epson_ef/select.py
  - custom_components/epson_ef/coordinator.py
  - tests/test_const_contract.py
---

# ADR-003: 以 const 元组表驱动 43/58 个同构实体

## 状态

accepted（4 张表的宽度契约由 AST 测试守护）

## 1. 背景

**业务背景**：投影仪固件把"设置项"抽象成统一的 `CONFIG_GET_*`/`CONFIG_SET_*` 命令对，参数恒为 0/1 或几档数字；遥控器则把动作抽象成 `NOTIFY_*` 通知 ID。因此 58 个实体中有 43 个在行为上完全同构——差别只有：命令名、值域、图标、翻译键、unique_id 后缀。

**技术背景**：本模块有 17 个实体类覆盖 58 个实体。其中 4 个类通过解包元组表来批量实例化：

| 表 | 行数 | 元组宽度 | 消费点 | 证据 |
|-|-|-|-|-|
|`WRITABLE_SWITCHES`|6|4（suffix, key, get_cmd, set_cmd）|`switch.py:36-39` 与 `coordinator.py:92-96`|`const.py:53-66`|
|`WRITABLE_SELECTS`|4|6（+ options, labels）|`select.py:38-42` 与 `coordinator.py:97-101`|`const.py:75-92`|
|`AMBIENT_LIGHT_SWITCHES`|3|4（suffix, key, icon, kind）|`switch.py:43-46`|`const.py:138-142`|
|`BUTTON_DEFS`|30|5（suffix, key, icon, cmd_id, params）|`button.py:92-98`|`button.py:17-83`|

合计派生 43 个实体（74%）。剩余 15 个是手写实例化，因为它们各自有**不可表化的个体差异**：`media_player`（状态机 + 锁）、`sensor` ×2（不同解析与单位）、`number` ×4（不同值域与读源）+ `color_gain` ×3（生成器派生，但构造需额外 `channel`）、`select` ×2（启动模式需专门映射函数、氛围灯模式需 assumed_state）。

**核心问题**：43 个"只在参数上不同"的实体，应该逐类建模还是数据驱动装配？

**约束条件**：

- 同一份表被**两个消费者**解包：平台装配（`switch.py`/`select.py`/`button.py`）与状态回读（`coordinator.py`）。两侧宽度必须一致。
- 元组表若增列（如 `WRITABLE_SELECTS` 从 5 元组扩到 6 元组），所有解包点必须同步。
- 表驱动项无法承载个体化行为（例如某开关需要写后延时、某按钮需要两次调用）。

## 2. 决策驱动因素（Decision Drivers）

1. **实体数量的增长速度**（重要性：高）：30 个按钮逐个建模 = 30 个 10 行的类，且每加一个按钮要改 3 个文件。
2. **跨文件契约的一致性**（重要性：高）：表被两处解包，宽度漂移会造成运行时 `ValueError`。
3. **可测试性**（重要性：中）：表是纯数据，可用 AST/不导入 HA 的方式静态校验。
4. **个体差异的表达力**（重要性：中）：少数实体有特殊行为，不能被表压平。

## 3. 候选方案（Considered Options）

|方案|简述|优点|缺点|风险|
|-|-|-|-|-|
|Option A|**一个类 + const 元组表 + 推导式实例化**|43 个实体只需 4 个类；新增按钮=加一行；表可静态校验|元组宽度成为跨文件契约；无法表达个体差异；元组第 2 列（translation_key）当前与第 1 列恒等，属冗余|宽度漂移导致整平台 setup 失败（历史事故，`tests/test_const_contract.py:1-9`）|
|Option B|每实体一个类（43 个类）|个体差异表达自由；IDE 跳转直观|43 × ~30 行样板；改公共逻辑要改 43 处；`strings.json` 与类名仍需手工对齐|漂移面从"元组宽度"变成"43 个类的行为分叉"|
|Option C|工厂函数 + dict 配置（每实体一个 dict）|可读性优于元组；可带默认值|仍是数据驱动，但失去元组的定宽校验能力（dict 少键不报错，只会在运行时 KeyError）|静默缺键|
|Option D|用一个"万能实体"类 + 运行时分支参数（type 字段）|单类覆盖全部|把 43 种语义压进一堆 `if kind == ...`；类型与行为高度耦合|不可维护|

### Option A：表驱动

```python
# const.py：纯数据，可被 AST 解析
WRITABLE_SWITCHES = [
    ("auto_correction", "auto_correction",
     "CONFIG_GET_AUTO_CORRECTION", "CONFIG_SET_AUTO_CORRECTION"),
    ...
]
# switch.py：一次推导
[Ef50ConfigSwitch(..., suffix, key, get_cmd, set_cmd)
 for suffix, key, get_cmd, set_cmd in WRITABLE_SWITCHES]
# coordinator.py：同一张表驱动状态回读
for suffix, _key, get_cmd, _set in WRITABLE_SWITCHES: ...
```

同一份数据既是"要建哪些实体"的声明，也是"要轮询哪些命令"的声明——**实体的存在与它的数据来源由同一行定义**，这是 Option A 相对 Option B 的关键优势。

### Option B：逐类建模

为 `auto_correction`/`auto_fit_wall_color`/... 各写一个类，每个类 30 行（属性 + `is_on` + `_write` + `async_turn_on/off`），共约 1300 行，且这 1300 行除命令名外完全相同。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

1. 对照 D1：30 个按钮的增删成本从"3 文件 × 30 处"降到"1 行"（`button.py:17-83`）。
2. 对照 D2：宽度漂移风险被显式承认并工程化对冲 —— `tests/test_const_contract.py` 用 AST 同时求"表行宽度"与"全部解包点宽度"（含推导式，`:59-87`），并锁定解包点位置（`:127-142`）。这是 Option B 不具备的保障类型（Option B 的漂移是语义级的，无法静态发现）。
3. 对照 D3：表是纯字面量，测试无需导入 `homeassistant`，可在无 HA 的 CI 中运行（`tests/test_const_contract.py:1-15` 明示此意图）。
4. 对照 D4：个体差异通过"**不进表**"解决——`Ef50StandbyModeSelect`（需要中文映射函数）、`Ef50AmbientModeSelect`（需要 assumed_state）、`Ef50MediaPlayer`（需要状态机 + 锁）都是手写类。表只收"真正同构"的项。

### 4.1 实施要点

- **一行表项 = 一个实体 + 一条轮询命令**：新增设置项时同时得到实体与回读，无需改协调器（`coordinator.py:92-101` 自动遍历）。
- **元组定宽**：禁止"某些行多一列"；需要新字段就整表升级并同步所有解包点。
- **个体差异不进表**：需要特殊行为的实体写独立类（现有 6 个例外）。
- **测试即契约**：任何表的宽度/解包点变更必须同步 `tests/test_const_contract.py` 的 `TABLE_SOURCES`（当前不含 `AMBIENT_MODE_OPTIONS`，因为它靠 `dict()`/下标消费，不受解包宽度约束）。
- **第 2 列冗余的处置**：`suffix == translation_key` 在 15/15 个 setting 项与 30/30 个按钮项上恒成立（实测），第二列目前是纯冗余；但它为"翻译键与 settings 键解耦"保留了扩展位，暂不删除。

## 备注

- 表驱动的**未覆盖面**正是模块最大风险的来源：表只锁住了"宽度"，没锁住"跨表后缀唯一性"（两张表同后缀会在扁平的 `settings` 字典里静默互相覆盖，`coordinator.py:92-101`）与"实体集与 legacy 清理表不相交"（`__init__.py:146-150`）。这两条应补进同一份 AST 测试。
- Option A 的一个副作用值得记录：`Ef50ConfigSwitch` 与 `Ef50ConfigSelect` 都保存了 `_get_cmd`（`switch.py:75`、`select.py:71`）却从不使用——因为回读被上移到协调器。这是表驱动的"列语义漂移"：该列对装配无用、对轮询有用（`coordinator.py:92-101` 用它发 GET），但被错误地也传进了实体构造。
