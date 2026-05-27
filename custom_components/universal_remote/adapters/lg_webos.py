"""LG WebOS adapter — uses aiowebostv directly.

Pairing flow:
  1. First connect with client_key=None — the TV shows an accept prompt.
  2. After user accepts, the client exposes a generated key in client.client_key.
  3. The integration stores that key in the config entry and reuses it on every reboot.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiowebostv import WebOsClient
from aiowebostv.exceptions import (
    WebOsTvCommandError,
    WebOsTvPairError,
    WebOsTvServiceNotFoundError,
)

from ..const import (
    CONF_CLIENT_KEY,
    CONF_HOST,
    CONF_MAC,
    Button,
)
from .base import (
    AdapterAuthError,
    AdapterConnectionError,
    DeviceState,
    RemoteAdapter,
    UnsupportedButtonError,
)

_LOGGER = logging.getLogger(__name__)


# Canonical button -> LG WebOS button code (the strings webostv.button accepts).
# Reference: https://www.home-assistant.io/integrations/webostv/#service-webostvbutton
_BUTTON_MAP: dict[str, str] = {
    Button.UP: "UP",
    Button.DOWN: "DOWN",
    Button.LEFT: "LEFT",
    Button.RIGHT: "RIGHT",
    Button.OK: "ENTER",
    Button.BACK: "BACK",
    Button.EXIT: "EXIT",
    Button.HOME: "HOME",
    Button.MENU: "MENU",
    Button.INFO: "INFO",
    Button.GUIDE: "GUIDE",
    Button.SETTINGS: "MENU",  # LG opens menu for settings
    Button.CH_UP: "CHANNELUP",
    Button.CH_DOWN: "CHANNELDOWN",
    Button.CH_LIST: "LIST",
    Button.VOL_UP: "VOLUMEUP",
    Button.VOL_DOWN: "VOLUMEDOWN",
    Button.MUTE: "MUTE",
    Button.RED: "RED",
    Button.GREEN: "GREEN",
    Button.YELLOW: "YELLOW",
    Button.BLUE: "BLUE",
    Button.NUM_0: "0",
    Button.NUM_1: "1",
    Button.NUM_2: "2",
    Button.NUM_3: "3",
    Button.NUM_4: "4",
    Button.NUM_5: "5",
    Button.NUM_6: "6",
    Button.NUM_7: "7",
    Button.NUM_8: "8",
    Button.NUM_9: "9",
    Button.INPUT: "INPUT_HUB",
}

# Media-transport buttons map to commands, not buttons, on WebOS.
_MEDIA_COMMAND_MAP: dict[str, str] = {
    Button.PLAY: "media.controls/play",
    Button.PAUSE: "media.controls/pause",
    Button.STOP: "media.controls/stop",
    Button.REWIND: "media.controls/rewind",
    Button.FAST_FORWARD: "media.controls/fastForward",
    Button.RECORD: "media.controls/Record",
}


class LGWebOSAdapter(RemoteAdapter):
    """Adapter for LG WebOS TVs over the websocket pylgtv protocol."""

    SUPPORTED_BUTTONS = set(_BUTTON_MAP.keys()) | set(_MEDIA_COMMAND_MAP.keys()) | {
        Button.POWER,
        Button.POWER_ON,
        Button.POWER_OFF,
    }

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._host: str = config[CONF_HOST]
        self._client_key: str | None = config.get(CONF_CLIENT_KEY)
        self._mac: str | None = config.get(CONF_MAC)
        self._client: WebOsClient | None = None
        self._connect_lock = asyncio.Lock()

    # ----- Lifecycle -----

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._client and self._client.is_connected():
                return

            self._client = WebOsClient(self._host, self._client_key)
            self._client.register_state_update_callback(self._on_tv_state)

            try:
                await self._client.connect()
            except WebOsTvPairError as err:
                raise AdapterAuthError(
                    "TV refused pairing — accept the prompt on the TV and retry"
                ) from err
            except (OSError, asyncio.TimeoutError, WebOsTvServiceNotFoundError) as err:
                raise AdapterConnectionError(f"Unable to reach TV: {err}") from err

            # First-pair flow generates a key — surface it so the config flow can store it.
            if self._client.client_key and self._client.client_key != self._client_key:
                self._client_key = self._client.client_key
                self._state.extra_attributes["client_key"] = self._client_key

            self._state.available = True
            self._notify()

    async def disconnect(self) -> None:
        async with self._connect_lock:
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Error during disconnect", exc_info=True)
                self._client = None
            self._state.available = False
            self._notify()

    @property
    def client_key(self) -> str | None:
        """Used by the config flow to persist the key after first pairing."""
        return self._client_key

    # ----- State callback from aiowebostv -----

    async def _on_tv_state(self, tv_state: Any) -> None:
        """Translate the aiowebostv WebOsTvState into our normalised DeviceState."""
        s = self._state
        s.available = True
        s.powered_on = bool(getattr(tv_state, "power_state", {}).get("state") != "Power Off") \
            if isinstance(getattr(tv_state, "power_state", None), dict) else None
        s.muted = getattr(tv_state, "muted", None)
        vol = getattr(tv_state, "volume", None)
        s.volume_level = (vol / 100.0) if isinstance(vol, (int, float)) and vol >= 0 else None
        s.current_app_id = getattr(tv_state, "current_app_id", None)

        inputs = getattr(tv_state, "inputs", None) or {}
        s.source_list = sorted([i.get("label", i.get("id", "")) for i in inputs.values()
                                if isinstance(i, dict)])
        current_input = getattr(tv_state, "current_input", None)
        if isinstance(current_input, dict):
            s.current_source = current_input.get("label") or current_input.get("id")

        self._notify()

    # ----- Commands -----

    async def press_button(self, button: str) -> None:
        # Power buttons short-circuit
        if button == Button.POWER:
            if self._state.powered_on:
                await self.turn_off()
            else:
                await self.turn_on()
            return
        if button == Button.POWER_ON:
            await self.turn_on()
            return
        if button == Button.POWER_OFF:
            await self.turn_off()
            return

        await self._ensure_connected()

        if button in _BUTTON_MAP:
            try:
                await self._client.button(_BUTTON_MAP[button])  # type: ignore[union-attr]
            except WebOsTvCommandError as err:
                raise AdapterConnectionError(str(err)) from err
            return

        if button in _MEDIA_COMMAND_MAP:
            try:
                await self._client.request(_MEDIA_COMMAND_MAP[button])  # type: ignore[union-attr]
            except WebOsTvCommandError as err:
                raise AdapterConnectionError(str(err)) from err
            return

        raise UnsupportedButtonError(f"LG WebOS adapter does not support button {button!r}")

    async def turn_on(self) -> None:
        # If the TV is off, the websocket is dead — only Wake-on-LAN can bring it back.
        if self._mac:
            from homeassistant.components.wake_on_lan import (  # local import: optional dep
                wake_on_lan,
            )
            # NOTE: in the real coordinator we'll call hass.services.async_call("wake_on_lan", ...)
            # instead, to avoid importing HA internals here. This is a placeholder for the standalone case.
            wake_on_lan.send_magic_packet(self._mac)
        elif self._client and self._client.is_connected():
            # Some models support turning on from standby via websocket
            try:
                await self._client.power_on()
            except Exception as err:  # noqa: BLE001
                raise AdapterConnectionError(
                    "TV is off and no MAC address configured for Wake-on-LAN"
                ) from err
        else:
            raise AdapterConnectionError(
                "TV is off and no MAC address configured for Wake-on-LAN"
            )

    async def turn_off(self) -> None:
        await self._ensure_connected()
        await self._client.power_off()  # type: ignore[union-attr]

    async def volume_up(self) -> None:
        await self._ensure_connected()
        await self._client.volume_up()  # type: ignore[union-attr]

    async def volume_down(self) -> None:
        await self._ensure_connected()
        await self._client.volume_down()  # type: ignore[union-attr]

    async def set_volume(self, level: float) -> None:
        await self._ensure_connected()
        await self._client.set_volume(int(round(level * 100)))  # type: ignore[union-attr]

    async def mute(self, muted: bool) -> None:
        await self._ensure_connected()
        await self._client.set_mute(muted)  # type: ignore[union-attr]

    async def select_source(self, source: str) -> None:
        await self._ensure_connected()
        # The label-vs-id distinction trips people up — try label first, then id.
        try:
            await self._client.launch_app_with_params(source, {})  # type: ignore[union-attr]
        except WebOsTvCommandError:
            await self._client.set_input(source)  # type: ignore[union-attr]

    async def play(self) -> None:
        await self.press_button(Button.PLAY)

    async def pause(self) -> None:
        await self.press_button(Button.PAUSE)

    async def stop(self) -> None:
        await self.press_button(Button.STOP)

    # ----- Helpers -----

    async def _ensure_connected(self) -> None:
        if not self._client or not self._client.is_connected():
            await self.connect()
