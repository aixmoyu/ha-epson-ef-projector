"""契约测试:const.py 的元组表与解包它们的消费者必须保持同构。

背景:3.10.0 发布前 `WRITABLE_SELECTS` 从 5 元组扩到 6 元组(新增 options),
但 coordinator.py 仍按 5 元组解包 —— 静态测试与单元测试都没抓到,真机
部署后每次诊断轮询都抛 `ValueError: too many values to unpack`,整个集成
的所有实体变成 unavailable。

本测试用 AST 直接比对两侧宽度,不导入任何依赖 Home Assistant 的模块,
因此在无 HA 的 CI 环境也能跑。
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types
import unittest

_PKG_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "epson_ef"

if "epson_ef" not in sys.modules:
    _pkg = types.ModuleType("epson_ef")
    _pkg.__path__ = [str(_PKG_DIR)]
    sys.modules["epson_ef"] = _pkg

# 元组表名 -> 定义所在的源文件
TABLE_SOURCES = {
    "WRITABLE_SWITCHES": "const.py",
    "WRITABLE_SELECTS": "const.py",
    "AMBIENT_LIGHT_SWITCHES": "const.py",
    "BUTTON_DEFS": "button.py",
}


def _parse(name: str) -> ast.Module:
    return ast.parse((_PKG_DIR / TABLE_SOURCES[name]).read_text(encoding="utf-8"))


def _table_arity(name: str) -> int:
    """由源码求得表项元组宽度(不信任 type annotation)。"""
    for node in ast.walk(_parse(name)):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            first = node.value.elts[0]
            if isinstance(first, ast.Tuple):
                return len(first.elts)
            raise AssertionError(f"{name} rows are not tuples: {first!r}")
    raise AssertionError(f"{name} not found in {TABLE_SOURCES[name]}")


def _table_ref(node: ast.expr) -> str | None:
    """`NAME` / `const.NAME` / `pkg.const.NAME` -> 最内层属性名。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _unpack_arities(name: str) -> list[tuple[str, int, int]]:
    """扫描全部组件源码,找出 `for a, b, ... in <name>` 的 (文件, 行, 宽度)。

    同时覆盖语句式 ``for`` 与列表/生成器推导式 —— 本项目两种写法都有,
    只认 ``ast.For`` 会漏掉 ``select.py`` / ``switch.py`` 里的推导式。
    """
    found: list[tuple[str, int, int]] = []
    for path in sorted(_PKG_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                target, it, lineno = node.target, node.iter, node.lineno
            elif isinstance(node, ast.comprehension):
                target, it, lineno = node.target, node.iter, node.target.lineno
            else:
                continue
            # target 必须是纯名字元组(嵌套元组不参与本契约)
            if not (
                isinstance(target, ast.Tuple)
                and all(isinstance(e, ast.Name) for e in target.elts)
            ):
                continue
            if _table_ref(it) == name:
                found.append((path.name, lineno, len(target.elts)))
    return found


class ConstContractTests(unittest.TestCase):
    def test_tables_are_non_empty_and_rectangular(self):
        for name in TABLE_SOURCES:
            arity = _table_arity(name)
            for node in ast.walk(_parse(name)):
                if (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == name
                    and isinstance(node.value, (ast.List, ast.Tuple))
                ):
                    rows = node.value.elts
                    self.assertTrue(rows, f"{name} must not be empty")
                    for row in rows:
                        self.assertEqual(
                            len(row.elts), arity,
                            f"{name} row width {len(row.elts)} != {arity}: "
                            f"{ast.unparse(row)}",
                        )

    def test_every_consumer_unpacks_full_width(self):
        """核心契约:每个解包点必须与表宽度一致。"""
        for name in TABLE_SOURCES:
            arity = _table_arity(name)
            consumers = _unpack_arities(name)
            self.assertTrue(
                consumers, f"{name} has no consumer — table may be dead code"
            )
            for filename, lineno, width in consumers:
                self.assertEqual(
                    width, arity,
                    f"{filename}:{lineno} unpacks {name} into {width} names, "
                    f"but rows have {arity} fields — this raises ValueError at "
                    f"runtime",
                )

    def test_known_consumer_locations(self):
        """防止有人改了表却把解包点挪到测试看不到的地方。"""
        expected = {
            ("WRITABLE_SWITCHES", "coordinator.py"),
            ("WRITABLE_SWITCHES", "switch.py"),
            ("WRITABLE_SELECTS", "coordinator.py"),
            ("WRITABLE_SELECTS", "select.py"),
            ("AMBIENT_LIGHT_SWITCHES", "switch.py"),
            ("BUTTON_DEFS", "button.py"),
        }
        actual = {
            (name, filename)
            for name in TABLE_SOURCES
            for filename, _lineno, _w in _unpack_arities(name)
        }
        self.assertEqual(expected - actual, set(), "missing expected consumers")


if __name__ == "__main__":
    unittest.main()
