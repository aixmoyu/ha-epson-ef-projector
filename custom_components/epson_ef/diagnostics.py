"""Diagnostics support (support bundles without exposing secrets)."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return integration diagnostics for download from the UI."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = data.get("coordinator")
    api = data.get("api")
    return {
        "entry": {
            "version": entry.version,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "identity": {
            "base_id": data.get("base_id"),
            "product": data.get("product"),
        },
        "coordinator": {
            "last_update_success": getattr(coordinator, "last_update_success", None),
            "update_interval": str(getattr(coordinator, "update_interval", None)),
            "data": dict(getattr(coordinator, "data", None) or {}),
        },
        "api": {
            "host": getattr(api, "_host", None),
            "port": getattr(api, "_port", None),
            "digest_nonce_cached": getattr(api, "_auth", None) is not None,
        },
    }
