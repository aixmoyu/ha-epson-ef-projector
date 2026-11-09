"""Async client for the projector's native WebApi (pjctl over HTTP).

Pure aiohttp + stdlib; no Home Assistant imports so it can be tested standalone.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
from typing import Any

import aiohttp

from .const import (
    AMBIENT_BRIGHTNESS_FW_SCALE,
    API_PATH,
    DIGEST_PASSWORD,
    DIGEST_REALM,
    DIGEST_USER,
    FOCUS_GOTO_MAX_ATTEMPTS,
    FOCUS_MAX_PULSE_S,
    FOCUS_MIN_PULSE_S,
    FOCUS_SETTLE_POLL,
    FOCUS_TOLERANCE,
    FOCUS_TRAVEL_UNITS_PER_SEC,
    KEYCODE_SEND,
    QC_SHIFT_STEP,
    QC_ZOOM_STEP,
    TEST_PATTERN_OFF,
    TEST_PATTERN_ON,
)

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=10)


class EpsonApiError(Exception):
    """Generic API failure."""


class EpsonApiUnavailable(EpsonApiError):
    """The projector's HTTP endpoint is unreachable (mains off / network down)."""


class EpsonApiCommandFailed(EpsonApiError):
    """The endpoint answered but the command reported FAIL.

    Some queries legitimately FAIL in certain power states —
    e.g. CONFIG_GET_LIGHT_TIME fails while the projector is in standby.
    """


class EpsonApiTimeout(EpsonApiError):
    """The command timed out before the server answered.

    Distinct from EpsonApiUnavailable: the endpoint is alive but the command
    could not complete — typical for engine queries in deep standby.
    """


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class EpsonApi:
    """Small async Digest-auth JSON client for /api/pjctl."""

    def __init__(
        self,
        host: str,
        port: int,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._url = f"http://{host}:{port}{API_PATH}"
        self._session = session
        self._owns_session = session is None
        # Digest parameters are valid until the server rotates the nonce, so
        # cache them and avoid a 401 round-trip for every command.
        self._auth: tuple[str, str, str] | None = None  # nonce, realm, qop
        self._nc = 0

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------------
    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    def _update_auth(self, www_authenticate: str) -> bool:
        """Parse a Digest challenge and cache it. Returns False if malformed."""
        nonce_m = re.search(r'nonce="([^"]+)"', www_authenticate)
        if not nonce_m:
            return False
        realm_m = re.search(r'realm="([^"]+)"', www_authenticate)
        qop_m = re.search(r'qop="?([A-Za-z]+)"?', www_authenticate)
        self._auth = (
            nonce_m.group(1),
            realm_m.group(1) if realm_m else DIGEST_REALM,
            qop_m.group(1) if qop_m else "auth",
        )
        self._nc = 0
        _LOGGER.debug("Cached new digest nonce for %s", self._url)
        return True

    def _clear_auth(self) -> None:
        self._auth = None
        self._nc = 0

    def _digest_header(self, method: str, uri: str) -> str | None:
        if self._auth is None:
            return None
        nonce, realm, qop = self._auth
        # Incrementing synchronously means concurrent coroutines can never
        # send the same nonce-count twice.
        self._nc += 1
        nc = f"{self._nc:08x}"
        cnonce = f"{random.randint(0, 0xFFFFFFFF):08x}"
        ha1 = _md5(f"{DIGEST_USER}:{realm}:{DIGEST_PASSWORD}")
        ha2 = _md5(f"{method}:{uri}")
        if qop:
            response = _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
        else:
            response = _md5(f"{ha1}:{nonce}:{ha2}")

        parts = [
            f'username="{DIGEST_USER}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{uri}"',
            'algorithm="MD5"',
            f'response="{response}"',
        ]
        if qop:
            parts.extend([f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"'])
        return "Digest " + ", ".join(parts)

    async def request(
        self,
        cmd_id: str,
        params: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST one pjctl command. Returns the parsed response JSON.

        Raises EpsonApiUnavailable on network failure and
        EpsonApiCommandFailed when the device reports resultCode != 0.
        ``timeout`` overrides the default per-request timeout — use a short
        one for probes that may block in deep standby.
        """
        total = timeout if timeout is not None else 10.0
        payload: dict[str, Any] = {
            "id": cmd_id,
            # `size` is the number of space-separated tokens in params, NOT
            # the byte length (verified on device: the web server forwards it
            # verbatim to the Binder config(id, size, params) call, and the
            # firmware UI always passes the token count — e.g.
            # SET_LIGHT_OUTPUT size=1 params="100", SET_REFRESH size=2
            # params="0 4". A mismatched size makes the MCU reject the write
            # with FAIL, which is why those writes used to be refused).
            "size": 0 if params is None else len(params.split(" ")),
        }
        if params is not None:
            payload["params"] = params

        session = await self._ensure_session()

        # First attempt may carry a cached nonce; on 401 we refresh the
        # challenge and retry exactly once.
        for attempt in range(2):
            headers = {"Content-Type": "application/json"}
            auth_header = self._digest_header("POST", API_PATH)
            if auth_header:
                headers["Authorization"] = auth_header
            try:
                async with session.post(
                    self._url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=total),
                ) as resp:
                    if resp.status == 200:
                        try:
                            data = await resp.json(content_type=None)
                        except Exception as err:  # noqa: BLE001
                            raise EpsonApiError(f"bad JSON response: {err}") from err
                        if int(data.get("resultCode", -1)) != 0:
                            raise EpsonApiCommandFailed(
                                f"{cmd_id}: {data.get('configResultCode', 'FAIL')}"
                            )
                        return data
                    if resp.status == 401 and attempt == 0:
                        www = resp.headers.get("WWW-Authenticate", "")
                        if not self._update_auth(www):
                            raise EpsonApiError("401 without digest challenge")
                        continue
                    text = await resp.text()
                    raise EpsonApiError(f"HTTP {resp.status}: {text[:120]}")
            except asyncio.TimeoutError as err:
                self._clear_auth()
                raise EpsonApiTimeout(str(err)) from err
            except aiohttp.ClientError as err:
                # Broad on purpose: payload/decode errors are network problems
                # too, and the cached nonce may no longer be trustworthy.
                self._clear_auth()
                raise EpsonApiUnavailable(str(err)) from err

        raise EpsonApiError("digest authentication failed")

    # ------------------------------------------------------------------
    # high-level helpers
    # ------------------------------------------------------------------
    async def probe_state(
        self, verify: bool = True
    ) -> tuple[str, tuple[int, int] | None]:
        """Detect power state, robust across every standby flavour.

        1. ``CONFIG_GET_PRODUCT_NAME`` answers instantly in every power
           state — liveness check. Failure means the device is truly
           unreachable (mains off) and EpsonApiUnavailable is raised.
        2. ``CONFIG_GET_LIGHT_TIME`` succeeds only while awake. It either
           FAILs (normal standby) or the server drops the connection
           (deep standby) — both map to "off". With ``verify`` a FAIL is
           re-checked once after a short delay to avoid transient false
           readings during power-state transitions.
        """
        # 1) liveness
        try:
            await self.request("CONFIG_GET_PRODUCT_NAME")
        except EpsonApiUnavailable:
            raise
        except EpsonApiTimeout as err:
            # A host that does not answer at all (mains off, wrong IP, or a
            # firewall DROP) surfaces as a timeout rather than a connection
            # error. It is just as unreachable, so normalise it — otherwise
            # callers that only handle EpsonApiUnavailable would treat a
            # powered-off projector as "alive but wedged".
            raise EpsonApiUnavailable(str(err)) from err

        # 2) wake flag with a short timeout. Once liveness passed, ANY
        # failure of this engine query means "standby" — the firmware either
        # FAILs it, times out, or drops the connection entirely in deep
        # standby. It must never surface as "unreachable".
        attempts = 2 if verify else 1
        for attempt in range(attempts):
            try:
                data = await self.request(
                    "CONFIG_GET_LIGHT_TIME", timeout=5.0
                )
            except (EpsonApiTimeout, EpsonApiUnavailable):
                return "off", None
            except EpsonApiCommandFailed:
                if attempt + 1 < attempts:
                    await asyncio.sleep(1.5)
                    continue
                return "off", None
            raw = (data.get("response") or "").strip()
            parts = raw.split()
            try:
                light = (
                    (int(parts[0]), int(parts[1]))
                    if len(parts) >= 2
                    else (int(parts[0]), None)
                )
            except (ValueError, IndexError):
                return "on", None
            return "on", light
        return "off", None

    async def wait_for_state(
        self, target: str, timeout_s: float = 10.0
    ) -> bool:
        """Poll probe_state until it reports `target` (best-effort confirm)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            try:
                state, _ = await self.probe_state(verify=False)
            except EpsonApiError:
                return False
            if state == target:
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(1.0)

    async def get_product_name(self) -> str | None:
        try:
            data = await self.request("CONFIG_GET_PRODUCT_NAME")
        except EpsonApiError:
            return None
        return (data.get("response") or "").strip() or None

    async def get_serial_number(self) -> str | None:
        """Return the projector serial number, used as a stable identity.

        The firmware only answers CONFIG_GET_PJ_SNO with params="1" (the
        settings UI uses the same sub-index); without params it returns an
        empty string. Returns None when the command fails in the current
        power state; callers must then fall back to a host/port based
        identity.
        """
        try:
            data = await self.request("CONFIG_GET_PJ_SNO", params="1")
        except EpsonApiError:
            return None
        return (data.get("response") or "").strip() or None

    async def get_standby_mode(self) -> str | None:
        try:
            data = await self.request("CONFIG_GET_PJ_STANDBY_MODE")
        except EpsonApiError:
            return None
        return (data.get("response") or "").strip() or None

    async def set_standby_mode(self, value: str) -> None:
        await self.request("CONFIG_SET_PJ_STANDBY_MODE", params=value)

    async def send_keyevent(self, keycode: int) -> None:
        await self.request(KEYCODE_SEND, params=str(int(keycode)))

    async def get_light_time(self) -> tuple[int, int | None]:
        """Raises EpsonApiCommandFailed while in standby."""
        data = await self.request("CONFIG_GET_LIGHT_TIME")
        parts = (data.get("response") or "").strip().split()
        if not parts:
            raise EpsonApiCommandFailed("empty light time")
        hours = int(parts[0])
        extra = int(parts[1]) if len(parts) >= 2 else None
        return hours, extra

    async def get_raw(self, cmd_id: str, params: str | None = None) -> str | None:
        """Generic read: returns the raw response string, None on failure.

        Many CONFIG_GET_* commands need params we have not mapped, and some
        hang the HTTP thread when the projector cannot answer — callers must
        apply a timeout and treat None as "not available in this state".
        """
        try:
            data = await self.request(cmd_id, params=params)
        except EpsonApiError:
            return None
        return (data.get("response") or "").strip() or None

    async def get_distance_mm(self) -> float | None:
        """Distance to the screen in millimetres (awake only)."""
        raw = await self.get_raw("CONFIG_GET_MEASURE_DISTANCE")
        if raw is None:
            return None
        try:
            return float(raw.split()[0])
        except (ValueError, IndexError):
            return None

    # ------------------------------------------------------------------
    # writable settings (enabled by the size=token-count fix)
    # ------------------------------------------------------------------
    async def get_light_output(self) -> int | None:
        """Light source brightness 50–100 (step 5), awake only."""
        raw = await self.get_raw("CONFIG_GET_LIGHT_OUTPUT", params="0")
        if raw is None:
            return None
        try:
            return int(float(raw.split()[0]))
        except (ValueError, IndexError):
            return None

    async def set_light_output(self, value: int) -> None:
        """Set the light source brightness (50–100). Raises EpsonApiCommandFailed."""
        await self.request("CONFIG_SET_LIGHT_OUTPUT", params=str(int(value)))

    async def get_picture_preference(self) -> str | None:
        """Projection preference: 35=colour (色彩), 6=bright (明亮)."""
        return await self.get_raw("CONFIG_GET_PICTURE")

    async def set_picture_preference(self, value: str) -> None:
        await self.request("CONFIG_SET_PICTURE", params=value)

    async def set_ambient_light(self, kind: int, on: bool) -> None:
        """Control the ambient light subsystem (write-only; no read-back).

        kind 3 = light on/off (params "3 1 48" / "3 0 48", the 48 is a
        firmware constant the UI always sends), 4 = welcome lighting,
        5 = touch sensor light.
        """
        if kind == 3:
            params = f"3 {1 if on else 0} 48"
        else:
            params = f"{kind} {1 if on else 0}"
        await self.request("CONFIG_SET_AMBIENT_LIGHT", params=params)

    async def set_ambient_brightness(self, ui_percent: int) -> None:
        """Set ambient light brightness (write-only, optimistic).

        params "9 <0-255> 48"; the firmware value is 0–255 while the UI
        exposes 0–100 (linear ×2.55, same as the mini app).
        Live-verified SUCCESS on HB71.
        """
        fw = max(0, min(255, round(ui_percent * AMBIENT_BRIGHTNESS_FW_SCALE)))
        await self.request("CONFIG_SET_AMBIENT_LIGHT", params=f"9 {fw} 48")

    async def set_ambient_mode(self, mode_id: int) -> None:
        """Select an ambient light effect slot (write-only, optimistic).

        params "1 <mode> 48" — MODE1..4 = 3..6 (slot 11 is the mini app's
        temporary preview profile, deliberately not exposed).
        Live-verified SUCCESS on HB71.
        """
        await self.request("CONFIG_SET_AMBIENT_LIGHT", params=f"1 {int(mode_id)} 48")

    async def set_test_pattern(self, on: bool) -> None:
        """Show/hide the factory test pattern via the MCU serial link.

        CONFIG_GET_ESCVP passthrough of "AGPATTERN 01"/"00" (works without
        unlocking MCU debug mode). 01 = pattern on, 00 = screen back to
        normal. Live-verified SUCCESS on HB71.
        """
        await self.request(
            "CONFIG_GET_ESCVP", params=TEST_PATTERN_ON if on else TEST_PATTERN_OFF
        )

    async def get_color_gain(self) -> tuple[int, int, int] | None:
        """RGB colour gain triple, awake only.

        GET(params="0") answers "R G B" (e.g. "81 90 100"); the "1"/"2"
        params return the same values (not sub-indexes).
        """
        raw = await self.get_raw("CONFIG_GET_COLOR_GAIN", params="0")
        if raw is None:
            return None
        parts = raw.split()
        if len(parts) != 3:
            return None
        try:
            return int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            return None

    async def set_color_gain(self, r: int, g: int, b: int) -> None:
        """Write the RGB colour gain (params "R G B", size=3).

        Live verification: full 0–255 range accepted with exact read-back
        (0/1/50/150/200/255 loop test); the value survives picture-preference
        changes. NOTE: the menu's ColorTuner sliders are a different
        subsystem (Android Settings.Global) — this is the engine-side gain.
        """
        await self.request(
            "CONFIG_SET_COLOR_GAIN", params=f"{int(r)} {int(g)} {int(b)}"
        )

    # ------------------------------------------------------------------
    # refresh timer + manual focus
    # ------------------------------------------------------------------
    async def get_refresh_timer(self) -> int | None:
        """Refresh-mode timer in hours (1–24), awake only.

        ⚠️ GET_REFRESH is NOT a 0/1 flag: the value is the periodic-refresh
        interval in hours (the「定时器(小时)」slider, min 1 / max 24, reads
        this command; live loop 1→4→12→1 verified). Standby returns FAIL.
        """
        raw = await self.get_raw("CONFIG_GET_REFRESH")
        if raw is None:
            return None
        try:
            return int(raw.split()[0])
        except (ValueError, IndexError):
            return None

    async def set_refresh_timer(self, hours: int) -> None:
        """Set the refresh timer (params "0 <h>", size=2 tokens).

        ⚠️ Never send params "1" (size=1): that is the "start refresh NOW
        and power off" command.
        """
        await self.request("CONFIG_SET_REFRESH", params=f"0 {int(hours)}")

    async def get_focus_range(self) -> tuple[int, int] | None:
        """Focus travel range (params "RANGE") → (min, max), awake only.

        HB71 reports "0 264".
        """
        raw = await self.get_raw("CONFIG_GET_FOCUS", params="RANGE")
        if raw is None:
            return None
        parts = raw.split()
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    async def get_focus_position(self) -> int | None:
        """Current focus position (params "POS"), awake only."""
        raw = await self.get_raw("CONFIG_GET_FOCUS", params="POS")
        if raw is None:
            return None
        try:
            return int(raw.split()[0])
        except (ValueError, IndexError):
            return None

    async def focus_step(self, direction: int) -> None:
        """One focus step: params "INC" (+1 step) / "DEC" (−1 step).

        ⚠️ The step size is NOT constant (measured on HB71 2026-11-07):
        the first INC/DEC after a direction change moves ±10, every
        following step in the same direction moves only ±1. Idle time does
        not matter — only a direction reversal makes the next step coarse.
        For absolute positioning use :meth:`goto_focus` instead.

        The long-press variants "MIN"/"MAX" move continuously until "OFF"
        is sent; they are used internally by :meth:`goto_focus`.
        """
        params = "INC" if direction >= 0 else "DEC"
        await self.request("CONFIG_SET_FOCUS", params=params)

    async def focus_move_start(self, direction: int) -> None:
        """Begin continuous focus travel: "MAX" (+) / "MIN" (−).

        Must be paired with :meth:`focus_move_stop`. Verified on HB71:
        "MAX" moves ~+9 units per 0.1 s while awake (≈65 units/s), and
        "OFF" halts it within a fraction of a second.
        """
        await self.request("CONFIG_SET_FOCUS", params="MAX" if direction >= 0 else "MIN")

    async def focus_move_stop(self) -> None:
        """Stop continuous focus travel ("OFF"). Safe to send repeatedly."""
        await self.request("CONFIG_SET_FOCUS", params="OFF")

    async def goto_focus(self, target: int) -> int | None:
        """Drive the focus motor to an absolute position, closed-loop.

        The firmware has no "set position X" command, so this moves with
        the continuous MIN/MAX transport, sleeps for a duration estimated
        from the remaining distance, sends OFF, and re-reads POS. Repeats
        until the position is within ``FOCUS_TOLERANCE`` or the attempt
        budget is exhausted.

        Live verification (HB71, 2026-11-07): targets 60 / 200 / 94 landed
        exactly (error 0), 120 landed at 122 (error 2).

        Returns the final measured position, or None if POS is unreadable.
        Raises EpsonApiCommandFailed if the engine rejects the command.
        """
        tol = FOCUS_TOLERANCE
        position: int | None = None
        for _attempt in range(FOCUS_GOTO_MAX_ATTEMPTS):
            position = await self.get_focus_position()
            if position is None:
                return None
            remaining = int(target) - position
            if abs(remaining) <= tol:
                return position
            # Travel time proportional to distance, clamped so short moves
            # stay gentle and long moves stay under a couple of seconds.
            duration = abs(remaining) / FOCUS_TRAVEL_UNITS_PER_SEC
            duration = max(FOCUS_MIN_PULSE_S, min(FOCUS_MAX_PULSE_S, duration))
            await self.focus_move_start(1 if remaining > 0 else -1)
            try:
                await asyncio.sleep(duration)
            finally:
                # Always stop the motor, even if the sleep is cancelled
                # (e.g. Home Assistant shutdown) — a runaway focus motor
                # would otherwise keep moving until it hits the end stop.
                try:
                    await self.focus_move_stop()
                except EpsonApiError:
                    _LOGGER.warning("focus: OFF after move failed", exc_info=True)
            await asyncio.sleep(FOCUS_SETTLE_POLL)
        return await self.get_focus_position()

    # ------------------------------------------------------------------
    # geometry (SET_QC sub-commands), ± loop-verified on device
    # ------------------------------------------------------------------
    async def geometry_zoom(self, direction: int) -> None:
        """Digital zoom step: "1 <±104>". +1 = zoom in (corners move out).

        Live verification: "1 104" then "1 -104" returned GET_QC to the
        exact 8-token baseline (zero residue). Mode entry "4 1" is NOT
        required (verified: the step works standalone).
        """
        step = QC_ZOOM_STEP if direction >= 0 else -QC_ZOOM_STEP
        await self.request("CONFIG_SET_QC", params=f"1 {step}")

    async def geometry_shift(self, dx: int, dy: int) -> None:
        """Image shift step: "2 <dx> <dy>", +x = right, +y = down.

        One app tap = 13 units (≈1% of panel width; verified round trip
        "2 13 0" → "2 -13 0" restores the QC baseline exactly).
        """
        await self.request(
            "CONFIG_SET_QC", params=f"2 {dx * QC_SHIFT_STEP} {dy * QC_SHIFT_STEP}"
        )

    async def geometry_reset(self) -> None:
        """Reset all geometry corrections: params "5" (size=1).

        Same command as the menu item「重置校正」. Clears zoom/shift/
        quick-corner/keystone state; the projector's auto-correction
        (NOTIFY_AUTO_CORRECTION button) can rebuild the keystone afterwards.
        """
        await self.request("CONFIG_SET_QC", params="5")

    # ------------------------------------------------------------------
    # auto contrast (write-only) + GET_AUTO_FOCUS sub-commands
    # ------------------------------------------------------------------
    async def set_auto_contrast(self, on: bool) -> None:
        """Engine auto contrast on/off (params "1"/"0", size=1).

        Write path verified live (SET 0/1 both SUCCESS); the read side
        (GET_AUTO_CONTRAST) answers FAIL on HB71 even with params, so the
        entity keeps an optimistic state. NOTE: the menu's「动态对比度」is
        the separate DYNAMIC_CONTRAST select; this is the engine-side
        automatic contrast, which has no application-layer caller.
        """
        await self.request("CONFIG_SET_AUTO_CONTRAST", params="1" if on else "0")

    async def get_auto_focus_state(self) -> str | None:
        """GET_AUTO_FOCUS "0" → engine auto-focus flag/state (fast sub-command).

        With params the query answers instantly; without params it hangs.
        HB71 returns "1".
        """
        return await self.get_raw("CONFIG_GET_AUTO_FOCUS", params="0")

    async def get_auto_focus_target(self) -> int | None:
        """GET_AUTO_FOCUS "1" → auto-focus target position (numeric).

        HB71 returns e.g. "196" — inside the focus travel range 0–264.
        """
        raw = await self.get_raw("CONFIG_GET_AUTO_FOCUS", params="1")
        if raw is None:
            return None
        try:
            return int(raw.split()[0])
        except (ValueError, IndexError):
            return None
