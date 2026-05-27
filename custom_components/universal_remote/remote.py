"""remote platform — exposes send_command for every canonical button.

This is the entity the RosCard and similar remote-style cards should bind to.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import RemoteEntity, RemoteEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .adapters.base import UnsupportedButtonError
from .const import CONF_DEVICE_TYPE, DEVICE_TYPE_LABELS, DOMAIN, canonicalize_button
from .coordinator import UniversalRemoteCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: UniversalRemoteCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([UniversalRemoteRemote(coordinator)])


class UniversalRemoteRemote(CoordinatorEntity[UniversalRemoteCoordinator], RemoteEntity):
    """The remote entity — send_command takes canonical button names."""

    _attr_has_entity_name = True
    _attr_name = "Remote"
    _attr_supported_features = RemoteEntityFeature(0)  # no activities yet

    def __init__(self, coordinator: UniversalRemoteCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_remote"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer=DEVICE_TYPE_LABELS.get(
                coordinator.entry.data[CONF_DEVICE_TYPE], "Unknown"
            ),
        )

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and self.coordinator.data.available

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.powered_on)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"supported_buttons": sorted(self.coordinator.adapter.SUPPORTED_BUTTONS)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.adapter.turn_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.adapter.turn_off()

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send one or more button names.

        Names are case-insensitive and aliases are accepted — see
        const.py:_ALIASES. Anything that can't be mapped raises ValueError.

            service: remote.send_command
            target: { entity_id: remote.tv_sala }
            data:   { command: ["HOME"] }            # or ["power"], ["volume_up"], ...
        """
        for raw in command:
            canonical = canonicalize_button(raw)
            if canonical is None:
                raise ValueError(
                    f"Unknown button {raw!r}. Use one of the canonical names "
                    f"(see remote attribute 'supported_buttons') or a known alias."
                )
            try:
                await self.coordinator.adapter.press_button(canonical)
            except UnsupportedButtonError as err:
                raise ValueError(str(err)) from err
