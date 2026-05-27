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

        self.adapter: RemoteAdapter = build_adapter(
            entry.data[CONF_DEVICE_TYPE],
            dict(entry.data),
            service_caller=_call_service,
        )
        self._reconnect_task: asyncio.Task | None = None
        self._unsub_adapter: callable | None = None

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
