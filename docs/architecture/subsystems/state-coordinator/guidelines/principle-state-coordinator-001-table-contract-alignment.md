---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-state-coordinator-001
description: const.py 的 4 张声明式表是跨文件契约：宽度必须与全部解包点严格一致，第一列后缀必须与 settings 键、unique_id 尾段、legacy 清理表三方对齐。任何宽度或后缀变更必须同时改全部消费点并由 AST 级静态测试守护，否则失败模式是「慢轮抛 ValueError → 全集成 unavailable」。
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
principle_category: 架构
principle_scope: module
---

# 声明式表契约的对齐纪律

## 原则详细描述

本模块用 4 张元组表把「哪些设置可写」从代码里剥离成数据（`const.py:53-66`, `:75-92`, `:138-142`, `:158-163`），由通用实体类遍历建实体（`switch.py:34-47`、`select.py:33-45`），并由协调器遍历回读当前值（`coordinator.py:92-101`）。同一张表被**至少两个文件、两条完全不同的路径**解包。

因此每张表都背负两份契约：

1. **宽度契约**（横向）：表定义的行元组宽度 == 每个解包点的目标名字个数。Python 的元组解包是严格的，不一致即 `ValueError`。
2. **后缀契约**（纵向）：第一列「唯一后缀」同时是三个地方的主键——
   - `settings` 字典的键（`coordinator.py:95`, `:100`）；
   - 实体 `unique_id` 的尾段 `f"{base_id}-setting-{suffix}"`（`switch.py:78`、`select.py:75`）；
   - 升级时清理旧实体的匹配依据 `f"{base_id}-{suffix}"`（`__init__.py:146-150`）。

这两份契约的失败模式都极其恶劣：宽度错误**只在慢轮爆发**（首轮或第 12 轮），一旦爆发 `_async_update_data` 抛异常、`last_update_success=False`、**全部 58 个实体同时变 unavailable**；后缀错误则静默地把已有用户的实体重命名，历史记录断裂。

这不是理论风险。仓库里记录了一次真实事故（`tests/test_const_contract.py:1-9`）：3.10.0 发布前 `WRITABLE_SELECTS` 从 5 元组扩到 6 元组（新增 options 列），`coordinator.py` 仍按 5 元组解包，静态测试与单元测试都没抓到，真机部署后每次诊断轮询都抛 `ValueError: too many values to unpack`，整个集成所有实体 unavailable。

## 为什么要这样

1. **失败点与暴露点距离太远**：改表在 `const.py`，炸在 `coordinator.py:97`，中间隔着 12 轮节流。没有静态护栏时，从改动到发现平均需要「跑满一个慢轮周期」，且发现时已经是全集成不可用。
2. **表被多处消费，任何一处漏改都致命**：`WRITABLE_SWITCHES` 有 2 个解包点（`switch.py:38`、`coordinator.py:92`），`WRITABLE_SELECTS` 有 2 个（`select.py:40`、`coordinator.py:97`）。人能记住一处，记不住四处。
3. **后缀是隐式主键，改它等于改用户数据**：`unique_id` 变了 HA 就认为是新实体，旧实体的历史、自动化引用、仪表盘全部断掉。这个代价不会在 code review 的 diff 里显形——diff 只是把 `"vmute"` 改成 `"video_mute"`。
4. **类型标注靠不住**：本仓库就有反例——`coordinator.py:63` 的 `probes` 标注为 4 元组而实际是 3 元组。既然标注会漂移，护栏就必须读**实际字面量**，这就是 `tests/test_const_contract.py` 用 AST 而不 import 的原因。

## 适用范围

- `const.py` 的 4 张表：`WRITABLE_SWITCHES`、`WRITABLE_SELECTS`、`AMBIENT_LIGHT_SWITCHES`、`AMBIENT_MODE_OPTIONS`
- `button.py:17-83` 的 `BUTTON_DEFS`（5 元组，30 行，同属声明式表家族）
- 任何新增的「表 + 通用类」组合

## 规则

- **加列/减列时，必须 grep 全包内该表名的全部解包点**，一处不漏地同步修改宽度。解包点现状：`WRITABLE_SWITCHES` → `switch.py:38`、`coordinator.py:92`；`WRITABLE_SELECTS` → `select.py:40`、`coordinator.py:97`；`AMBIENT_LIGHT_SWITCHES` → `switch.py:45`。
- **改第一列后缀前，先确认它是否已被发布**（查 `__init__.py:146-150` 的 legacy 表与 `strings.json`）。若已发布，必须同时在 legacy 清理表登记旧后缀，否则老用户的实体变成永久 unavailable 的孤儿。
- **宽度用 AST 静态测试守护，不依赖类型标注**。新表必须登记进 `tests/test_const_contract.py:27-32` 的 `TABLE_SOURCES`，并补进 `:129-136` 的 `expected` 集合。
- **表项必须与 `strings.json` 的翻译键一一对应**（`switch.py:77` 的 `_attr_translation_key = key`）。缺键不报错，只显示原始键名——属静默劣化。
- **不要在没有护栏的情况下用「不产生元组解包 AST 节点」的方式消费表**（如 `dict(table)`、`[x for x, _ in table]`）——那样 `test_const_contract` 看不到它。`AMBIENT_MODE_OPTIONS` 正是因此逃逸（`select.py:180`, `:187`），见 risk.md R-SC-06。

## 反模式 / 禁止项

- ❌ 只改 `const.py` 的表定义就提交，没跑 `python3 -m pytest tests/ -q`。
- ❌ 用 `for row in table: suffix = row[0]` 绕过宽度契约同时删掉测试覆盖——索引访问把「编译期契约」降级成「运行期越界」。
- ❌ 在表里塞第四种列语义（现在的列语义已经分四类：后缀/翻译键/命令/载荷），却不在表头注释里更新 `(唯一后缀, 翻译键, GET 命令, SET 命令)` 这段说明。
- ❌ 把宽度不一致当成「风格问题」放过——它的后果是全集成不可用。
- ❌ 仅靠类型标注 `list[tuple[str, str, str, str]]` 当护栏（本仓库 `probes` 已证标注会漂移）。
- ❌ 改动 `unique_id` 后缀却不更新 `__init__.py:146-150` 的 legacy 清理表。

## 修改检查清单

- [ ] 该表的**全部**解包点是否都已同步（`grep -n "TABLE_NAME" custom_components/epson_ef/*.py`）？
- [ ] `python3 -m pytest tests/ -q` 是否全绿（尤其 `test_const_contract.py`）？
- [ ] 新表是否登记进 `TABLE_SOURCES` 与 `expected`？
- [ ] 改的是第一列后缀吗？若是——`strings.json` 是否同步？legacy 清理表是否登记旧后缀？
- [ ] 新列语义是否写进表头上方的 `(列1, 列2, ...)` 注释？
- [ ] `strings.json` 的对应 `entity.<platform>` 下是否补齐翻译键？
- [ ] 若该表以 `dict()` / 索引方式消费（无解包 AST 节点），是否单独加了断言？
