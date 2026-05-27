"""The Universal Remote integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .adapters.base import AdapterAuthError, AdapterConnectionError
from .const import DOMAIN
from .coordinator import UniversalRemoteCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.REMOTE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Universal Remote from a config entry."""
    coordinator = UniversalRemoteCoordinator(hass, entry)
    try:
        await coordinator.async_setup()
    except AdapterAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except AdapterConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err
    except Exception as err:  # noqa: BLE001
        # Defensive: any unexpected exception during setup is treated as transient.
        # HA will retry the entry instead of marking it permanently failed.
        _LOGGER.exception("Unexpected error setting up %s", entry.title)
        raise ConfigEntryNotReady(
            f"Unexpected error: {type(err).__name__}: {err}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: UniversalRemoteCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unloaded
