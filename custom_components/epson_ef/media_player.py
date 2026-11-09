"""Media player entity controlling the projector via the native pjctl API."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EpsonApiCommandFailed, EpsonApiError, EpsonApiUnavailable
from .const import DEFAULT_NAME, DOMAIN
from .coordinator import Ef50Coordinator

_LOGGER = logging.getLogger(__name__)

_POWER_CONFIRM_TIMEOUT = 10.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [Ef50MediaPlayer(entry, data["coordinator"], data["base_id"])]
    )


class Ef50MediaPlayer(
    CoordinatorEntity[Ef50Coordinator], MediaPlayerEntity, RestoreEntity
):
    """Epson EF projector media player."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:projector"
    # No mute-state query on the device: assume unmuted until we toggle it
    # (RestoreEntity keeps the last known value across restarts).
    _attr_is_volume_muted: bool | None = False
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.NEXT_TRACK
    )

    def __init__(
        self, entry: ConfigEntry, coordinator: Ef50Coordinator, base_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._base_id = base_id
        self._attr_unique_id = base_id
        self._attr_name = entry.data.get("name", DEFAULT_NAME)
        # Serialise power actions: KEYCODE_POWER is a blind toggle, so two
        # concurrent turn_on/turn_off calls would flip the projector twice and
        # leave HA out of sync with the device.
        self._power_lock = asyncio.Lock()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            muted = last_state.attributes.get("is_volume_muted")
            if isinstance(muted, bool):
                self._attr_is_volume_muted = muted

    # ------------------------------------------------------------------
    @property
    def device_info(self) -> DeviceInfo:
        c = self.coordinator
        model = (c.data or {}).get("product") or "Epson EF Projector"
        return DeviceInfo(
            identifiers={(DOMAIN, self._base_id)},
            name=self._attr_name,
            manufacturer="EPSON",
            model=model,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def state(self) -> MediaPlayerState | None:
        state = (self.coordinator.data or {}).get("state")
        if state == "on":
            return MediaPlayerState.ON
        if state == "off":
            return MediaPlayerState.OFF
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        attrs: dict[str, Any] = {}
        if data.get("standby_mode") is not None:
            attrs["standby_mode"] = data["standby_mode"]
        if data.get("light_hours") is not None:
            attrs["light_hours"] = data["light_hours"]
            measured = data.get("light_measured_at")
            if measured is not None:
                attrs["light_measured_at"] = (
                    measured.isoformat() if hasattr(measured, "isoformat") else measured
                )
        diag = data.get("diag") or {}
        for key in ("vmute", "color_temp", "dynamic_contrast",
                    "focus_range", "movement_sensor", "smart_eye_protection",
                    "startup_correction", "qc", "inertial",
                    "color_gain", "pj_ver",
                    "auto_focus_state", "auto_focus_target",
                    "sales_area", "usb_typec", "lcmd_ver", "camera_pattern"):
            if diag.get(key) is not None:
                attrs[key] = diag[key]
        # 可写设置的当前值(与对应实体同源)
        settings = data.get("settings") or {}
        for key in ("light_output", "picture_preference", "refresh_timer",
                    "focus_position"):
            if settings.get(key) is not None:
                attrs[key] = settings[key]
        return attrs

    # ------------------------------------------------------------------
    async def _key(self, keycode: int) -> None:
        try:
            await self.coordinator.api.send_keyevent(keycode)
        except EpsonApiUnavailable as err:
            raise HomeAssistantError(f"投影仪不可达: {err}") from err
        except EpsonApiCommandFailed as err:
            raise HomeAssistantError(
                "投影仪未执行该按键(可能处于待机或正在切换电源状态)"
            ) from err
        except EpsonApiError as err:
            raise HomeAssistantError(f"投影仪返回错误: {err}") from err

    async def _probe(self) -> str:
        """Fresh power-state probe; raises if the projector is unreachable."""
        try:
            state, _ = await self.coordinator.api.probe_state()
        except EpsonApiError as err:
            raise HomeAssistantError(f"投影仪不可达: {err}") from err
        return state

    def _assume(self, state: str) -> None:
        """Optimistically update the coordinator cache so the UI reacts now."""
        if self.coordinator.data is not None:
            self.coordinator.data["state"] = state
            self.async_write_ha_state()

    async def _wait_and_reconcile(self, target: str) -> None:
        """Confirm a power transition, falling back to a coordinator refresh."""
        confirmed = await self.coordinator.api.wait_for_state(
            target, timeout_s=_POWER_CONFIRM_TIMEOUT
        )
        if not confirmed:
            _LOGGER.warning(
                "Projector did not confirm power-%s within %ss; refreshing state",
                target,
                _POWER_CONFIRM_TIMEOUT,
            )
            await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        # KEYCODE_POWER is a blind toggle — only send it when verifiably off
        async with self._power_lock:
            state = await self._probe()
            if state == "on":
                return
            await self._key(26)
            self._assume("on")
            # wait until the projector confirms it is awake, so a turn_off sent
            # right after this cannot race the wake-up transition
            await self._wait_and_reconcile("on")

    async def async_turn_off(self) -> None:
        # KEYCODE_POWER is a blind toggle — only send it when verifiably on
        async with self._power_lock:
            state = await self._probe()
            if state == "off":
                return
            await self._key(26)
            self._assume("off")
            # confirm the projector actually went to standby
            await self._wait_and_reconcile("off")

    async def async_volume_up(self) -> None:
        await self._key(24)

    async def async_volume_down(self) -> None:
        await self._key(25)

    async def async_mute_volume(self, mute: bool) -> None:
        # The firmware only exposes a mute *toggle*, so keep an optimistic
        # local state (restored across restarts) and only send the key when the
        # requested state differs. It can still drift if the physical remote is
        # used, since the device has no mute-state query.
        if self._attr_is_volume_muted == mute:
            return
        await self._key(164)
        self._attr_is_volume_muted = mute
        self.async_write_ha_state()

    async def async_media_play(self) -> None:
        await self._key(126)

    async def async_media_pause(self) -> None:
        await self._key(127)

    async def async_media_previous_track(self) -> None:
        await self._key(88)

    async def async_media_next_track(self) -> None:
        await self._key(87)
