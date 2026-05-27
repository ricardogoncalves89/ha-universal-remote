"""media_player platform — exposes the standard playback / volume / source interface."""
from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_TYPE, CONF_HOST, DEVICE_TYPE_LABELS, DOMAIN
from .coordinator import UniversalRemoteCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: UniversalRemoteCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([UniversalRemoteMediaPlayer(coordinator)])


class UniversalRemoteMediaPlayer(CoordinatorEntity[UniversalRemoteCoordinator], MediaPlayerEntity):
    """A device-agnostic media_player backed by an adapter."""

    _attr_has_entity_name = True
    _attr_name = None  # use device name

    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
    )

    def __init__(self, coordinator: UniversalRemoteCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_media_player"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer=DEVICE_TYPE_LABELS.get(
                coordinator.entry.data[CONF_DEVICE_TYPE], "Unknown"
            ),
            configuration_url=f"http://{coordinator.entry.data.get(CONF_HOST, '')}",
        )

    # ----- State projection -----

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and self.coordinator.data.available

    @property
    def state(self) -> MediaPlayerState | None:
        s = self.coordinator.data
        if s is None or not s.available:
            return MediaPlayerState.OFF
        if s.powered_on is False:
            return MediaPlayerState.OFF
        if s.powered_on is True:
            return MediaPlayerState.ON
        return None

    @property
    def volume_level(self) -> float | None:
        return self.coordinator.data.volume_level if self.coordinator.data else None

    @property
    def is_volume_muted(self) -> bool | None:
        return self.coordinator.data.muted if self.coordinator.data else None

    @property
    def source(self) -> str | None:
        return self.coordinator.data.current_source if self.coordinator.data else None

    @property
    def source_list(self) -> list[str] | None:
        return self.coordinator.data.source_list if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        return {
            "current_app_id": self.coordinator.data.current_app_id,
            **self.coordinator.data.extra_attributes,
        }

    # ----- Commands -----

    async def async_turn_on(self) -> None:
        await self.coordinator.adapter.turn_on()

    async def async_turn_off(self) -> None:
        await self.coordinator.adapter.turn_off()

    async def async_volume_up(self) -> None:
        await self.coordinator.adapter.volume_up()

    async def async_volume_down(self) -> None:
        await self.coordinator.adapter.volume_down()

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.adapter.set_volume(volume)

    async def async_mute_volume(self, mute: bool) -> None:
        await self.coordinator.adapter.mute(mute)

    async def async_select_source(self, source: str) -> None:
        await self.coordinator.adapter.select_source(source)

    async def async_media_play(self) -> None:
        await self.coordinator.adapter.play()

    async def async_media_pause(self) -> None:
        await self.coordinator.adapter.pause()

    async def async_media_stop(self) -> None:
        await self.coordinator.adapter.stop()
