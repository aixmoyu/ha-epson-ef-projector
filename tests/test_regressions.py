"""回归测试:真机测试阶段发现并修复的三个缺陷。

每个测试对应 2026-11-07 真机测试中的一条实测结论,防止回退:

1. 对焦步进语义 —— 固件在换向后首步 ±10、同向后续仅 ±1,所以"按步数
   换算"的算法到不了远距离目标;必须用 MAX/MIN + OFF 闭环定位。
2. 升级残留实体 —— 旧版本升级后 `setting-refresh` 与
   `button-engine_refresh` 变成永久 unavailable 的孤儿,清理表必须覆盖。
3. 弃用 API —— `device_registry.async_get_device(identifiers=...)`
   在 HA 2027.8.0 移除。
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


_PKG_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "epson_ef"

if "epson_ef" not in sys.modules:
    _pkg = types.ModuleType("epson_ef")
    _pkg.__path__ = [str(_PKG_DIR)]
    sys.modules["epson_ef"] = _pkg

from epson_ef import const  # noqa: E402
from epson_ef.api import (  # noqa: E402
    EpsonApi,
    EpsonApiCommandFailed,
    EpsonApiTimeout,
    EpsonApiUnavailable,
)


class FocusPositioningTests(unittest.IsolatedAsyncioTestCase):
    """`goto_focus` 必须闭环,不能假设 INC/DEC 的单步位移。"""

    async def test_goto_focus_uses_continuous_transport(self):
        api = EpsonApi("203.0.113.1", 8080)
        # 起点 89,目标 150:旧实现按"每步 10"算,只会走到 128。
        positions = [89, 120, 145, 150]
        calls: list[tuple[str, str | None]] = []

        async def fake_request(cmd_id, params=None, timeout=None):
            calls.append((cmd_id, params))
            return {"resultCode": 0, "response": ""}

        async def fake_pos():
            return positions.pop(0) if positions else 150

        with patch.object(api, "request", side_effect=fake_request), \
             patch.object(api, "get_focus_position", side_effect=fake_pos), \
             patch("asyncio.sleep", new=AsyncMock()):
            final = await api.goto_focus(150)
        await api.close()

        self.assertEqual(final, 150)
        params = [p for c, p in calls if c == "CONFIG_SET_FOCUS"]
        # 必须用连续移动命令,并按 OFF 收尾(每个脉冲后面都要停)
        self.assertIn("MAX", params)
        self.assertEqual(params.count("OFF"), params.count("MAX") + params.count("MIN"))

    async def test_goto_focus_stops_motor_when_sleep_is_cancelled(self):
        """取消(HA 关闭)时也必须发 OFF,否则马达会一直走到端点。"""
        api = EpsonApi("203.0.113.1", 8080)
        calls: list[str | None] = []

        async def fake_request(cmd_id, params=None, timeout=None):
            calls.append(params)
            return {"resultCode": 0, "response": ""}

        async def cancelled_sleep(_seconds):
            raise asyncio.CancelledError

        import asyncio
        with patch.object(api, "request", side_effect=fake_request), \
             patch.object(api, "get_focus_position", side_effect=[89]), \
             patch("asyncio.sleep", side_effect=cancelled_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await api.goto_focus(150)
        await api.close()

        self.assertIn("MAX", calls)
        self.assertIn("OFF", calls)

    async def test_focus_step_unit_is_one(self):
        """固件同向步进为 1,常量若被改回 10 说明又回到了错误假设。"""
        self.assertEqual(const.FOCUS_STEP_UNIT, 1)
        self.assertTrue(hasattr(const, "FOCUS_TRAVEL_UNITS_PER_SEC"))
        self.assertGreater(const.FOCUS_TRAVEL_UNITS_PER_SEC, 0)


class LegacyEntityCleanupTests(unittest.TestCase):
    """升级后不得留下永久 unavailable 的孤儿实体。"""

    def _legacy_map(self) -> dict:
        """从源码里取出清理表,不导入依赖 HA 的 __init__.py。"""
        tree = ast.parse((_PKG_DIR / "__init__.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "legacy":
                    return ast.literal_eval(node.value)
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "legacy":
                        return ast.literal_eval(node.value)
        self.fail("legacy cleanup table not found in __init__.py")

    def test_removed_entities_are_cleaned_up(self):
        legacy = self._legacy_map()
        # 旧版本创建过、当前版本不再创建的实体
        self.assertIn("setting-refresh", legacy.get("switch", ()))
        self.assertIn("button-engine_refresh", legacy.get("button", ()))
        # 更早版本被取代的传感器
        self.assertIn("standby_mode", legacy.get("sensor", ()))
        self.assertIn("light_output", legacy.get("sensor", ()))

    def test_no_deprecated_device_registry_lookup(self):
        """必须用 AST 检查真实调用,避免被注释/文档字符串里的文字误伤。"""
        tree = ast.parse((_PKG_DIR / "__init__.py").read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name != "async_get_device":
                continue
            if any(k.arg == "identifiers" for k in node.keywords):
                offenders.append(node.lineno)
        self.assertEqual(
            offenders, [],
            "async_get_device(identifiers=...) is deprecated and removed in "
            "HA 2027.8; use dr.async_entries_for_config_entry",
        )


class EntityRegistryApiTests(unittest.TestCase):
    """`async_get_entity_id` 的参数顺序是 (domain, platform, unique_id)。

    3.5.6 起这里一直传成 (platform, DOMAIN, uid),导致 legacy 实体清理
    静默失效 —— 升级用户的 `setting-refresh` / `button-engine_refresh`
    永久留在注册表里显示 unavailable。因为两者都是字符串,写反了不会
    报错,只能用 AST 校验实参位置。
    """

    def test_async_get_entity_id_argument_order(self):
        tree = ast.parse((_PKG_DIR / "__init__.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name != "async_get_entity_id":
                continue
            first = node.args[0] if node.args else None
            second = node.args[1] if len(node.args) > 1 else None
            self.assertIsInstance(first, ast.Name)
            self.assertEqual(
                first.id, "DOMAIN",
                f"__init__.py:{node.lineno}: first arg of async_get_entity_id "
                f"must be DOMAIN (signature: domain, platform, unique_id)",
            )
            self.assertNotEqual(
                getattr(second, "id", None), "DOMAIN",
                f"__init__.py:{node.lineno}: platform and domain look swapped",
            )


class CoordinatorContractTests(unittest.TestCase):
    """coordinator 的解包宽度已由 test_const_contract 覆盖,这里只保证
    coordinator 的轮询确实遍历了两张可写表(防止有人删掉读取逻辑)。"""

    def test_coordinator_reads_both_setting_tables(self):
        source = (_PKG_DIR / "coordinator.py").read_text(encoding="utf-8")
        self.assertIn("WRITABLE_SWITCHES", source)
        self.assertIn("WRITABLE_SELECTS", source)


class RemovedUnverifiedEntityTests(unittest.TestCase):
    """未被真机验证的写通路不得以实体形式暴露。

    HACS 评审要求:效果(或副作用)未在设备上确认的命令不能提供可点按钮,
    尤其是会改写用户标定状态的相机标定/重力传感器标定。这些命令一旦被
    重新加回表里,本测试必须同步且必须在标题里写明验证依据。
    """

    REMOVED_BUTTONS = (
        # 0.0.3: 评审逐条点名的未验证命令
        "input_source",
        "patternless_auto_focus",
        "manual_zoom",
        "camera_calibration",
        "depth_gsensor_calibration",
        "framefit",
        "generate_camera_pattern",
        "smart_eye_protect_run",
        # 0.0.4: 同一条规则下补全 —— 注释只写了"作用"、从未写实测结论,
        # 且其中几条会重写几何校正/标定/整机模式
        "manual_focus",
        "manual_qc",
        "manual_shift",
        "panel_alignment",
        "screen_fit",
        "btspk_mode",
        "floating_settings",
        "error_warning",
        "wall_color_fit",
        "auto_correction_run",
        "obstacle_avoidance_run",
        "geometry_reset",
    )

    # 当前唯一允许暴露的按钮集合(遥控器等价键 + 真机回环验证过的步进)
    EXPOSED_BUTTONS = {
        "home", "back", "menu", "auto_focus",
        "zoom_in", "zoom_out",
        "shift_up", "shift_down", "shift_left", "shift_right",
    }

    def _button_suffixes(self) -> set[str]:
        """用 AST 取表的第一列,不 import button.py(它依赖 homeassistant)。"""
        tree = ast.parse((_PKG_DIR / "button.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AnnAssign)
                and getattr(node.target, "id", None) == "BUTTON_DEFS"
                and isinstance(node.value, (ast.Tuple, ast.List))
            ):
                return {ast.literal_eval(row.elts[0]) for row in node.value.elts}
        self.fail("BUTTON_DEFS not found in button.py")

    def test_unverified_buttons_are_not_exposed(self):
        suffixes = self._button_suffixes()
        for suffix in self.REMOVED_BUTTONS:
            self.assertNotIn(
                suffix, suffixes,
                f"{suffix} 的效果未在真机确认,不能作为按钮暴露",
            )

    def test_verified_buttons_are_kept(self):
        # 防止"删干净"式回退:真机验证过的按钮必须保留
        suffixes = self._button_suffixes()
        for suffix in ("home", "back", "menu", "auto_focus", "zoom_in",
                       "zoom_out", "shift_up", "shift_down", "shift_left",
                       "shift_right"):
            self.assertIn(suffix, suffixes)
        # 反向:表里不允许出现上表之外的按钮,以免悄悄加回未验证项
        self.assertEqual(suffixes, self.EXPOSED_BUTTONS)

    def test_color_temp_write_path_is_not_exposed(self):
        from epson_ef.const import WRITABLE_SELECTS

        self.assertNotIn("color_temp", {row[0] for row in WRITABLE_SELECTS})
        # 读通路仍在:它是只读诊断值,不是写实体
        source = (_PKG_DIR / "coordinator.py").read_text(encoding="utf-8")
        self.assertIn("CONFIG_GET_COLOR_TEMP", source)

    def test_standby_mode_has_no_unverified_labels(self):
        from epson_ef import const

        self.assertFalse(
            hasattr(const, "STANDBY_MODE_OPTIONS"),
            "0/1 → 节能/快速启动 的映射未逐台核对,不得以带标签的实体暴露",
        )
        self.assertFalse(hasattr(const, "standby_option_to_raw"))
        self.assertFalse(hasattr(const, "standby_raw_to_option"))


class DiagnosticsRedactionTests(unittest.TestCase):
    """诊断包必须脱敏 host 与序列号(序列号同时是 base_id)。

    测试环境不装 homeassistant,只能校验源码确实调用了 async_redact_data
    且脱敏键覆盖 host/base_id/serial。
    """

    def test_diagnostics_uses_async_redact_data(self):
        source = (_PKG_DIR / "diagnostics.py").read_text(encoding="utf-8")
        self.assertIn("async_redact_data", source)
        self.assertIn("TO_REDACT", source)

    def test_redacted_keys_cover_host_and_serial(self):
        tree = ast.parse((_PKG_DIR / "diagnostics.py").read_text(encoding="utf-8"))
        keys: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if not any(
                getattr(t, "id", None) == "TO_REDACT" for t in targets
            ):
                continue
            for elt in node.value.elts:
                # 值可能是字面量 "base_id" 或常量名 CONF_HOST
                if isinstance(elt, ast.Constant):
                    keys.add(elt.value)
                elif isinstance(elt, ast.Name):
                    keys.add(elt.id)
        # host 用常量名 CONF_HOST 表达;序列号与 identity 键用字面量
        self.assertTrue(
            {"host", "CONF_HOST"} & keys,
            f"TO_REDACT 必须包含 host,实际为 {sorted(keys)}",
        )
        self.assertIn("base_id", keys)
        self.assertIn("serial", keys)

    def test_redaction_is_applied_to_every_section(self):
        tree = ast.parse((_PKG_DIR / "diagnostics.py").read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "async_redact_data"
        ]
        # entry / identity / coordinator.data / api 四段
        self.assertGreaterEqual(len(calls), 4)


class LegacyCleanupCoverageTests(unittest.TestCase):
    """本版移除的实体必须登记进 legacy 清理表,否则升级用户留孤儿。"""

    def _legacy_map(self) -> dict[str, tuple[str, ...]]:
        tree = ast.parse((_PKG_DIR / "__init__.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "legacy":
                    return ast.literal_eval(node.value)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "legacy":
                        return ast.literal_eval(node.value)
        self.fail("legacy cleanup table not found in __init__.py")

    def test_removed_buttons_are_cleaned_up(self):
        legacy = self._legacy_map()
        for suffix in RemovedUnverifiedEntityTests.REMOVED_BUTTONS:
            self.assertIn(f"button-{suffix}", legacy.get("button", ()))
        self.assertIn("button-engine_refresh", legacy.get("button", ()))

    def test_removed_selects_are_cleaned_up(self):
        legacy = self._legacy_map()
        self.assertIn("setting-color_temp", legacy.get("select", ()))
        self.assertIn("standby_mode", legacy.get("select", ()))


class PrivilegedServiceTests(unittest.TestCase):
    """send_key / raw_request 能发任意固件命令,必须限定管理员。

    两者都用 Home Assistant 自带的 async_register_admin_service 注册,
    而不是普通 async_register —— 这样权限检查由核心完成,不会因为漏写
    分支而退化。测试环境不装 homeassistant,只能校验源码。
    """

    def _source(self) -> str:
        return (_PKG_DIR / "services.py").read_text(encoding="utf-8")

    def _registered_names(self) -> dict[str, str]:
        """service 名 -> 注册时调用的函数名。"""
        tree = ast.parse(self._source())
        mapping: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = ast.unparse(node.func)
            if func not in ("async_register_admin_service",
                            "hass.services.async_register"):
                continue
            # 前两个位置参数是 hass/domain 或 domain;service 名是字符串常量
            names = [
                a.value for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
            for name in ("send_key", "raw_request"):
                if name in names:
                    mapping[name] = func
        return mapping

    def test_both_services_use_admin_registration(self) -> None:
        mapping = self._registered_names()
        self.assertEqual(set(mapping), {"send_key", "raw_request"})
        for name, func in mapping.items():
            self.assertEqual(
                func, "async_register_admin_service",
                f"{name} 必须用管理员注册,否则任何用户都能发原始命令",
            )

    def test_plain_registration_is_not_used(self) -> None:
        self.assertNotIn(
            "hass.services.async_register", self._source(),
            "不要退回普通注册:那会丢掉管理员校验",
        )


class UnreachableProjectorTests(unittest.IsolatedAsyncioTestCase):
    """不可达的投影仪必须报 EpsonApiUnavailable,而不是 EpsonApiTimeout。

    断电/换 IP/防火墙 DROP 时 TCP 连接不会立刻被拒,而是挂到超时。
    `probe_state` 的 liveness 检查如果只捕获 EpsonApiUnavailable,
    超时就会原样冒泡,违背"不可达"语义,也让只处理该异常的上层逻辑
    误判设备仍在线。真机测试中复现了这个问题。
    """

    async def test_timeout_on_liveness_becomes_unavailable(self):
        api = EpsonApi("203.0.113.9", 8080)

        async def fake_request(cmd_id, params=None, timeout=None):
            raise EpsonApiTimeout("timed out")

        with patch.object(api, "request", side_effect=fake_request):
            with self.assertRaises(EpsonApiUnavailable):
                await api.probe_state()
        await api.close()

    async def test_command_failure_still_means_standby(self):
        """liveness 通过但引擎查询 FAIL —— 必须仍判为待机,不能报不可达。"""
        api = EpsonApi("203.0.113.9", 8080)

        async def fake_request(cmd_id, params=None, timeout=None):
            if cmd_id == "CONFIG_GET_PRODUCT_NAME":
                return {"resultCode": 0, "response": "HB71"}
            raise EpsonApiCommandFailed("standby")

        with patch.object(api, "request", side_effect=fake_request), \
             patch("asyncio.sleep", new=AsyncMock()):
            state, _light = await api.probe_state()
        await api.close()
        self.assertEqual(state, "off")


if __name__ == "__main__":
    unittest.main()
