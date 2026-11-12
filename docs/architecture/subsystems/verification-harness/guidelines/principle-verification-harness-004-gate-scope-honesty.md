---
project: "ha-epson-ef-projector"
type: development-principle
id: principle-verification-harness-004
description: "门禁的每一项都必须在『范围』与『规则集』两处显式声明，且声明范围要与保障承诺一致：CI 只查 custom_components+tests 的 F,E9 规则，因此 tools/ 与 HA 装配层的缺陷天然不可见——不得以 CI 全绿推断代码正确，也不得在保障缺口存在时扩大『已验证』的说法。"
base_commit: 2ac3d734d748f9a524f168520baf21307fc7b42c
principle_category: 治理
principle_scope: module
---

# 门禁范围必须显式，且与保障声明对齐

## 原则详细描述

本项目只有一条门禁，两条命令，写在 `.github/workflows/tests.yml`：

```yaml
- name: Install dependencies
  run: pip install aiohttp ruff pyflakes        # :18  不装 homeassistant
- name: Lint
  run: ruff check custom_components tests --select F,E9   # :21  范围不含 tools/
- name: Unit tests
  run: python -m unittest discover -s tests -v  # :24  64 用例
```

三个维度都被写死在命令行里，没有配置文件（仓库无 `pyproject.toml`、`ruff.toml`、`setup.cfg`、`requirements*.txt`，`ls` 实测均不存在）。这既保证了"CI 语义唯一"，也导致了"本地 `ruff check .` 与 CI 语义不同"的认知错位。

**关键事实（实测，非推断）**：

| 观测 | 命令 | 结果 |
|-|-|-|
| CI lint 同款 | `ruff check custom_components tests --select F,E9` | `All checks passed!`，rc=0 |
| HA 可用性 | `python3 -c "import homeassistant"` | `ModuleNotFoundError`，rc=1 |
| 伪包副作用 | discover 后过滤 `sys.modules` | 仅 `['epson_ef','epson_ef.api','epson_ef.const']`——14 个生产模块只执行了 2 个 |

也就是说：**仓库里存在 1 个"一跑即崩"的脚本，CI 依然全绿**。这不是 bug，而是范围声明的必然后果——但只有在范围被显式写下、且不被"CI 通过"的措辞掩盖时，它才是可接受的工程取舍。

## 为什么要这样

- **门禁的野心与它的可信度成反比**。若 CI 声称"全量检查"却只查两个目录，团队会停止手工检查 `tools/`，缺陷照旧存在但心理上已被"覆盖"。反之，明确声明"只查 X 与 Y"，则 3 个已知错误就是待办清单而非意外。
- **`--select F,E9` 是刻意的极窄规则集**：F = pyflakes（未定义名/未使用导入/重定义），E9 = 语法错误。它不拦风格、不拦复杂度、不拦未使用导入之外的一切。这与 `.github/workflows/tests.yml` 只有 4 个 step 的极简结构一致——门禁只要"能拦住会让集成在真机上炸的语法级错误"。
- **不装 homeassistant 是最大的范围声明**，也是最大的缺口：12/14 个生产模块 import HA，装配、平台注册、迁移逻辑全部零覆盖。历史已因此发生过一次全实体 unavailable（历史事故，见 `tests/test_const_contract.py:1-9`）。写清这条，才能解释为什么唯一的自动化验证方式是"整目录替换 + 重启 HA"。
- **无版本锁是范围声明的一部分**：`pip install ruff pyflakes` 浮动，`python-version: "3.13"` 固定，`aiohttp` 不锁。因此"CI 绿"绑定的是"某天的 ruff 默认行为"，不是某个确定工具链。

## 适用范围

- `.github/workflows/tests.yml`
- 任何新增/修改门禁步骤的变更
- 任何"已验证/已覆盖/CI 通过"的措辞

## 规则

- **范围与规则集必须写在命令行**，不得依赖隐式默认（仓库刻意没有 ruff/ pytest 配置文件）。
- **新增门禁项前先评估既存违规**：把 `tools/` 纳入 `ruff check .` 之前，必须先清掉 2 个 F401 与 1 个 F821，否则 CI 立刻变红——这是"扩大范围需要先还债"的显式成本。
- **保障声明必须与范围同宽**：不得用"64 用例全绿"暗示"集成可用"；不得用"CI 通过"暗示"`tools/` 可用"。
- **测试命令必须用 `discover`（或模块路径）而不是文件直跑**：`python3 tests/test_api.py` 只跑 42/51、`python3 tests/test_regressions.py` 只跑 7/9（实测），而两者 rc 均为 0。CI 用的 `python -m unittest discover -s tests -v` 是唯一能跑满 64 的方式。
- **`pyflakes` 必须由 CI 显式安装**：`tests/test_static.py` 依赖它，缺失时 `skipTest` 会让唯一用例静默消失（实测 skipped=1、rc=0）。CI 的 `pip install ... pyflakes`（`:18`）是这条保障的唯一支柱。

## 反模式 / 禁止项

- ❌ 在 README/文档里写 `pytest tests/` 而 CI 与依赖清单都不含 pytest（无 requirements 声明，CI 只装 3 个包）。
- ❌ 用 `ruff check .` 作为本地"和 CI 一样"的证明——实测 rc=1，与 CI 结论相反。
- ❌ 扩大 lint 范围却不修既存错误，然后加 `# noqa` 掩盖。
- ❌ 把 `test_static.py` 的 `skipTest` 当作"通过"。
- ❌ 在 CI 里跑真机脚本（会改设备状态、需要设备在场、可能打挂服务器）。
- ❌ 声称"支持 HA 2024.4+（`hacs.json:3`）"却没有任何 HA 版本矩阵验证。

## 修改检查清单

- [ ] 改 `.github/workflows/tests.yml` 后，范围是否仍在命令行显式写出？
- [ ] 若把 `tools/` 纳入 lint，是否已先跑 `ruff check .` 确认 0 错误？若否，是否记录了豁免清单？
- [ ] 测试步骤是否仍是 `unittest discover`（而非文件直跑）？
- [ ] 依赖安装是否仍含 `pyflakes`（否则 `test_static` 静默 skip）？
- [ ] 变更后本地的"CI 同款两条命令"是否仍与 workflow 逐字一致？
- [ ] 任何新增的"已验证"表述，是否同时写明了它不覆盖什么（HA 装配层 / tools/ / 真机）？
