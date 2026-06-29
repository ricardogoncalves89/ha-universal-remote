"""The Universal Remote integration."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .adapters.base import AdapterAuthError, AdapterConnectionError
from .const import DOMAIN
from .coordinator import UniversalRemoteCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.REMOTE]

# Frontend card registration
_CARD_URL = "/universal_remote_card/universal-remote-card.js"
_CARD_FILENAME = "universal-remote-card.js"
_CARD_REGISTERED_KEY = f"{DOMAIN}_card_registered"


async def _async_register_card(hass: HomeAssistant) -> None:
    """Register the Universal Remote Card as a frontend resource.

    Done once per HA boot, regardless of how many config entries exist.
    The JS file ships inside the integration at frontend/<filename>;
    we serve it via a static HTTP path and tell the frontend to load
    it automatically. No user-side resource configuration is needed.
    """
    if hass.data.get(_CARD_REGISTERED_KEY):
        return

    integration_dir = Path(__file__).parent
    js_path = integration_dir / "frontend" / _CARD_FILENAME

    if not js_path.is_file():
        _LOGGER.warning(
            "Universal Remote Card JS not found at %s; card will not be available",
            js_path,
        )
        return

    # Use the integration version as a cache buster so the browser
    # picks up new releases without manual resource version bumps.
    manifest_path = integration_dir / "manifest.json"
    try:
        version = json.loads(manifest_path.read_text(encoding="utf-8")).get(
            "version", "0"
        )
    except (OSError, ValueError):
        version = "0"

    await hass.http.async_register_static_paths(
        [StaticPathConfig(_CARD_URL, str(js_path), False)]
    )
    add_extra_js_url(hass, f"{_CARD_URL}?v={version}")
    hass.data[_CARD_REGISTERED_KEY] = True
    _LOGGER.info(
        "Universal Remote Card registered at %s (v=%s)", _CARD_URL, version
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Universal Remote from a config entry."""
    # Register the Lovelace card resource on first entry setup.
    # Safe to call multiple times — it's gated by hass.data.
    await _async_register_card(hass)

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
