"""The Universal Remote integration."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .adapters.base import AdapterAuthError, AdapterConnectionError
from .const import DOMAIN
from .coordinator import UniversalRemoteCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.REMOTE]

# Frontend card registration
_CARD_URL = "/universal_remote_card/universal-remote-card.js"
_CARD_FILENAME = "universal-remote-card.js"
_CARD_REGISTERED_KEY = f"{DOMAIN}_card_registered"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Universal Remote integration.

    HA calls this exactly once at boot per integration (independent of
    how many config entries the user has), which is the correct place
    to register the Lovelace card frontend resource. Using
    ``async_setup_entry`` for this caused two issues with multiple
    entries:

    * a race condition where parallel entry setups all passed the
      "already registered" guard and double-registered the route,
      raising ``RuntimeError: Added route will never be executed``;
    * the per-entry guard flag check happening between awaits, which
      isn't atomic across cooperatively-scheduled tasks.
    """
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Register the Universal Remote Card as a frontend resource."""
    # Defence in depth: even though async_setup runs once, claim the
    # slot synchronously before any await so any caller race is safe.
    if hass.data.get(_CARD_REGISTERED_KEY):
        return
    hass.data[_CARD_REGISTERED_KEY] = True

    integration_dir = Path(__file__).parent
    js_path = integration_dir / "frontend" / _CARD_FILENAME

    # Path.is_file() does a stat() syscall — keep it off the loop.
    if not await hass.async_add_executor_job(js_path.is_file):
        _LOGGER.warning(
            "Universal Remote Card JS not found at %s; card will not be available",
            js_path,
        )
        return

    # Use HA's already-parsed integration metadata for the version
    # instead of reading manifest.json again from the event loop.
    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version) if integration.version else "0"

    await hass.http.async_register_static_paths(
        [StaticPathConfig(_CARD_URL, str(js_path), False)]
    )
    add_extra_js_url(hass, f"{_CARD_URL}?v={version}")
    _LOGGER.info(
        "Universal Remote Card registered at %s (v=%s)", _CARD_URL, version
    )


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
