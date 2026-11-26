"""Diagnostics support (support bundles without exposing secrets)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# 需要脱敏的键名。序列号既是设备身份也是 base_id,host 是局域网地址,
# 两者都属于"用户不该随 issue 附件公开"的信息。
TO_REDACT = {
    CONF_HOST,
    "base_id",
    "serial",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return integration diagnostics for download from the UI."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = data.get("coordinator")
    api = data.get("api")
    return {
        "entry": async_redact_data(
            {
                "version": entry.version,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "identity": async_redact_data(
            {
                "base_id": data.get("base_id"),
                "product": data.get("product"),
            },
            TO_REDACT,
        ),
        "coordinator": {
            "last_update_success": getattr(coordinator, "last_update_success", None),
            "update_interval": str(getattr(coordinator, "update_interval", None)),
            # 深度防御:目前 coordinator.data 不含 host/序列号,但一旦新增
            # 这类键,下面的脱敏会自动覆盖。
            "data": async_redact_data(
                dict(getattr(coordinator, "data", None) or {}), TO_REDACT
            ),
        },
        "api": async_redact_data(
            {
                "host": getattr(api, "_host", None),
                "port": getattr(api, "_port", None),
                "digest_nonce_cached": getattr(api, "_auth", None) is not None,
            },
            TO_REDACT,
        ),
    }
