"""Extra services: send_key / raw_request."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, KEYCODES

_LOGGER = logging.getLogger(__name__)


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

    hass.services.async_register(
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
    hass.services.async_register(
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
    for service in ("send_key", "raw_request"):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
