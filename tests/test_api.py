"""Standalone tests for the pjctl client (no Home Assistant required).

Run with:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG_DIR = _REPO_ROOT / "custom_components" / "epson_ef"
sys.path.insert(0, str(_REPO_ROOT))

# Import epson_ef.api without executing the package __init__, which pulls in
# Home Assistant (not available in the standalone test environment).
if "epson_ef" not in sys.modules:
    _pkg = types.ModuleType("epson_ef")
    _pkg.__path__ = [str(_PKG_DIR)]
    sys.modules["epson_ef"] = _pkg

from epson_ef.api import (  # noqa: E402
    EpsonApi,
    EpsonApiCommandFailed,
    EpsonApiError,
    EpsonApiUnavailable,
)

CHALLENGE = (
    'Digest realm="Secret Zone", nonce="abc123", algorithm="MD5", qop="auth"'
)


class FakeResponse:
    """Minimal stand-in for an aiohttp response context manager."""

    def __init__(self, status=200, payload=None, headers=None, error=None):
        self.status = status
        self.headers = headers or {}
        self._payload = {} if payload is None else payload
        self._error = error

    async def __aenter__(self):
        if self._error is not None:
            raise self._error
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)


class FakeSession:
    """Records requests and replays canned responses in order."""

    closed = False

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self._responses:
            raise AssertionError("unexpected HTTP request")
        return self._responses.pop(0)


def challenge():
    return FakeResponse(401, headers={"WWW-Authenticate": CHALLENGE})


def ok(response="HB71"):
    return FakeResponse(
        200,
        {"resultCode": 0, "configResultCode": "SUCCESS", "response": response},
    )


def fail():
    return FakeResponse(200, {"resultCode": -1, "configResultCode": "FAIL"})


def make_api(responses):
    session = FakeSession(responses)
    return EpsonApi("192.0.2.10", 8080, session=session), session


class DigestTests(unittest.TestCase):
    def test_header_requires_nonce(self):
        api = EpsonApi("192.0.2.10", 8080)
        self.assertIsNone(api._digest_header("POST", "/api/pjctl"))

    def test_header_increments_nonce_count(self):
        api = EpsonApi("192.0.2.10", 8080)
        self.assertTrue(api._update_auth(CHALLENGE))
        first = api._digest_header("POST", "/api/pjctl")
        second = api._digest_header("POST", "/api/pjctl")
        self.assertIn("nc=00000001", first)
        self.assertIn("nc=00000002", second)
        self.assertIn('username="EPSONICL"', first)
        self.assertIn("qop=auth", first)

    def test_malformed_challenge_rejected(self):
        api = EpsonApi("192.0.2.10", 8080)
        self.assertFalse(api._update_auth("Basic realm=x"))


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_challenge_then_success(self):
        api, session = make_api([challenge(), ok()])
        data = await api.request("CONFIG_GET_PRODUCT_NAME")
        self.assertEqual(data["response"], "HB71")
        self.assertEqual(len(session.calls), 2)
        self.assertNotIn("Authorization", session.calls[0]["headers"])
        self.assertTrue(
            session.calls[1]["headers"]["Authorization"].startswith("Digest ")
        )

    async def test_cached_nonce_avoids_second_challenge(self):
        api, session = make_api([challenge(), ok(), ok("191 2971")])
        await api.request("CONFIG_GET_PRODUCT_NAME")
        data = await api.request("CONFIG_GET_LIGHT_TIME")
        self.assertEqual(data["response"], "191 2971")
        self.assertEqual(len(session.calls), 3)
        self.assertIn("Authorization", session.calls[2]["headers"])

    async def test_command_failure(self):
        api, _ = make_api([challenge(), fail()])
        with self.assertRaises(EpsonApiCommandFailed):
            await api.request("CONFIG_GET_LIGHT_TIME")

    async def test_connection_error_is_unavailable_and_clears_nonce(self):
        api, _ = make_api(
            [challenge(), FakeResponse(error=aiohttp.ClientConnectionError("boom"))]
        )
        with self.assertRaises(EpsonApiUnavailable):
            await api.request("CONFIG_GET_PRODUCT_NAME")
        self.assertIsNone(api._auth)

    async def test_http_error(self):
        api, _ = make_api([FakeResponse(500, payload="oops")])
        with self.assertRaises(EpsonApiError):
            await api.request("CONFIG_GET_PRODUCT_NAME")

    async def test_missing_nonce_in_401(self):
        api, _ = make_api(
            [FakeResponse(401, headers={"WWW-Authenticate": "Basic"})]
        )
        with self.assertRaises(EpsonApiError):
            await api.request("X")


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_parses_light_time(self):
        # PRODUCT_NAME 探活 + LIGHT_TIME 成功
        api, _ = make_api(
            [challenge(), ok("HB71"), challenge(), ok("191 2971")]
        )
        self.assertEqual(await api.probe_state(), ("on", (191, 2971)))

    async def test_probe_off_when_light_time_fails(self):
        # 探活成功,但 LIGHT_TIME FAIL → 待机
        api, session = make_api(
            [challenge(), ok("HB71"), challenge(), fail(), challenge(), fail()]
        )
        with patch("asyncio.sleep", new=AsyncMock()):
            self.assertEqual(await api.probe_state(), ("off", None))
        self.assertEqual(len(session.calls), 6)

    async def test_probe_verify_false_single_attempt(self):
        api, session = make_api(
            [challenge(), ok("HB71"), challenge(), fail()]
        )
        self.assertEqual(await api.probe_state(verify=False), ("off", None))
        self.assertEqual(len(session.calls), 4)

    async def test_probe_off_on_timeout_deep_standby(self):
        # 冷启动深待机:LIGHT_TIME 超时(服务器掐断连接的另一种形态)
        api, session = make_api(
            [
                challenge(), ok("HB71"),
                FakeResponse(error=asyncio.TimeoutError()),
            ]
        )
        self.assertEqual(await api.probe_state(), ("off", None))

    async def test_probe_off_when_light_time_connection_dropped(self):
        # 深待机:服务器对灯时长查询直接掐断连接(探活已通过 → 判待机,不能判不可达)
        api, _ = make_api(
            [
                challenge(), ok("HB71"),
                FakeResponse(error=aiohttp.ServerDisconnectedError()),
            ]
        )
        self.assertEqual(await api.probe_state(), ("off", None))

    async def test_probe_unreachable_when_liveness_fails(self):
        # PRODUCT_NAME 连不上 → 真不可达(主机关机)
        api, _ = make_api(
            [FakeResponse(error=aiohttp.ClientConnectionError("boom"))]
        )
        with self.assertRaises(EpsonApiUnavailable):
            await api.probe_state()


class PayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_keyevent_payload(self):
        api, session = make_api([challenge(), ok()])
        await api.send_keyevent(26)
        body = session.calls[1]["json"]
        # size = params 的空格分词数(非字节长度)
        self.assertEqual(body, {"id": "NOTIFY_KEYEVENT", "size": 1, "params": "26"})

    async def test_set_standby_mode_payload(self):
        api, session = make_api([challenge(), ok()])
        await api.set_standby_mode("1")
        body = session.calls[1]["json"]
        self.assertEqual(
            body,
            {"id": "CONFIG_SET_PJ_STANDBY_MODE", "size": 1, "params": "1"},
        )

    async def test_set_light_output_size_is_token_count(self):
        # 多字符单 token 的 size 必须是 1(UI Binder 同款):这是
        # SET_LIGHT_OUTPUT/SET_PICTURE 写通路被拒根因的修复验证
        api, session = make_api([challenge(), ok()])
        await api.set_light_output(55)
        body = session.calls[1]["json"]
        self.assertEqual(
            body,
            {"id": "CONFIG_SET_LIGHT_OUTPUT", "size": 1, "params": "55"},
        )

    async def test_set_ambient_light_payloads(self):
        api, session = make_api([challenge(), ok(), ok(), ok()])
        await api.set_ambient_light(4, True)
        self.assertEqual(
            session.calls[1]["json"],
            {"id": "CONFIG_SET_AMBIENT_LIGHT", "size": 2, "params": "4 1"},
        )
        await api.set_ambient_light(4, False)
        self.assertEqual(
            session.calls[2]["json"],
            {"id": "CONFIG_SET_AMBIENT_LIGHT", "size": 2, "params": "4 0"},
        )
        await api.set_ambient_light(3, True)
        self.assertEqual(
            session.calls[3]["json"],
            {"id": "CONFIG_SET_AMBIENT_LIGHT", "size": 3, "params": "3 1 48"},
        )

    async def test_get_serial_number_uses_subindex_param(self):
        # 固件只在 params="1" 子索引下返回序列号
        api, session = make_api([challenge(), ok("XEQM0000000")])
        self.assertEqual(await api.get_serial_number(), "XEQM0000000")
        body = session.calls[1]["json"]
        self.assertEqual(
            body, {"id": "CONFIG_GET_PJ_SNO", "size": 1, "params": "1"}
        )

    async def test_get_serial_number_returns_none_on_failure(self):
        api, _ = make_api([challenge(), fail()])
        self.assertIsNone(await api.get_serial_number())

    async def test_get_serial_number_trims_value(self):
        api, _ = make_api([challenge(), ok("  SNO12345  ")])
        self.assertEqual(await api.get_serial_number(), "SNO12345")


class StandbyModeMappingTests(unittest.TestCase):
    def test_option_to_raw(self):
        from epson_ef.const import standby_option_to_raw

        self.assertEqual(standby_option_to_raw("节能"), "0")
        self.assertEqual(standby_option_to_raw("快速启动"), "1")
        self.assertIsNone(standby_option_to_raw("未知"))

    def test_raw_to_option(self):
        from epson_ef.const import standby_raw_to_option

        self.assertEqual(standby_raw_to_option("0"), "节能")
        self.assertEqual(standby_raw_to_option("1"), "快速启动")
        self.assertIsNone(standby_raw_to_option(None))
        self.assertIsNone(standby_raw_to_option("9"))


class Round4Tests(unittest.IsolatedAsyncioTestCase):
    """刷新定时器 + 手动对焦(真机回环验证)。"""

    async def test_get_refresh_timer_parses_hours(self):
        # GET_REFRESH 返回定时器小时数(非 0/1 标志)
        api, _ = make_api([challenge(), ok("4")])
        self.assertEqual(await api.get_refresh_timer(), 4)

    async def test_get_refresh_timer_none_on_fail(self):
        # 待机时 FAIL → None(与 LIGHT_TIME 同款行为)
        api, _ = make_api([challenge(), fail()])
        self.assertIsNone(await api.get_refresh_timer())

    async def test_set_refresh_timer_payload(self):
        # 写格式 = SET_REFRESH "0 <h>",size=2
        api, session = make_api([challenge(), ok()])
        await api.set_refresh_timer(12)
        self.assertEqual(
            session.calls[1]["json"],
            {"id": "CONFIG_SET_REFRESH", "size": 2, "params": "0 12"},
        )

    async def test_get_focus_range(self):
        # GET_FOCUS "RANGE" → "0 264"(HB71 实测)
        api, session = make_api([challenge(), ok("0 264")])
        self.assertEqual(await api.get_focus_range(), (0, 264))
        body = session.calls[1]["json"]
        self.assertEqual(body, {"id": "CONFIG_GET_FOCUS", "size": 1, "params": "RANGE"})

    async def test_get_focus_range_malformed(self):
        api, _ = make_api([challenge(), ok("0")])
        self.assertIsNone(await api.get_focus_range())

    async def test_get_focus_position(self):
        # GET_FOCUS "POS" → "110"(HB71 实测)
        api, session = make_api([challenge(), ok("110")])
        self.assertEqual(await api.get_focus_position(), 110)
        body = session.calls[1]["json"]
        self.assertEqual(body, {"id": "CONFIG_GET_FOCUS", "size": 1, "params": "POS"})

    async def test_focus_step_payloads(self):
        # SET_FOCUS "INC"/"DEC",size=1(单步对焦)
        api, session = make_api([challenge(), ok(), ok()])
        await api.focus_step(1)
        self.assertEqual(
            session.calls[1]["json"],
            {"id": "CONFIG_SET_FOCUS", "size": 1, "params": "INC"},
        )
        await api.focus_step(-1)
        self.assertEqual(
            session.calls[2]["json"],
            {"id": "CONFIG_SET_FOCUS", "size": 1, "params": "DEC"},
        )


class Round5Tests(unittest.IsolatedAsyncioTestCase):
    """色彩增益 + 氛围灯亮度/模式 + 测试图案(真机回环验证)。"""

    async def test_get_color_gain_parses_triple(self):
        # GET_COLOR_GAIN "0" → "R G B" 三元组(实机出厂 81 90 100)
        api, session = make_api([challenge(), ok("81 90 100")])
        self.assertEqual(await api.get_color_gain(), (81, 90, 100))
        body = session.calls[1]["json"]
        self.assertEqual(
            body, {"id": "CONFIG_GET_COLOR_GAIN", "size": 1, "params": "0"}
        )

    async def test_get_color_gain_malformed_and_fail(self):
        api, _ = make_api([challenge(), ok("81 90")])
        self.assertIsNone(await api.get_color_gain())
        api2, _ = make_api([challenge(), fail()])
        self.assertIsNone(await api2.get_color_gain())

    async def test_set_color_gain_payload(self):
        # 写格式 = "R G B",size=3(0–255 全范围真机回环验证)
        api, session = make_api([challenge(), ok()])
        await api.set_color_gain(80, 90, 100)
        self.assertEqual(
            session.calls[1]["json"],
            {"id": "CONFIG_SET_COLOR_GAIN", "size": 3, "params": "80 90 100"},
        )

    async def test_set_ambient_brightness_payload_and_clamp(self):
        # 亮度:UI 0–100 → 固件 0–255(线性映射)
        api, session = make_api([challenge(), ok(), ok(), ok()])
        await api.set_ambient_brightness(78)  # 78*2.55 = 198.9 → 199
        self.assertEqual(
            session.calls[1]["json"],
            {"id": "CONFIG_SET_AMBIENT_LIGHT", "size": 3, "params": "9 199 48"},
        )
        await api.set_ambient_brightness(0)
        self.assertEqual(
            session.calls[2]["json"],
            {"id": "CONFIG_SET_AMBIENT_LIGHT", "size": 3, "params": "9 0 48"},
        )
        await api.set_ambient_brightness(100)
        self.assertEqual(
            session.calls[3]["json"],
            {"id": "CONFIG_SET_AMBIENT_LIGHT", "size": 3, "params": "9 255 48"},
        )

    async def test_set_ambient_mode_payload(self):
        # 光效模式:MODE1..4 = 固件槽 3..6
        api, session = make_api([challenge(), ok()])
        await api.set_ambient_mode(3)
        self.assertEqual(
            session.calls[1]["json"],
            {"id": "CONFIG_SET_AMBIENT_LIGHT", "size": 3, "params": "1 3 48"},
        )

    async def test_set_test_pattern_payload(self):
        # AGPATTERN 走 GET_ESCVP 透传(带响应读回),无需 MCU debug 模式
        api, session = make_api([challenge(), ok(), ok()])
        await api.set_test_pattern(True)
        self.assertEqual(
            session.calls[1]["json"],
            {"id": "CONFIG_GET_ESCVP", "size": 3, "params": "2 AGPATTERN 01"},
        )
        await api.set_test_pattern(False)
        self.assertEqual(
            session.calls[2]["json"],
            {"id": "CONFIG_GET_ESCVP", "size": 3, "params": "2 AGPATTERN 00"},
        )


class Round6Tests(unittest.IsolatedAsyncioTestCase):
    """SET_QC 几何子命令表(真机 ± 回环零残差验证)。"""

    async def test_geometry_zoom_payload(self):
        # "1 ±104"(size=2):+ = 放大(四角外扩),真机对称回环零残差
        api, session = make_api([challenge(), ok(), ok()])
        await api.geometry_zoom(1)
        self.assertEqual(
            session.calls[1]["json"],
            {"id": "CONFIG_SET_QC", "size": 2, "params": "1 104"},
        )
        await api.geometry_zoom(-1)
        self.assertEqual(
            session.calls[2]["json"],
            {"id": "CONFIG_SET_QC", "size": 2, "params": "1 -104"},
        )

    async def test_geometry_shift_payloads(self):
        # "2 dx dy"(size=3):单步 13,+x=右、+y=下(ShiftActivity 键位映射)
        api, session = make_api([challenge(), ok(), ok(), ok(), ok()])
        await api.geometry_shift(1, 0)   # 右
        self.assertEqual(
            session.calls[1]["json"],
            {"id": "CONFIG_SET_QC", "size": 3, "params": "2 13 0"},
        )
        await api.geometry_shift(-1, 0)  # 左
        self.assertEqual(
            session.calls[2]["json"],
            {"id": "CONFIG_SET_QC", "size": 3, "params": "2 -13 0"},
        )
        await api.geometry_shift(0, 1)   # 下
        self.assertEqual(
            session.calls[3]["json"],
            {"id": "CONFIG_SET_QC", "size": 3, "params": "2 0 13"},
        )
        await api.geometry_shift(0, -1)  # 上
        self.assertEqual(
            session.calls[4]["json"],
            {"id": "CONFIG_SET_QC", "size": 3, "params": "2 0 -13"},
        )

    async def test_geometry_reset_payload(self):
        # "5"(size=1)= 机身「重置校正」同款
        api, session = make_api([challenge(), ok()])
        await api.geometry_reset()
        self.assertEqual(
            session.calls[1]["json"],
            {"id": "CONFIG_SET_QC", "size": 1, "params": "5"},
        )

    async def test_set_auto_contrast_payload(self):
        # SET_AUTO_CONTRAST "0"/"1"(size=1)写 SUCCESS,
        # GET 带参实测 FAIL → 乐观开关(氛围灯同款先例)
        api, session = make_api([challenge(), ok(), ok()])
        await api.set_auto_contrast(True)
        self.assertEqual(
            session.calls[1]["json"],
            {"id": "CONFIG_SET_AUTO_CONTRAST", "size": 1, "params": "1"},
        )
        await api.set_auto_contrast(False)
        self.assertEqual(
            session.calls[2]["json"],
            {"id": "CONFIG_SET_AUTO_CONTRAST", "size": 1, "params": "0"},
        )

    async def test_get_auto_focus_subcommands(self):
        # GET_AUTO_FOCUS 带参快速返回(无参数查询挂)
        # "0" → 状态(HB71 "1");"1" → 自动对焦目标位置(HB71 "196",行程 0–264)
        api, session = make_api([challenge(), ok("1"), ok("196")])
        self.assertEqual(await api.get_auto_focus_state(), "1")
        body = session.calls[1]["json"]
        self.assertEqual(
            body, {"id": "CONFIG_GET_AUTO_FOCUS", "size": 1, "params": "0"}
        )
        self.assertEqual(await api.get_auto_focus_target(), 196)
        body = session.calls[2]["json"]
        self.assertEqual(
            body, {"id": "CONFIG_GET_AUTO_FOCUS", "size": 1, "params": "1"}
        )


if __name__ == "__main__":
    unittest.main()


class FocusAbsoluteTests(unittest.IsolatedAsyncioTestCase):
    """`goto_focus` / `focus_move_*`:真机回环验证的通路。

    真机语义(2026-11-07 复测):SET_FOCUS MIN/MAX 连续移动直到 OFF,
    INC/DEC 则只在方向反转时给 ±10、同向后续只给 ±1。因此绝对定位
    必须走 "连续移动 + 按剩余距离定时 + OFF + 回读" 的闭环,不能按
    步数换算。
    """

    async def test_move_start_stop_params(self):
        # 首次请求带 401 挑战,之后每次 SET 各需一个 200 响应
        api, session = make_api([challenge(), ok(), ok(), ok()])
        await api.focus_move_start(+1)
        await api.focus_move_start(-1)
        await api.focus_move_stop()
        sent = [c["json"]["params"] for c in session.calls if "json" in c]
        self.assertEqual(sent[-3:], ["MAX", "MIN", "OFF"])

    async def test_goto_focus_converges_and_pulses_in_direction(self):
        api = EpsonApi("192.0.2.10", 8080)
        positions = [100, 160, 200]
        params: list[str | None] = []

        async def fake_request(cmd_id, params_=None, timeout=None, **kw):
            params.append(kw.get("params", params_))
            return {"resultCode": 0}

        call_args = []
        async def req(cmd_id, params=None, timeout=None):
            call_args.append(params)
            return {"resultCode": 0}

        async def fake_pos():
            return positions.pop(0) if positions else 200

        with patch.object(api, "request", side_effect=req), \
             patch.object(api, "get_focus_position", side_effect=fake_pos), \
             patch("asyncio.sleep", new=AsyncMock()):
            final = await api.goto_focus(200)
        await api.close()

        self.assertEqual(final, 200)
        self.assertIn("MAX", call_args)
        self.assertEqual(call_args.count("OFF"),
                         call_args.count("MAX") + call_args.count("MIN"))

    async def test_goto_focus_returns_none_when_position_unreadable(self):
        api = EpsonApi("192.0.2.10", 8080)

        async def fake_request(cmd_id, params=None, timeout=None):
            raise EpsonApiCommandFailed("standby")

        with patch.object(api, "request", side_effect=fake_request):
            final = await api.goto_focus(150)
        await api.close()
        self.assertIsNone(final)


class ReadHelperTests(unittest.IsolatedAsyncioTestCase):
    """读辅助方法的容错:失败一律返回 None,不向上抛。"""

    async def test_get_light_output_parses_and_tolerates_garbage(self):
        for payload, expected in (("100", 100), ("55.0", 55), ("", None), ("abc", None)):
            api, session = make_api([challenge(), ok(payload)])
            self.assertEqual(await api.get_light_output(), expected)

    async def test_get_color_gain_requires_three_tokens(self):
        for payload, expected in (
            ("81 90 100", (81, 90, 100)),
            ("81 90", None),
            ("a b c", None),
        ):
            api, session = make_api([challenge(), ok(payload)])
            self.assertEqual(await api.get_color_gain(), expected)

    async def test_get_raw_returns_none_on_fail(self):
        api, session = make_api([challenge(), fail()])
        self.assertIsNone(await api.get_raw("CONFIG_GET_PICTURE"))

    async def test_get_distance_mm(self):
        api, session = make_api([challenge(), ok("3558.5")])
        self.assertEqual(await api.get_distance_mm(), 3558.5)


class PowerStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_for_state_times_out_false(self):
        api = EpsonApi("192.0.2.10", 8080)

        async def fake_probe(verify=True):
            return ("off", None)

        with patch.object(api, "probe_state", side_effect=fake_probe), \
             patch("asyncio.sleep", new=AsyncMock()):
            self.assertFalse(await api.wait_for_state("on", timeout_s=0))

    async def test_wait_for_state_true_when_matched(self):
        api = EpsonApi("192.0.2.10", 8080)

        async def fake_probe(verify=True):
            return ("on", (1, 2))

        with patch.object(api, "probe_state", side_effect=fake_probe):
            self.assertTrue(await api.wait_for_state("on", timeout_s=1))
