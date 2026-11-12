---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-verification-harness-001
description: "在没有 Home Assistant 的环境里验证生产代码时，用伪包注入（手工构造 ModuleType 并只设 __path__）绕开包的 __init__.py，而不是伪造/模拟 Home Assistant 本身。代价是 __init__.py 的装配逻辑零覆盖，因此必须显式承认这条边界，禁止把『测试全绿』当作『集成可用』。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
principle_category: 工程
principle_scope: module
---

# 隔离依赖，而不是伪造依赖

## 原则详细描述

本仓库面对一个硬约束：CI 与本地都没有 `homeassistant`（实测 `python3 -c "import homeassistant"` → `ModuleNotFoundError`，rc=1），而 14 个生产模块里有 12 个 import 它（`__init__.py` 6 处、`number.py` 9 处、`media_player.py` 8 处……）。要让测试跑起来，有两种取向：

- **伪造**：mock `homeassistant.*` 的各个子模块，让 `__init__.py` 能执行。
- **隔离**：让被测模块根本不需要 `__init__.py` 执行。

本项目选后者，具体是往 `sys.modules` 里塞一个手工构造的包对象：

```python
if "epson_ef" not in sys.modules:
    _pkg = types.ModuleType("epson_ef")
    _pkg.__path__ = [str(_PKG_DIR)]
    sys.modules["epson_ef"] = _pkg

from epson_ef.api import EpsonApi  # noqa: E402
```

三处逐字相同：`tests/test_api.py:23-26`、`tests/test_const_contract.py:21-24`、`tests/test_regressions.py:24-27`。同一手法在 20 个真机脚本里演化为"伪包 + `importlib.util.spec_from_file_location("epson_ef.api", ...)` + 预登记 + `exec_module`"的双保险（`tools/diagnose_state.py:11-18`）。

这条原则之所以成立，是因为**被测对象本身被设计成可隔离的**：`api.py:1-4` 明确写 "Pure aiohttp + stdlib; no Home Assistant imports so it can be tested standalone"，`const.py` 零内部 import。可测性是生产代码的设计属性，不是测试的技巧。

## 为什么要这样

1. **mock 的维护成本随 HA 版本爆炸**。`__init__.py` 的第 6-11 行 import 了 `config_entries`/`core`/`helpers.*` 等多个子模块；`number.py:8-16` 有 9 处。伪造它们意味着每次 HA 升级都要跟着改，而且伪造出来的行为与真实 HA 的差异无法自证。
2. **伪包只屏蔽一个文件，不屏蔽整个运行时**。`__path__` 注入让 `epson_ef.gps` 之类的子模块查找仍然走真实源码，因此被测的是**真实字节码**，不是替身。
3. **实测验证过必要性**：去掉伪包后 `import sys; sys.path.insert(0,'custom_components'); import epson_ef.button` → `ModuleNotFoundError: No module named 'homeassistant'`（栈顶 `__init__.py:6`），rc=1；加上伪包后同一导入返回 `<class 'epson_ef.api.EpsonApi'>`。
4. **边界清晰可陈述**：该手法的副作用是确定的、可量化的——`__init__.py` 221 行、7 个平台文件、`config_flow` 132 行、`services` 102 行、`diagnostics` 39 行全部不执行。discover 结束后 `sys.modules` 里只有 `['epson_ef', 'epson_ef.api', 'epson_ef.const']`（实测）。

## 适用范围

- `tests/` 全部 4 个文件
- `tools/` 全部 20 个脚本
- 任何需要在本仓库新增的、要触碰生产代码的验证代码

## 规则

- **优先隔离**：如果被测模块 import HA，先问"能否绕开它的包入口"，而不是先写 mock。
- **伪包必须只设 `__path__`**，不得预填任何属性（`__file__` 保持 `None` 就是"`__init__.py` 未执行"的证明）。
- **必须在测试中显式记录覆盖缺口**：任何"HA 装配层"的行为都不得宣称已被验证。`tests/test_const_contract.py:6-9` 的写法是范例——它明说"不导入任何依赖 Home Assistant 的模块，因此在无 HA 的 CI 环境也能跑"，随后在 `docs/` 里记下装配层只能真机回归。
- **新增测试文件必须复用同一注入形态**（当前是复制 4 行，不是 import 一个 helper；见 ADR-004 的取舍）。
- **伪包注入必须幂等**：`if "epson_ef" not in sys.modules` 的保护不能删，否则 discover 同进程多文件会重复注册并可能覆盖真实模块。

## 反模式 / 禁止项

- ❌ 写 `sys.modules["homeassistant"] = MagicMock()` 之类的全局伪造——它会让 `__init__.py` "能跑"，但断言的是 mock 的行为。
- ❌ 在测试里 `from custom_components.epson_ef import ...`——这会走真实包入口，无 HA 时直接 ImportError。
- ❌ 用 `sys.path` 技巧替代 `__path__`（`tests/test_api.py:19` 的 `sys.path.insert` 对手法本身并非必需，实测在 `/tmp` 下不插也能导入；留着只是历史遗留）。
- ❌ 因为"64 用例全绿"就认为 `async_setup_entry`、迁移、平台转发正确——它们一行都没执行。
- ❌ 用 `*` 兜底或 `try/except ImportError` 包裹生产模块导入，把导入失败降级成 skip（这会让隔离手法退化成一个更隐蔽的 VH-S03）。

## 修改检查清单

- [ ] 新增测试文件时，是否复制了 `types.ModuleType` + `__path__` + `if not in sys.modules` 三件套？
- [ ] 新导入的生产模块是否真的零 HA 依赖？用 `grep -c homeassistant custom_components/epson_ef/<mod>.py` 确认是 0。
- [ ] 是否在文件/测试的 docstring 里写明了"本用例不覆盖装配层"？
- [ ] discover 后 `sys.modules` 里新增了哪些 `epson_ef.*`？是否与预期一致（当前应是 3 个）？
- [ ] 有没有为了让导入成功而给伪包补属性（如 `__file__`、`__spec__`）？补了就要问为什么。
