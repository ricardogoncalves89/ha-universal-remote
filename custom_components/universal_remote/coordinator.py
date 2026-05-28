"""Coordinator — owns the adapter lifecycle and reconnect logic for one device."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .adapters import build_adapter
from .adapters.base import (
    AdapterAuthError,
    AdapterConnectionError,
    DeviceState,
    RemoteAdapter,
)
from .const import CONF_DEVICE_TYPE, DOMAIN, RECONNECT_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)

# Key names that, when changed in entry.options, should NOT trigger an
# entry reload. These are written by the integration itself (e.g. caching
# the known sources discovered from the device) rather than by the user.
# Reloading on these would create an infinite update loop.
_INTERNAL_OPTION_KEYS = frozenset({"known_sources"})


async def _handle_options_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when the user changes options.

    Triggered by entry.add_update_listener. We compare against a snapshot
    of the previously-seen user-facing options to avoid reloading on
    internal writes (e.g. when the adapter persists its known source list).
    """
    user_facing = {k: v for k, v in entry.options.items()
                   if k not in _INTERNAL_OPTION_KEYS}
    runtime = hass.data.setdefault("_universal_remote_runtime", {})
    last_seen = runtime.get(entry.entry_id)
    if last_seen == user_facing:
        _LOGGER.debug("Options changed but only internal keys — skipping reload")
        return
    runtime[entry.entry_id] = user_facing
    await hass.config_entries.async_reload(entry.entry_id)


class UniversalRemoteCoordinator(DataUpdateCoordinator[DeviceState]):
    """One coordinator per configured device.

    The adapter pushes state via callbacks; the coordinator's update method
    is mostly a connection watchdog that reconnects on failure.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=None,  # we don't poll; we push via adapter callbacks
        )
        self.entry = entry

        async def _call_service(domain: str, service: str, data: dict) -> None:
            """Thin wrapper passed to the adapter so it can invoke HA services
            (wake_on_lan, etc.) without importing HA internals.
            """
            await hass.services.async_call(domain, service, data, blocking=True)

        # Merge entry.data with entry.options so the adapter sees both.
        # data holds connection details (host, mac, client_key, device_type).
        # options holds user-editable preferences (allowed_sources, etc.).
        adapter_config = {**entry.data, **entry.options}

        self.adapter: RemoteAdapter = build_adapter(
            entry.data[CONF_DEVICE_TYPE],
            adapter_config,
            service_caller=_call_service,
        )

        # Give the adapter a way to persist its discovered sources back into
        # the config entry. Whenever the TV reports a new set of inputs+apps,
        # we save them so the options flow can show the full list even
        # immediately after a reload (before the state callback fires again).
        def _persist_sources(sources: list[str]) -> None:
            current = entry.options.get("known_sources", [])
            if list(current) == list(sources):
                return  # no change, avoid writing
            new_options = {**entry.options, "known_sources": sources}
            hass.config_entries.async_update_entry(entry, options=new_options)

        # Set the attribute directly — only some adapters support this.
        if hasattr(self.adapter, "on_source_map_changed"):
            self.adapter.on_source_map_changed = _persist_sources

        self._reconnect_task: asyncio.Task | None = None
        self._unsub_adapter: callable | None = None

        # Seed the runtime snapshot for the options-change diff so the first
        # legitimate user change is correctly detected as different from the
        # initial state.
        runtime = hass.data.setdefault("_universal_remote_runtime", {})
        runtime[entry.entry_id] = {
            k: v for k, v in entry.options.items()
            if k not in _INTERNAL_OPTION_KEYS
        }

        # Re-run setup when the user changes options (e.g. filtered sources).
        self._unsub_options_listener = entry.add_update_listener(_handle_options_update)

    async def async_setup(self) -> None:
        """Initial connect + register push listener.

        Never propagates connection errors — the integration arrives in HA
        with available=False and a background reconnect loop takes over.
        Only AdapterAuthError propagates, since that requires user intervention
        (re-pairing).
        """
        self._unsub_adapter = self.adapter.add_listener(self._on_adapter_state)
        try:
            await self.adapter.connect()
        except AdapterAuthError:
            # Auth error means stored client_key is stale — surface to user.
            raise
        except AdapterConnectionError as err:
            _LOGGER.warning(
                "Initial connection to %s failed (%s); will keep retrying in background",
                self.entry.title,
                err,
            )
            self._schedule_reconnect()
        except Exception as err:  # noqa: BLE001
            # Anything else (a library raising something we don't model) should
            # still degrade gracefully — don't break HA setup.
            _LOGGER.exception(
                "Unexpected error connecting to %s; will keep retrying", self.entry.title
            )
            self._schedule_reconnect()

        # Push the initial state into HA (likely with available=False).
        self.async_set_updated_data(self.adapter.state)

    async def async_shutdown(self) -> None:
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._unsub_adapter:
            self._unsub_adapter()
        if self._unsub_options_listener:
            self._unsub_options_listener()
        await self.adapter.disconnect()

    @callback
    def _on_adapter_state(self, state: DeviceState) -> None:
        """Adapter pushed new state — propagate to HA entities."""
        self.async_set_updated_data(state)

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = self.hass.async_create_background_task(
            self._reconnect_loop(), name=f"{DOMAIN}_reconnect_{self.entry.entry_id}"
        )

    async def _reconnect_loop(self) -> None:
        while True:
            await asyncio.sleep(RECONNECT_INTERVAL_SECONDS)
            try:
                await self.adapter.connect()
                _LOGGER.info("Reconnected to %s", self.entry.title)
                return
            except AdapterConnectionError:
                continue
            except AdapterAuthError:
                _LOGGER.error(
                    "Auth error reconnecting to %s — re-add the device",
                    self.entry.title,
                )
                return
