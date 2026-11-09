"""静态检查:防止"引用了不存在的名字"这类运行时才炸的 bug。

pyflakes 能发现 NameError 类问题(如把 self.api 写成 api),
这类错误 py_compile/unittest 都抓不到,但会在 HA 运行时报错。
"""
import pathlib
import subprocess
import sys
import unittest


class StaticAnalysisTests(unittest.TestCase):
    def test_no_undefined_names(self):
        try:
            import pyflakes  # noqa: F401
        except ImportError:
            self.skipTest("pyflakes not installed")

        root = (
            pathlib.Path(__file__).resolve().parents[1]
            / "custom_components"
            / "epson_ef"
        )
        files = sorted(str(p) for p in root.glob("*.py"))
        self.assertTrue(files, "no component files found")

        result = subprocess.run(
            [sys.executable, "-m", "pyflakes", *files],
            capture_output=True,
            text=True,
        )
        # 只关心致命级问题(未定义名字/重定义);未使用导入属于风格问题
        problems = [
            line
            for line in result.stdout.splitlines()
            if "undefined name" in line or "redefinition" in line
        ]
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
