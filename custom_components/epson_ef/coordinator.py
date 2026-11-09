"""Data update coordinator: fast power-state probe + slow diagnostics."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EpsonApi, EpsonApiCommandFailed, EpsonApiUnavailable
from .const import DIAGNOSTIC_EVERY_N_POLLS, WRITABLE_SELECTS, WRITABLE_SWITCHES

_LOGGER = logging.getLogger(__name__)


class Ef50Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the projector over the native HTTP API."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: EpsonApi,
        poll_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="epson_ef",
            update_interval=timedelta(seconds=poll_interval),
        )
        self.api = api
        self._poll_count = 0

    async def _async_update_data(self) -> dict[str, Any]:
        self._poll_count += 1
        data: dict[str, Any] = {}

        # fast probe: power state (+ lamp hours when awake)
        try:
            state, light = await self.api.probe_state()
        except EpsonApiUnavailable as err:
            raise UpdateFailed(f"projector unreachable: {err}") from err
        except EpsonApiCommandFailed as err:
            # endpoint reachable but the probe itself failed — treat as off
            _LOGGER.debug("probe failed (%s), assuming standby", err)
            state, light = "off", None

        data["state"] = state
        if light:
            data["light_hours"], data["light_extra"] = light
            # Stored as an ISO string: HA state attributes must stay JSON
            # serialisable, and sensors re-expose this to show data freshness.
            data["light_measured_at"] = dt_util.now(timezone.utc).isoformat()

        # slow diagnostics
        if self._poll_count == 1 or self._poll_count % DIAGNOSTIC_EVERY_N_POLLS == 0:
            diag: dict[str, str] = {}
            if state == "on":
                # 带参数的读取:params="0" 是多数命令的"读当前值"子索引
                probes: list[tuple[str, str, str, str | None]] = [
                    ("distance_mm", "CONFIG_GET_MEASURE_DISTANCE", None),
                    ("vmute", "CONFIG_GET_VMUTE", None),
                    ("color_temp", "CONFIG_GET_COLOR_TEMP", None),
                    ("dynamic_contrast", "CONFIG_GET_DYNAMIC_CONTRAST", None),
                    ("movement_sensor", "CONFIG_GET_MOVEMENT_SENSOR", None),
                    ("smart_eye_protection", "CONFIG_GET_SMART_EYE_PROTECTION", None),
                    ("startup_correction", "CONFIG_GET_STARTUP_CORRECTION", None),
                    ("qc", "CONFIG_GET_QC", None),
                    ("inertial", "CONFIG_GET_INERTIALSENSOR", None),
                    ("sales_area", "CONFIG_GET_SALES_AREA", None),
                    ("usb_typec", "CONFIG_GET_USB_TYPEC", None),
                    ("lcmd_ver", "CONFIG_GET_LCMD_VER", "0"),
                    ("camera_pattern", "CONFIG_GET_GENERATE_CAMERA_PATTERN", None),
                    ("color_gain", "CONFIG_GET_COLOR_GAIN", "0"),
                    # GET_AUTO_FOCUS 参数化子命令实测快速返回
                    # (无参数查询会挂,带参不挂)
                    ("auto_focus_state", "CONFIG_GET_AUTO_FOCUS", "0"),
                    ("auto_focus_target", "CONFIG_GET_AUTO_FOCUS", "1"),
                    ("pj_ver", "CONFIG_GET_PJ_VER", "0"),
                ]
                for key, cmd, params in probes:
                    raw = await self.api.get_raw(cmd, params=params)
                    if raw is not None:
                        diag[key] = raw
                        await asyncio.sleep(0.2)
                # 可写设置当前值(switch/select/number 实体数据源)。
                # switch 定义没有 params;light_output 需 params="0" 子索引。
                settings: dict[str, str] = {}
                for suffix, _key, get_cmd, _set in WRITABLE_SWITCHES:
                    raw = await self.api.get_raw(get_cmd)
                    if raw is not None:
                        settings[suffix] = raw
                        await asyncio.sleep(0.2)
                for suffix, _key, get_cmd, _set, _options, _labels in WRITABLE_SELECTS:
                    raw = await self.api.get_raw(get_cmd)
                    if raw is not None:
                        settings[suffix] = raw
                        await asyncio.sleep(0.2)
                raw = await self.api.get_raw("CONFIG_GET_LIGHT_OUTPUT", params="0")
                if raw is not None:
                    settings["light_output"] = raw
                # 刷新定时器(GET_REFRESH=小时数,非 0/1)
                timer = await self.api.get_refresh_timer()
                if timer is not None:
                    settings["refresh_timer"] = str(timer)
                # 手动对焦(POS/RANGE 子命令)
                focus_range = await self.api.get_focus_range()
                if focus_range is not None:
                    diag["focus_range"] = f"{focus_range[0]} {focus_range[1]}"
                focus_pos = await self.api.get_focus_position()
                if focus_pos is not None:
                    settings["focus_position"] = str(focus_pos)
                if settings:
                    merged = dict(self.data.get("settings", {})) if self.data else {}
                    merged.update(settings)
                    data["settings"] = merged
                if state == "on":
                    product = await self.api.get_product_name()
                    if product is not None:
                        data["product"] = product
            if diag:
                merged = dict(self.data.get("diag", {})) if self.data else {}
                merged.update(diag)
                data["diag"] = merged
            # A None here means "query failed in this power state" — do not
            # overwrite the last known value with it (see carry-forward below).
            standby_mode = await self.api.get_standby_mode()
            if standby_mode is not None:
                data["standby_mode"] = standby_mode

        # carry forward last known diagnostics when not refreshed this cycle
        if "product" not in data and self.data:
            data["product"] = self.data.get("product")
        if "standby_mode" not in data and self.data:
            data["standby_mode"] = self.data.get("standby_mode")
        if "light_hours" not in data and self.data:
            # keep last lamp reading visible while off, together with the
            # timestamp that says when it was actually measured
            data["light_hours"] = self.data.get("light_hours")
            data["light_extra"] = self.data.get("light_extra")
            data["light_measured_at"] = self.data.get("light_measured_at")
        if "diag" not in data and self.data:
            # carry forward the slow diagnostic bundle between fast polls
            previous = self.data.get("diag")
            if previous is not None:
                data["diag"] = previous
        if "settings" not in data and self.data:
            previous = self.data.get("settings")
            if previous is not None:
                data["settings"] = previous
        return data
