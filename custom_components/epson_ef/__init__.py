"""The Epson EF Projector (native pjctl API) integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .api import EpsonApi, EpsonApiError
from .const import (
    CONF_POLL_INTERVAL,
    CONF_PORT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
)
from .coordinator import Ef50Coordinator
from .services import async_register_services, async_unregister_services

PLATFORMS = [
    "media_player", "sensor", "select", "binary_sensor", "button", "switch",
    "number",
]
_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate v1 (ADB-based) entries to the v2 native-API format."""
    if entry.version == 1:
        data = {**entry.data}
        data[CONF_PORT] = 8080  # native API port
        data.pop("enable_camera", None)
        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.info("Migrated epson_ef entry %s to v2", entry.entry_id)
    return True


async def _async_get_base_id(
    hass: HomeAssistant, entry: ConfigEntry, api: EpsonApi, host: str, port: int
) -> tuple[str, str | None]:
    """Return a stable identity for devices/entities, cached in HA storage.

    The projector's DHCP address can change over time, so the identity is
    normally the serial number. It is persisted in HA storage (not the config
    entry, because that would trigger a reload). When the firmware does not
    expose a serial, the first host:port is pinned instead so a later
    reconfigure to a new address keeps the same identity.
    """
    store: Store[dict[str, str]] = Store(
        hass, 1, f"{DOMAIN}.{entry.entry_id}.identity"
    )
    stored = await store.async_load() or {}

    serial = stored.get("serial")
    if not serial:
        serial = await api.get_serial_number()
        if serial:
            stored["serial"] = serial
            await store.async_save(dict(stored))
            _LOGGER.debug("Discovered projector serial %s", serial)
    if serial:
        return serial, serial

    pinned = stored.get("identity")
    if pinned:
        return pinned, None

    identity = f"{host}:{port}"
    stored["identity"] = identity
    await store.async_save(dict(stored))
    _LOGGER.debug("No serial available; pinning identity to %s", identity)
    return identity, None


def _async_migrate_identity(
    hass: HomeAssistant, entry: ConfigEntry, old_id: str, new_id: str
) -> None:
    """Rename registry entries from a host:port identity to the stable one."""
    if old_id == new_id:
        return

    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        uid = entity.unique_id
        if uid == old_id:
            target = new_id
        elif uid.startswith(f"{old_id}-"):
            target = f"{new_id}{uid[len(old_id):]}"
        else:
            continue
        try:
            registry.async_update_entity(entity.entity_id, new_unique_id=target)
        except ValueError:
            _LOGGER.warning(
                "Could not migrate entity %s unique_id %s -> %s",
                entity.entity_id,
                uid,
                target,
            )

    device_registry = dr.async_get(hass)
    # Iterate the entry's own devices rather than using the deprecated
    # `async_get_device(identifiers=...)` (deprecated in HA 2026.x, removed
    # in 2027.8.0). `async_entries_for_config_entry` is a stable API and
    # needs no identifier-signature assumptions.
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if (DOMAIN, old_id) not in device.identifiers:
            continue
        try:
            device_registry.async_update_device(
                device.id, new_identifiers={(DOMAIN, new_id)}
            )
        except ValueError:
            _LOGGER.warning(
                "Could not migrate device identity %s -> %s", old_id, new_id
            )


def _async_remove_legacy_entities(
    hass: HomeAssistant, entry: ConfigEntry, base_id: str, host: str, port: int
) -> None:
    """Drop entities that were removed or replaced by newer platforms.

    Users upgrading in place keep every registry entry from the previous
    version, so anything this version no longer creates has to be deleted
    explicitly — otherwise it lingers forever as an `unavailable` orphan.

    Superseded / removed so far:
    - ``standby_mode`` was a read-only sensor, now a writable select.
    - ``serial`` was a diagnostic sensor, removed.
    - ``light_output`` was a diagnostic sensor, now a number entity.
    - ``setting-refresh`` was the refresh switch, removed when
      GET_REFRESH turned out to be an hour count; replaced by the
      ``refresh_timer`` number entity.
    - ``button-engine_refresh`` was removed: the NOTIFY command
      only marks the refresh-confirmation app's startup and does nothing
      on its own.
    """
    registry = er.async_get(hass)
    # unique_id 后缀 → 该后缀所属的平台。suffix 拼在 base_id 后面组成完整
    # unique_id;历史上还存在 host:port 形式的老 identity,两种都清。
    legacy: dict[str, tuple[str, ...]] = {
        "sensor": ("standby_mode", "serial", "light_output"),
        "switch": ("setting-refresh",),
        "button": ("button-engine_refresh",),
    }
    wanted: set[tuple[str, str]] = set()
    for platform, suffixes in legacy.items():
        for suffix in suffixes:
            wanted.add((platform, f"{base_id}-{suffix}"))
            wanted.add((platform, f"{host}:{port}-{suffix}"))

    # 遍历本条目自己的实体,而不是用 registry.async_get_entity_id(...) ——
    # 后者的签名是 (domain, platform, unique_id),本函数历史上把前两个参数
    # 传反了(传成 platform, DOMAIN),导致清理静默失效、孤儿实体一直残留。
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (entity.domain, entity.unique_id) in wanted:
            registry.async_remove(entity.entity_id)
            _LOGGER.info("Removed legacy entity %s (superseded)", entity.entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    host = entry.data["host"]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    api = EpsonApi(host, port)

    try:
        product = await api.get_product_name()
    except EpsonApiError as err:
        await api.close()
        raise ConfigEntryNotReady(f"Cannot reach {host}:{port}: {err}") from err

    base_id, serial = await _async_get_base_id(hass, entry, api, host, port)
    legacy_id = f"{host}:{port}"
    _async_migrate_identity(hass, entry, legacy_id, base_id)
    _async_remove_legacy_entities(hass, entry, base_id, host, port)

    # Options take precedence over the value captured when the entry was added.
    poll_interval = entry.options.get(
        CONF_POLL_INTERVAL,
        entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    )
    coordinator = Ef50Coordinator(hass, api, poll_interval)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await api.close()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "product": product,
        "base_id": base_id,
        "serial": serial,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["api"].close()
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
            async_unregister_services(hass)
    return unload_ok
