---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-entity-platforms-002
description: 实体写成功后，乐观值必须同时回填 coordinator.data 与实体本地属性，并（对有读通路的能力）紧接着 async_request_refresh()。只改 self._attr_* 会在下一帧被陈旧的 coordinator.data 覆盖回去；只回填缓存不刷新则要等下一个慢诊断轮（最坏 180s）才被固件真值确认。
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
principle_category: 架构
principle_scope: module
---

# 乐观写要双写回填，并按能力决定是否刷新

## 原则详细描述

实体读值的优先级决定了回填位置。`Ef50LightOutputNumber.native_value` 的实现是"**轮询值优先，本地值兜底**"（`number.py:110-121`）：

```python
raw = ((self.coordinator.data or {}).get("settings") or {}).get("light_output")
if raw is not None:
    return float(raw)          # 协调器有值就用协调器的
return self._attr_native_value # 否则用恢复/乐观值
```

因此**只写 `self._attr_native_value` 是无效的**：只要 `settings` 里有旧值，下一帧渲染立刻回到旧值，用户看到滑条"弹回"。正确写法是双写，见 `number.py:129-132`：

```python
settings = (self.coordinator.data or {}).setdefault("settings", {})
settings["light_output"] = str(ivalue)   # ① 回填共享缓存
self._attr_native_value = float(ivalue)  # ② 回填本地
self.async_write_ha_state()              # ③ 立即上屏
await self.coordinator.async_request_refresh()  # ④ 请求权威回读
```

同一模式在 `select.py:110-113`（档位）、`number.py:216-220`（刷新定时器）、`number.py:392-396`（色彩增益）、`number.py:297-301`（对焦）、`select.py:160-164`（启动模式）重复出现。对焦那处还有个额外理由：`settings["focus_position"]` 只在慢诊断轮刷新（`coordinator.py:113-115`），而闭环定位有 ±容差偏差（实测目标 120 落到 122），所以回写的是**实测终值**而非目标值（`number.py:297-299`）。

**当前实现的两处例外，都是有意的但会带来代价的**：

- `switch.py:104-111` 的 6 个配置开关：双写做了（①②③），但**没有第 ④ 步**。后果是开关状态要等下一个慢诊断轮（`DIAGNOSTIC_EVERY_N_POLLS = 12` × 默认 15s ≈ 最坏 180s）才被固件真值确认；若固件实际未生效（如待机写入），界面会在 3 分钟内显示错误值。
- `media_player.py:207-216` 的静音：只写本地 `_attr_is_volume_muted`（③），**不碰** `coordinator.data`——因为设备侧根本没有静音状态可回读，回填缓存没有意义。这是"无读通路"情形，适用 principle-003。

## 为什么要这样

1. **渲染优先级是硬的**：`native_value`/`current_option`/`is_on` 的实现决定了哪份数据赢。不回填缓存 = 白写。
2. **收敛窗口由刷新时机决定**：有读通路时不刷新，乐观值就一直"自说自话"到下一个慢轮；慢轮间隔是 12 个轮询周期（`coordinator.py:59`），默认 15s/轮。
3. **失败时不回填**：所有实体的写法都是"`await api...` 抛错则 `raise HomeAssistantError`，成功才回填"（`number.py:126-133`、`switch.py:105-111`、`select.py:106-113`）。这保证缓存里永远是"最后一次被设备接受的值"，不会被失败的意图污染。

## 适用范围

- 全部有写方法且读值来自 `coordinator.data` 的实体：`select` 5 个、`switch` 6 个、`number` 7 个。
- **不适用**：`media_player`（状态来自 `state` 键且电源走 probe 确认真值，静音无读通路）、写-only 的 7 个实体（适用 principle-003）。

## 规则

- 写成功 → **必须**同时改 `coordinator.data` 里的对应键与 `self._attr_*`。
- 有读通路（协调器会在慢轮回读该键）→ **必须** `await self.coordinator.async_request_refresh()`。
- 写失败 → **禁止**做任何回填或本地翻转，直接 `raise HomeAssistantError(...) from err`。
- 回填的值必须是设备实际接受的值（对焦场景是实测终值，不是请求值）。

## 反模式 / 禁止项

- ✗ 只写 `self._attr_native_value`，不改 `settings[...]`（会被 `:116` 的轮询值覆盖）。
- ✗ 写入成功但既不刷新也不回填，指望"下一轮反正会读到"（用户先看到 3 分钟的错值）。
- ✗ 在 `except` 分支里也改本地状态（会造成"写失败但界面已变"的假象）。
- ✗ 用**目标值**而非**实测值**回填对焦位置（闭环有容差，界面会撒谎）。

## 修改检查清单

- [ ] 该实体的 `native_value`/`current_option`/`is_on` 读的是哪个键？回填是否打在同一键上？
- [ ] 是否同时更新了 `self._attr_*`？只改一处会被另一处覆盖。
- [ ] 是否调用了 `async_request_refresh()`？若没有，理由是什么（无读通路？）
- [ ] 失败路径是否完全没有副作用？
- [ ] 回填值是设备接受值还是用户请求值？
