---
project: "ha-epson-ef-projector"
type: adr
id: ADR-global-001
description: "放弃 ADB 与 PJLink/ESC-VP21 路线，改用投影仪固件自带的 pjctl HTTP API（8080，Digest 认证）作为唯一控制通道；配置条目因此从 v1(ADB) 迁移到 v2(native)，且不再要求用户开启任何调试选项。"
base_commit: 52928ed9d1ac925ad7d87f47a0dd1af9a24fb3a3
status: accepted
title: "选择固件原生 pjctl HTTP API 作为唯一控制通道"
date: 2026-11-07
code_refs:
  - custom_components/epson_ef/api.py
  - custom_components/epson_ef/const.py
  - custom_components/epson_ef/__init__.py
  - custom_components/epson_ef/config_flow.py
---

# ADR-001: 以固件原生 pjctl HTTP API 取代 ADB 控制

## 状态

accepted（v2 起生效；v1 的 ADB 路径已无任何调用点，仅保留迁移分支）

## 1. 背景

**业务背景**：Epson EF 系列（HB7x 平台，Android 13 国内特供）不提供 PJLink(4352)、ESC/VP21(3629) 或可用的 Web 管理界面（逐端口探测全部 `Connection refused`；）。用户需要的是"开箱即用、固件升级后仍可用"的控制能力。

**技术背景**：设备上运行 `WebApiSrv`（NanoHTTPD），在 TCP 8080 暴露 `POST /api/pjctl` 的原生 JSON API，后端经 `EpsonManager → IProjectorControl.config()` 调系统服务 `EpsonService`（`const.py` 的协议注释）。认证为 HTTP Digest（MD5, qop=auth），凭据内置固件。

**核心问题**：在"官方无文档、无标准协议、不想让用户开 USB/网络调试"的约束下，用什么通道控制投影仪？

**约束条件**：

- 用户侧零调试要求（不能假设 ADB 可用）。
- 固件 OTA 后必须仍可用（WebApiSrv 随系统自启）。
- 只用 HA 运行时已带的库（`manifest.json:12` `requirements: []`）。

## 2. 决策驱动因素

1. **开箱即用**（高）：ADB 需用户手动开启网络调试并授权 RSA，属不可接受的门槛。
2. **固件升级存活**（高）：ADB 授权在部分固件更新/恢复出厂后失效；WebApiSrv 是系统服务，随开机自启。
3. **能力覆盖**（高）：pjctl 暴露 40 个 GET + 31 个 SET + 27 个 NOTIFY（`const.py` 的协议注释 的枚举统计），覆盖开关机、按键、画面、对焦、几何、氛围灯。
4. **零第三方依赖**（中）：`aiohttp` 是 HA core 自带（`api.py:14`），满足空 requirements。

## 3. 候选方案

| 方案 | 简述 | 优点 | 缺点 | 风险 |
|-|-|-|-|-|
| Option A：pjctl HTTP API | 固件自带 JSON API，Digest 认证 | 零调试门槛、固件自启、能力最全、纯 HTTP 易实现 | 无官方文档，命令语义全靠现场分析与真机验证；明文 HTTP | 语义误判导致误操作（如 `SET_REFRESH "1"` 会关机） |
| Option B：ADB shell（`am`/`input`/`settings`） | 通过 5555 端口执行 Android 命令 | 能力近似无限（能改 Settings.Global 的画质滑条） | 需开启网络调试 + RSA 授权；固件更新易失效；安全面大 | 用户不接受；ADB 常开是高危通道 |
| Option C：PJLink / ESC/VP21 | 行业标准投影控制协议 | 有标准、可复用现成 HA 集成 | 本机型端口全部 `Connection refused`，协议被裁掉 | 不可行 |
| Option D：官方云 API / 小程序协议 | 走腾讯云或小程序接收器 | 官方支持 | 需外网、需账号、延迟高、隐私风险 | 与"本地控制"目标冲突 |

### Option A：pjctl HTTP API（选中）

以 `EpsonApi` 封装 `POST /api/pjctl`，body 形如 `{"id": "...", "size": N, "params": "..."}`，Digest 认证缓存 nonce（`api.py:99-145`）。高层方法按命令族分节（`api.py:218-656`），危险命令不暴露。

### Option B：ADB shell

曾作为 v1 实现路径（`async_migrate_entry` 仍在处理 v1 条目：`__init__.py:31-39` 会删掉 `enable_camera` 键并强写 `port=8080`）。已废弃。

## 4. 决策结果

**选择方案**：**Option A**

**选择理由**：只有 A 同时满足"零调试门槛 + 固件升级存活 + 本地低延迟"；C 在本机型不存在，B 违反用户约束，D 违反本地控制目标。代价是必须承担现场分析成本——这被"注释即契约 + 真机回环验证"的工程纪律（见 `guidelines/principle-global-002-measured-firmware-facts.md`）部分抵消。

### 4.1 实施要点

- `API_PATH = "/api/pjctl"`，`DEFAULT_PORT = 8080`（`const.py:14`, `:10`）。
- 配置条目 `VERSION = 2`（`config_flow.py:26`），`async_migrate_entry` 把 v1(ADB) 条目原地升级（`__init__.py:31-39`，幂等）。
- 认证为固件内置常量（`const.py:15-19`），不可轮换——这决定了部署侧必须靠网络隔离（见 `risk.md` R-24）。
- 传输层不 import HA，使现场分析脚本与单测可复用同一实现（`api.py:1-3`）。

## 备注

- 端口探测过程与命令覆盖状态见 固件行为记录。
- 未采纳的 upload/download 路由（`/api/pjctl_upload`、`/api/pjctl_download`）当前不暴露。
- ADB 不进入集成代码；设备若已开启网络调试，可用于手工排障。
