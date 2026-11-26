"""Extra services: send_key / raw_request."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN, KEYCODES

_LOGGER = logging.getLogger(__name__)

# 这两个服务能把任意固件命令写进投影仪,包括会重写标定状态、几何校正与
# 整机模式的命令。破坏性远超"控制一台投影仪"的常规权限,因此用
# async_register_admin_service 注册:普通用户即使被授予了实体控制权,
# 也拿不到一条绕过所有实体校验的原始命令通道。(自动化/脚本调用不带
# user_id,按 HA 的既有语义放行。)
_ADMIN_ONLY_SERVICES = ("send_key", "raw_request")


def _resolve_api(hass: HomeAssistant, entity_id: str | None):
    """Map a media_player entity (optional) back to its entry's API client."""
    store = hass.data.get(DOMAIN) or {}
    if not store:
        raise HomeAssistantError("No loaded epson_ef entry found")
    if entity_id:
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
        entry_obj = registry.async_get(entity_id)
        if entry_obj is None or not entry_obj.config_entry_id:
            raise HomeAssistantError(f"{entity_id} is not a registry entity")
        data = store.get(entry_obj.config_entry_id)
        if data:
            return data["api"]
        raise HomeAssistantError(f"{entity_id} does not belong to a loaded entry")
    # no entity given: use the first loaded entry
    for _entry_id, data in store.items():
        if isinstance(data, dict) and "api" in data:
            return data["api"]
    raise HomeAssistantError("No loaded epson_ef entry found")


@callback
def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "send_key"):
        return

    async def send_key(call: ServiceCall) -> None:
        entity_id = call.data.get("entity_id")
        api = _resolve_api(
            hass, entity_id[0] if isinstance(entity_id, list) else entity_id
        )
        key = str(call.data["key"]).upper()
        keycode = KEYCODES.get(key)
        if keycode is None and key.isdigit():
            keycode = int(key)
        if keycode is None:
            raise HomeAssistantError(f"Unknown key: {key}")
        await api.send_keyevent(keycode)

    async def raw_request(call: ServiceCall) -> None:
        entity_id = call.data.get("entity_id")
        api = _resolve_api(
            hass, entity_id[0] if isinstance(entity_id, list) else entity_id
        )
        command_id = call.data["id"]
        params = call.data.get("params")
        try:
            result = await api.request(command_id, params=params)
        except Exception as err:  # noqa: BLE001
            result = {"error": str(err)}
        hass.bus.async_fire(
            f"{DOMAIN}_response",
            {"entity_id": entity_id, "id": command_id, "response": result},
        )

    async_register_admin_service(
        hass,
        DOMAIN,
        "send_key",
        send_key,
        schema=vol.Schema(
            {
                vol.Required("key"): str,
                vol.Optional("entity_id"): str,
            }
        ),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "raw_request",
        raw_request,
        schema=vol.Schema(
            {
                vol.Required("id"): str,
                vol.Optional("params"): str,
                vol.Optional("entity_id"): str,
            }
        ),
    )


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove integration services when the last entry is unloaded."""
    for service in _ADMIN_ONLY_SERVICES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
