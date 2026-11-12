---
project: "ha-epson-ef-projector"
type: adr
id: ADR-verification-harness-001
description: "在无 Home Assistant 的 CI 环境中验证生产代码，最终选择『伪包注入（只设 __path__ 的 ModuleType）+ AST 静态解析』，明确拒绝 mock HA 与安装 HA 两条路线。代价是 __init__.py 与 7 个平台模块零覆盖，收益是 64 用例 0.2s 跑完且不引入任何测试框架依赖。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
status: accepted
title: "用伪包隔离代替 mock/安装 Home Assistant"
date: 2026-11-01
code_refs:
  - "tests/test_api.py:17-33"
  - "tests/test_const_contract.py:19-24"
  - "tests/test_regressions.py:22-35"
  - "custom_components/epson_ef/api.py:1-4"
  - ".github/workflows/tests.yml:18"
---

# ADR-001: 用伪包隔离代替 mock/安装 Home Assistant

## 状态

accepted（自 3.10.0 之前确立，`tests/test_api.py` 的 docstring 已写明 "no Home Assistant required"，`tests/test_api.py:1-4`）

## 1. 背景

**业务背景**：这是一个单文件包分发的 HACS 自定义集成（`custom_components/epson_ef/`，14 模块 3055 行，58 实体）。维护者需要在**每次改协议细节后**都能快速确认没有破坏已固化的真机结论；同时仓库要能被外部贡献者在没有投影仪、没有 HA 的机器上 clone 后立即跑测试。

**技术背景**：

- 14 个生产模块中 12 个 import `homeassistant`（`__init__.py` 6 处、`number.py` 9 处、`switch.py` 8 处、`select.py` 8 处、`media_player.py` 8 处、`button.py` 7 处、`sensor.py` 7 处、`binary_sensor.py` 6 处、`config_flow.py` 4 处、`coordinator.py` 3 处、`services.py` 3 处、`diagnostics.py` 2 处）。
- 仅 `api.py`（656 行）与 `const.py`（222 行）为零 HA 依赖；`api.py:1-4` 明确声明 "Pure aiohttp + stdlib; no Home Assistant imports so it can be tested standalone"。
- CI 的依赖安装是 `pip install aiohttp ruff pyflakes`（`.github/workflows/tests.yml:18`）——**刻意不含 homeassistant**（该包体量大、版本与 HA 核心强耦合、且需要完整 HA 运行环境才有意义）。
- 实测环境：`python3 -c "import homeassistant"` → `ModuleNotFoundError`，rc=1。

**核心问题**：在没有（也不打算安装）Home Assistant 的进程里，如何让 `from epson_ef.api import EpsonApi` 成功，同时保证被测的是**真实生产字节码**而不是替身？

**约束条件**：

- 不得引入 `pytest-homeassistant-custom-component` 之类重型依赖（会拖入完整 HA，CI 时间与体积不可接受）。
- 测试必须能在 `python -m unittest discover -s tests` 下运行（CI 唯一测试命令）。
- 被测对象的行必须是真实源码行（否则断言失去意义）。
- 必须能让外部贡献者零配置跑通。

## 2. 决策驱动因素（Decision Drivers）

1. **真实性**（重要性：高）：断言必须作用于生产源码，而不是 mock 出的一致行为。
2. **零依赖**（重要性：高）：除 `aiohttp` 外不新增测试依赖；CI 只装 3 个包。
3. **速度**（重要性：中）：全量套件需在秒级完成，让"改一行就跑一遍"成为习惯。
4. **可移植性**（重要性：中）：macOS 本地与 ubuntu-latest CI 行为一致。
5. **覆盖完整性**（重要性：低，但必须显式承认损失）：装配层可接受零覆盖，只要这个缺口被写下来。

## 3. 候选方案（Considered Options）

| 方案 | 简述 | 优点 | 缺点 | 风险 |
|-|-|-|-|-|
| **Option A** 伪包注入 + AST | 手工 `ModuleType("epson_ef")` 只设 `__path__`；结构性耦合改用 `ast.parse` | 零新增依赖；被测真实源码；0.2s；外部贡献者零配置 | `__init__.py` 与 7 平台零覆盖；需要 3 处复制同样的 4 行样板 | 覆盖缺口被误读为"全绿" |
| **Option B** mock `homeassistant.*` | 在 `sys.modules` 里伪造 HA 各子模块 | 理论上可执行 `__init__.py` | 需伪造 `config_entries`/`core`/`helpers.*` 等多个模块；HA 升级即失效；断言的是 mock 行为 | 高：伪 HA 与真 HA 行为差异无法自证 |
| **Option C** 安装 homeassistant | CI 与本地 `pip install homeassistant` | 覆盖真实装配层 | CI 时间/体积激增；需完整 HA 运行环境（`hass` fixture）才有意义；不是装个包就能测 | 中：装了也只能 import，不能 setup |
| **Option D** `pytest-homeassistant-custom-component` | 官方推荐的集成测试框架 | 提供 `hass` fixture、config entry 装配、状态机 | 拖入完整 HA 依赖树；需改测试框架为 pytest（当前 CI 用 unittest）；锁定 HA 版本 | 中：框架与 HA 版本强耦合，升级成本外溢 |

### Option A：伪包注入 + AST 静态解析

```python
if "epson_ef" not in sys.modules:
    _pkg = types.ModuleType("epson_ef")
    _pkg.__path__ = [str(_PKG_DIR)]
    sys.modules["epson_ef"] = _pkg
from epson_ef.api import EpsonApi
```

包对象已存在（但从未执行 `__init__.py`），因此 `epson_ef.api` 的子模块查找走 `__path__`，命中真实 `api.py`。错误只在 `__init__.py` 里被屏蔽——实测 `epson_ef.__file__` 为 `None`，即"从未执行"的直接证据。

结构性耦合（表宽度、实参顺序、清理表内容）改用 `ast.parse(path.read_text())`，连 import 都不需要。

### Option B：mock `homeassistant.*`

需覆盖 `homeassistant.config_entries`、`homeassistant.core`、`homeassistant.helpers.entity_platform`、`homeassistant.helpers.update_coordinator`、`homeassistant.const` 等；每个平台文件的装饰器（`@property`、`async_setup_entry`）都依赖 HA 基类行为。mock 出来的 `CoordinatorEntity` 不具备真实的数据结转语义，测试将验证"mock 是否被正确调用"而非"集成是否正确"。

### Option C：安装 homeassistant

即便装上，`async_setup_entry(hass, entry)` 仍需要一个构造好的 `hass` 实例、一个 `ConfigEntry`、一个事件循环与 storage 后端。没有 `pytest-homeassistant-custom-component` 的 fixture，装包本身几乎不带来覆盖。

## 4. 决策结果（Decision Outcome）

**选择方案**：**Option A**

**选择理由**：

- 对照驱动因素 1（真实性）：A 执行的是真实 `api.py`，B/C/D 都会引入替身层或框架层。
- 对照驱动因素 2（零依赖）：A 只需 `aiohttp`（本来就必需）+ 标准库 `types`/`ast`；B 需手工维护伪造层；C/D 拖入完整 HA。
- 对照驱动因素 3（速度）：A 实测 **64 用例 0.177–0.20s**；C/D 的 HA fixture 通常需数秒起步。
- 对照驱动因素 5（覆盖）：A 的损失是明确且可陈述的（`__init__.py` 221 行 + 7 平台 + config_flow/services/diagnostics），而 B 的损失是"看起来覆盖了但覆盖的是 mock"——后者更危险。

**已知损失（必须随决策一起记录）**：

1. `__init__.py` 的 `PLATFORMS` 转发（`:203`）、身份解析（`:42`/`:79`）、legacy 清理（`:123`）、`hass.data` 组装（`:195`）零覆盖。
2. 7 个平台文件的 `async_setup_entry`、实体属性、状态写回零覆盖。
3. 实测佐证：discover 结束后 `sys.modules` 中 `epson_ef.*` 只有 `['epson_ef', 'epson_ef.api', 'epson_ef.const']`。

### 4.1 实施要点

- 伪包注入必须幂等（`if "epson_ef" not in sys.modules`），否则 discover 多文件会重复注册。
- 伪包**只设 `__path__`**，不设 `__file__`/`__spec__`——保持"未执行"的状态可观测。
- `tools/` 的脚本用更强的双保险：伪包 + `spec_from_file_location("epson_ef.api", ...)` + `sys.modules["epson_ef.api"] = mod` + `exec_module`（20/20 脚本，如 `tools/diagnose_state.py:11-18`）。
- 装配层的结构性约束用 AST 补：清理表内容（`tests/test_regressions.py:99-119`）、实参顺序（`:150-170`）、coordinator 遍历表名（`:177-180`）。
- 任何"已验证"的表述必须附覆盖边界说明（参见 `principle-verification-harness-004`）。

## 备注

- 若未来要补装配层覆盖，推荐路径是 Option D（`pytest-homeassistant-custom-component`）而非 Option B/C，并需先把 CI 的测试命令从 `unittest discover` 迁移到 `pytest`——这是一次框架级变更，应单独立 ADR。
- Option A 的 4 行样板当前在 3 个测试文件里重复（`tests/test_api.py:23-26`、`test_const_contract.py:21-24`、`test_regressions.py:24-27`），是否抽成共享模块见 ADR-004。
- 相关全局决策：`architecture_views/guidelines/principle-global-001-transport-ha-decoupling.md`（传输层与 HA 解耦）——本 ADR 是该全局原则在验证设施侧的落地。
