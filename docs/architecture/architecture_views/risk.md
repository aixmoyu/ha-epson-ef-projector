---
project: "ha-epson-ef-projector"
type: risk
description: "全局风险登记：四类来源（稳定性/并发、性能、安全、运维与工程）合并自四张视图的探索结论。本项目最高危的两个事实是『写操作几乎无并发保护』与『单轮轮询可远超轮询间隔』——两者叠加在单线程 NanoHTTPD 固件上；其次是 raw_request 服务无白名单无 admin 校验的任意命令面，以及 CI 不安装 homeassistant 导致的装配层零覆盖。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
---

# 架构风险分析

> 来源：`architecture_views/{logical,development,process,deployment}_view.md` 的探索返回值合并去重。
> 每条风险均附 `path/to/file:line`；无法从仓库内证实的条目已标注 `[待确认]`。
> 概率/严重度取值：高/中/低；优先级 = 严重度×概率的业务排序（P0 阻断发布、P1 应尽快、P2 排期、P3 观察）。

## 稳定性与并发风险

| 风险 ID| 场景 | 触发条件 | 影响范围 | 概率 | 严重度 | 优先级 | 当前缓解 | 建议缓解 | 状态 |
|-|-|-|-|-|-|-|-|-|-|
| R-01 | 写操作无并发保护（除电源） | 用户/自动化并发调用 number/switch/select/button/services 的写路径 | 全部可写实体 | 中 | 高 | **P0** | 仅电源路径有 `asyncio.Lock`（`media_player.py:73`, `:180`, `:192`）；其余写路径直接 `await api.request`（`number.py:126`, `switch.py:106`, `select.py:107`, `button.py:138`, `services.py:64`） | 在 `EpsonApi` 上加写串行化 `asyncio.Lock`（或按命令族分锁），最少覆盖"连续运动类"（对焦/几何） | 开放 |
| R-02 | 对焦闭环可被并发重入打断 | 两次并发设置 focus 目标 | 对焦马达、几何位置 | 低 | 高 | **P0** | 无锁；`goto_focus` 循环内无 `async with`（`api.py:561-583`），实体 setter 亦无锁（`number.py:282-301`） | 对 `Ef50FocusNumber` 加实例锁；`goto_focus` 入口加可重入拒绝（busy 即抛错） | 开放 |
| R-03 | 马达在取消路径上可能不停 | HA 关停/任务被二次取消 | 对焦马达（可能顶到限位） | 低 | 高 | **P1** | `try/finally` 保证发 `OFF`（`api.py:573-582`，意图注释 `:575-578`），但 `await focus_move_stop()` 未用 `asyncio.shield` | 用 `asyncio.shield(focus_move_stop())` 包裹 finally 内调用 | 开放 |
| R-04 | 色彩增益"读-改-写"丢更新 | 两个通道并发写 | RGB 增益三实体 | 中 | 中 | **P1** | 三通道共享一条 GET（`number.py:351-361`），写为"取三元组→替换单通道→写整串"（`number.py:381-389`） | 三通道共享锁；或改为"写后统一回读再写" | 开放 |
| R-05 | 共享字典就地 mutate | 实体写与协调器 merge 交错 | 全部实体状态一致性 | 中 | 中 | **P1** | 实体直接改 `coordinator.data`（`media_player.py:162`, `number.py:129`, `switch.py:109`, `select.py:110`），协调器同时读取做 merge（`coordinator.py:117`, `:125`）；无版本号、无快照替换 | 改为"整体替换不可变快照"或加锁；最少在写入处 `dict(...)` 复制 | 开放 |
| R-06 | Digest nonce 并发竞争 | 并发协程中任一超时/网络错清空共享 `_auth` | 全部请求（可退化为 401 链） | 中 | 中 | **P2** | `_nc` 同步递增（`api.py:125`，注释声明并发安全），但 `_update_auth` 归零（`api.py:111`）、`_clear_auth` 被任一协程触发（`api.py:208`, `:213`） | 把 auth 状态改为每请求携带的不可变快照，或整体加锁 | 开放 |
| R-07 | session 生命周期缺口 | 卸载后迟到协程重建 session | 资源泄漏 | 低 | 低 | **P3** | `_ensure_session` 不检查已关闭/已卸载（`api.py:93-97`）；`close()` 后 `_owns_session` 不复位（`api.py:87-90`） | 增加 `_closed` 标志，关闭后拒绝重建 | 开放 |
| R-08 | 电源锁只覆盖单实体 | 按钮/服务发 `POWER(26)` 与 turn_on/off 并发 | 电源状态与设备真值分叉 | 中 | 中 | **P1** | 锁是每实体实例（`media_player.py:73`）；`services.send_key`（`services.py:54`）与按钮（`button.py:17-83` 含 POWER 键）不经过它 | 把电源锁上提到 entry 级共享对象（`hass.data[DOMAIN][entry_id]`） | 开放 |

## 性能瓶颈风险

| 瓶颈 ID| 位置 | 当前指标 | 阈值 | 触发条件 | 影响 | 突破方案 | 预计工作量 |
|-|-|-|-|-|-|-|-|
| R-09 | 慢诊断段串行 33–35 次 HTTP（`coordinator.py:59-132`） | 单条默认 10s 超时（`api.py:160`）；diag 17 条 + switch 6 + select 4 + light_output/refresh_timer/focus_range/focus_pos/product/standby_mode | `update_interval` 默认 15s（`const.py:11`） | 投影仪唤醒时每 12 轮一次；固件变慢或半挂起 | 最坏 ≈330s 远超 15s → 轮询积压、`async_request_refresh` 排队、状态长时间陈旧 | 给整轮 `_async_update_data` 加 `asyncio.timeout` 总预算；失败即跳过本轮并退避 | 小 |
| R-10 | 命令间节流不均 | diag 与 switch/select 回读后 `sleep(0.2)`（`coordinator.py:88`, `:96`, `:101`），后 5 条（light_output/refresh_timer/focus_range/focus_pos/standby_mode）无间隔（`coordinator.py:102-115`, `:130`） | 单线程固件（NanoHTTPD） | 每诊断轮 | 突发 5 连发对固件压力集中 | 统一节流策略（统一 0.2s 或按命令族分类） | 小 |
| R-10b | 慢轮存在 7 条重复请求（占慢轮 35 条的 20%） | `diag` 探针表与 `settings` 回读表对同一条命令各发一次：`CONFIG_GET_VMUTE`/`COLOR_TEMP`/`DYNAMIC_CONTRAST`/`MOVEMENT_SENSOR`/`SMART_EYE_PROTECTION`/`STARTUP_CORRECTION` 共 6 条重复（`coordinator.py:63-83` vs `:92-101`，均在 `const.py:53-66`/`:75-92` 表中），另有 `CONFIG_GET_PRODUCT_NAME` 在 probe 与 `coordinator.py:121` 各读一次 | 单线程固件 | 每诊断轮 | 无谓请求使慢轮从 28 条膨胀到 35 条，直接放大 R-09 的积压风险 | 把 `diag` 与 `settings` 合并成一次读取，同一命令只请求一次 | 小 |
| R-11 | 没有自适应退避 | 设备持续超时 | — | 断电/DHCP 失效/固件挂起 | 每 `poll_interval` 都重打 2 次 liveness，无退避 | 连续 N 次失败后指数退避至上限 | 小 |

## 数据一致性风险

| 风险 ID| 场景 | 一致性级别 | 当前保障 | 潜在问题 | 概率 | 影响 | 改进方案 |
|-|-|-|-|-|-|-|-|
| R-12 | 可读设置写入 → 固件真值 | 最终一致 | 乐观写内存 + 慢诊断回读覆盖（`coordinator.py:116-119`） | 收敛需 ≤12 个轮询周期；`Ef50ConfigSwitch` 写入后**不发 refresh**（`switch.py:104-111`） | 高 | 中等 | 所有可写实体统一 `async_request_refresh()` |
| R-13 | 待机期间设置不刷新 | 最终一致 | 整段 `settings` 落在 `if state == "on":` 内（`coordinator.py:61`, `:116`），关机时靠 carry-forward | 关机期间他人用遥控器改设置 → 集成显示旧值，开机会短暂错 | 高 | 低 | 待机也读一次 settings（或开机时强制刷新） |
| R-14 | 无读通路能力的乐观状态 | 无收敛 | `assumed_state=True`（`switch.py:166-169`、`number.py:444-447`、`select.py:203-206`）+ `RestoreEntity`（`switch.py:158-164` 等） | 物理遥控器/机身菜单修改后永久漂移；静音**未标** `assumed_state`（`media_player.py:50`） | 高 | 低 | 为静音补 `assumed_state`；文档明示"需手动同步" |
| R-15 | 灯时/距离的陈旧读数 | 最终一致 | 关机时 carry-forward 且暴露 `stale` 属性（`sensor.py:70-83`, `:112-121`；`media_player.py:114-120`） | 陈旧值可能被自动化当作实时值消费 | 中 | 中 | 自动化侧用 `stale` 属性做 guard；或提供 `last_measured_at` 阈值告警 |
| R-16 | 配置私钥/身份与 entry unique_id 不同源 | — | 身份走 serial+Store（`__init__.py:42-76`），entry unique_id 走 `host:port`（`config_flow.py:47-49`） | 改 IP 后"重新添加"会造出第二个 entry，同序列号实体 unique_id 冲突 | 中 | 高 | 创建时也尝试用序列号做 unique_id；或同序列号去重提示 |

**失败场景分析**

```
正常路径：用户写 → 固件 resultCode=0 → 改 coordinator.data → 下一诊断轮读真值覆盖
异常路径 1（写失败）：抛 HomeAssistantError，本地值不翻转（switch.py:174-177, number.py:291-292）→ UI 保持旧值，与设备一致
异常路径 2（写成功但设备未生效）：伪装一致 —— GET 若读回旧值，会在诊断轮被覆盖回来；无读通路的项则永久显示"已成功"
异常路径 3（并发写）：后写覆盖先写（无锁）→ 用户看到的值可能不是最后一次操作的结果
异常路径 4（确认超时）：wait_for_state 超期 → async_request_refresh 回落协调器真值（media_player.py:170-176）
```

## 可用性风险

| 风险 ID| 故障域 | 组件 | 当前可用性 | 目标可用性 | SPOF | 冗余策略 | 故障恢复策略 | RTO / RPO |
|-|-|-|-|-|-|-|-|-|
| R-17 | 固件 `WebApiSrv` 单线程挂起 | NanoHTTPD :8080 | 挂起即 8080 全不可用（客户端超时只是放弃） | — | 是 | 无 | **代码内无自愈**：只能物理重启设备（复位或断电重启） | RTO=人工分钟级 / RPO=期间所有命令丢失 |
| R-18 | 投影仪市电断电 / 深待机断网 | 设备整体 | 该 entry 全部实体 `unavailable` | — | 是 | 无 | 来电后下一轮自动恢复（`coordinator.py:42-45`）；**无远程唤醒**（WOL 实测无效） | RTO=人工开机 / RPO 不适用 |
| R-19 | 局域网断连 / DHCP 续租失败 | 网络 | 同上 | — | 是 | 无 | 只能持续轮询等恢复 | RTO=人工 / 不适用 |
| R-20 | HA 重启 / 集成 reload | 集成整体 | 全部实体短暂不可用 | — | 是 | 无 | 重新 setup；身份从 `.storage` 恢复；乐观态由 `RestoreEntity` 恢复（`__init__.py:53-76`） | RTO≈HA 启动 + 首次 refresh / RPO=乐观态可能回退到上次写入值 |
| R-21 | 固件升级 / 换机型 | 协议兼容 | 部分命令 FAIL | — | 是 | 无 | 无版本探测、无降级；`product name` 仅用于展示（`media_player.py:87`） | RTO=改代码 / 不适用 |
| R-22 | 集成自身装配缺陷 | 全部实体 | 3.10.0 曾因 `WRITABLE_SELECTS` 5→6 元组扩宽导致全实体 unavailable | — | 是 | 测试护栏（`tests/test_const_contract.py`） | 修正后发布；仍无 HA 运行时自动化覆盖 | RTO=发版 / 不适用 |

## 安全风险

| 风险 ID| 威胁 | 攻击面 | OWASP 类别 | 当前防护 | 漏洞等级 | 利用难度 | 改进措施 |
|-|-|-|-|-|-|-|-|
| R-23 | 任意 pjctl 命令注入（含高危写） | `epson_ef.raw_request` 服务：**无命令白名单、无参数校验、无 `require_admin`** | A01 权限控制失效 | 无（`services.py:56-94` 全文无 `require_admin`） | 中 | 低（任何可调服务的 HA 用户/自动化即可） | 加 `@require_admin`；对 `CONFIG_SET_REFRESH "1"`（立即刷新并关机，`api.py:481-488` 自己警示）等做参数级拒绝 |
| R-24 | 明文固件凭据入库 + 全链路无 TLS | `const.py:15-19` 的 `DIGEST_USER`/`DIGEST_PASSWORD`；`api.py:79` 硬编码 `http://` | A02 加密失效 | 无；注释声明这是固件出厂内置、全 HB7x 家族共用（`const.py:17-18`），该常量在 `const.py` 中另有注释说明其来源 | 中 | 低（需同网段） | 属"固件固定、不可轮换"的既成事实，只能网络隔离（VLAN/ACL）+ 文档明示；不要把 8080 映射到公网 |
| R-25 | 同网段任意主机可控制投影仪 | TCP 8080 明文 + 凭据公开 | A01/A07 | 无 IP 白名单 | 中 | 低 | 部署侧 VLAN 隔离；仓库内仅有文字建议（见 `README.md` 安全节）但无代码可校验 |
| R-26 | ADB 5555 长期开启 | 设备 ADB 调试口 | A05 安全配置错误 | 无 | 中 | 低 | 文档明示仅排障期使用，用完关闭（`tools/README.md:50-51`） |
| R-27 | 事件总线广播固件原始返回 | `epson_ef_response` 事件（`services.py:67-70`） | A09 日志与监控不足 | 无权限过滤 | 低 | 低 | 无（事件本身不含凭据）；如担心可在 payload 里脱敏 |

## 容量与扩展性风险

| 资源/瓶颈点 | 当前容量 | 当前利用率 | 短期预测 | 长期预测 | 扩容阈值 | 突破/扩容方案 |
|-|-|-|-|-|-|-|
| 单 entry 实体数 | 58 个（固定枚举，不可动态增减） | — | 随固件能力适配持续增长（v3.6→v3.10 从少量增至 58） | 固件仍存在未适配的读取与写入命令，能力面可能继续增长 | 实体数增长会线性放大诊断轮 HTTP 次数（当前 33–35） | 诊断轮改为按需/分批；或把只读诊断降为显式服务调用 |
| 慢诊断轮 HTTP 次数 | 33–35 次/12 轮 | 每条 0.2s 间隔 ≈ 7s 基线 | 同上 | 同上 | 超过 `poll_interval` × 12 | 见 R-09 |
| 多设备（多 entry） | 未验证 | — | 服务未指定 entity_id 时取"第一个 entry"（`services.py:31-35`） | 语义在多设备下含糊 | 2 台 | 服务强制要求 entity_id，或按 device_id 路由 |

## 技术债务

| 区域 | 债务类型 | 原因 | 严重度 | 偿还建议 |
|-|-|-|-|-|
| 传输层职责 | 设计债 | `api.py` 有 12 个公开方法在包内零调用者（`get_distance_mm:353`、`get_light_output:366`、`get_picture_preference:380`、`get_color_gain:432`、`focus_step:516`、`geometry_zoom:590`、`geometry_shift:600`、`geometry_reset:610`、`get_auto_focus_state:634`、`get_auto_focus_target:643`、`get_light_time:330`、`set_picture_preference:384` 等），协调器改用 `get_raw` + 手工解析（`coordinator.py:63-83`） | 中等 | 二选一：让协调器改用语义方法，或删掉无用方法；避免双份解析 |
| 实体平台越界 | 设计债 | 平台直接 `api.request(cmd, params=...)` 拼命令（`switch.py:106`、`select.py:107`、`button.py:138`），绕过 `api.py` 的 `set_*` 语义封装；实测 40 个可写实体中仅 15 个走语义方法，30 个按钮 + 6 个配置开关 + 4 个选择器全部裸拼命令串（`button.py:138`、`switch.py:106`、`select.py:107`） | 中等 | 平台只调语义方法；命令字符串集中到 `const.py`/`api.py` |
| `hass.data` 隐式契约 | 设计债 | 字典无类型，9 处解包（写入 `__init__.py:195-201`；读取 `binary_sensor.py:23`、`services.py:27`、`diagnostics.py:16` 等） | 中等 | 引入 `TypedDict`/dataclass 描述 entry data |
| 命名 | 代码债 | 20 个类名 `Ef50*` 与 domain `epson_ef`、覆盖 HB71/HB7x 全系脱节（`coordinator.py:19`、`media_player.py:41` 等） | 轻微 | 新代码用 `Epson*`，老类逐步重命名（需同步测试字符串） |
| 死代码 | 代码债 | `_TIMEOUT` 定义后从未引用（`api.py:37`，真实超时在 `:160`/`:188` 内联）；`coordinator.py:120` 的 `if state == "on":` 嵌在 `:61` 同条件内属冗余 | 轻微 | 删除或统一超时配置项 |
| 异常捕获过宽 | 代码债 | `switch.py:107`、`select.py:108`、`services.py:65` 用 `except Exception` 吞掉编程错误 | 中等 | 收窄到 `EpsonApiError` 家族，保留 `Exception` 仅用于最终兜底日志 |
| 不可达分支 | 代码债 | `__init__.py:172-176` 依赖 `get_product_name` 抛错，但其内部 `except EpsonApiError: return None`（`api.py:295-300`）→ 该分支死代码 | 中等 | 让 `get_product_name` 抛错，或删除该 try/except 并注明真实降级点在协调器首刷 |
| 测试结构 | 测试债 | `unittest.main()` 置于文件中部的类之间（`tests/test_api.py:507-508`、`tests/test_regressions.py:183-184`）→ 直跑只执行 42/64 与 7/9 | 严重 | 把 `unittest.main()` 移到文件末尾（或删除，统一用 discover/pytest） |
| HA 运行时零覆盖 | 测试债 | CI 不安装 `homeassistant`（`.github/workflows/tests.yml:18`），7 个平台 + config_flow + services + diagnostics 的装配零自动化验证；历史已因此出过全实体不可用 | 严重 | 引入 `pytest-homeassistant-custom-component` 与最小 setup/实体装配冒烟测试 |
| `tools/` 无门禁 | 测试债 | CI 的 ruff 范围不含 `tools/`（`.github/workflows/tests.yml:21`），本地 `ruff check .` 已全绿，但 CI 范围始终不含 `tools/` | 轻微 | 把 `tools/` 纳入 CI lint 范围 |
| 发布工程 | 工程债 | 0 tag、0 remote、无 release workflow，版本号唯一载体是 `manifest.json:13`；漏改即 HACS 显示旧版 | 中等 | 加 tag/release workflow 与版本一致性校验 |
| 依赖声明 | 工程债 | `pytest` 未声明（无 requirements/CI 未装），但无 requirements 声明 pytest；`ruff` 无配置无版本锁 | 轻微 | 加 `requirements-dev.txt` 或 pyproject 的 optional-dependencies |
| 文档漂移 | 文档债 | 协议结论分散在 `const.py` 注释与 `api.py` docstring 中，部分早期结论已被后续实测修正，历史版本仍可能被误引 | 中等 | 保持注释与代码同提交；已推翻的结论就地标注 |

## 风险汇总（按优先级）

| 优先级 | 风险 ID | 一句话 |
|-|-|-|
| **P0** | R-01, R-02 | 写路径几乎无并发保护；对焦闭环可被重入打断 |
| **P1** | R-03, R-04, R-05, R-08, R-09, R-16, 测试结构债, HA 零覆盖 | 马达可能不停、增益丢更新、共享字典无快照、电源锁范围不足、轮询可积压、身份双源、测试直跑漏 11 例、装配层零覆盖 |
| **P2** | R-06, R-10, R-10b, R-11, R-12, R-13, R-15, R-23, R-24, R-25, R-26 | nonce 竞争、节流不均、7 条重复请求、无退避、收敛慢、待机不刷新、陈旧读数、命令注入面、明文凭据 |
| **P3** | R-07, R-14, R-27, R-28, 命名/死代码/依赖声明/文档漂移 | 资源泄漏、乐观漂移、事件广播、内网地址、命名与债务 |

## 待确认（无法从仓库内证实）

- **[待确认: 固件 NanoHTTPD 是否真的单线程串行处理？]** 依据仅是 固件行为记录 的行为记录（原 该结论来自早期固件分析），无固件源码可验。生产代码未假设该点（既无节流常量也无背压设计），故 R-09/R-17 的严重度依赖此假设成立。
- **[待确认: 两次 `_async_update_data` 是否可能重叠？]** 属 HA `DataUpdateCoordinator` 框架调度行为；本分析环境未安装 `homeassistant`（`import homeassistant` → `ModuleNotFoundError`），无法代码验证。本报告只断言"单次刷新内部串行"这一可验证事实（`coordinator.py:84-132` 无 `gather`/`create_task`）。
- **[待确认: 最低可运行 HA 版本？]** `hacs.json:3` 声明 2024.4.0，而 CI 与本地实测均只覆盖 Python 3.13，中间版本无验证证据。
- **[待确认: probe_state 的 liveness 返回 resultCode!=0 时的语义？]** 会抛 `EpsonApiCommandFailed`，在 setup 阶段被 `__init__.py:172-176` 当作不可达（该分支本身不可达，见技术债），实际路径未测试覆盖。
