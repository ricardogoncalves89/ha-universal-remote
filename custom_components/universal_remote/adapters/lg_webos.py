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

    def __init__(
        self,
        config: dict[str, Any],
        service_caller=None,
    ) -> None:
        super().__init__(config, service_caller)
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
            # register_state_update_callback is async in aiowebostv 0.7+ — must be awaited.
            await self._client.register_state_update_callback(self._on_tv_state)

            try:
                await self._client.connect()
            except WebOsTvPairError as err:
                raise AdapterAuthError(
                    "TV refused pairing — accept the prompt on the TV and retry"
                ) from err
            except Exception as err:  # noqa: BLE001
                # aiowebostv 0.7.x can raise a variety of exceptions during connect:
                # OSError (network), asyncio.TimeoutError, WebOsTvServiceNotFoundError,
                # aiohttp.WSMessageTypeError (TV in standby refusing handshake with code 1008),
                # and others. Catch broadly so we can always degrade to ConfigEntryNotReady
                # and let HA retry instead of failing setup permanently.
                raise AdapterConnectionError(
                    f"Unable to connect to TV: {type(err).__name__}: {err}"
                ) from err

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
        """Translate the aiowebostv WebOsTvState into our normalised DeviceState.

        We infer powered_on from multiple signals because the LG webOS API
        is famously inconsistent across models:
          - power_state.state == "Power Off" → off
          - power_state.state in ("Active", "Active Standby") → on
          - current_app_id present → on (app running implies powered)
          - otherwise unknown → leave as last value
        """
        s = self._state
        s.available = True

        # Infer power state
        power_state = getattr(tv_state, "power_state", None)
        if isinstance(power_state, dict):
            state_str = power_state.get("state")
            if state_str == "Power Off":
                s.powered_on = False
            elif state_str in ("Active", "Active Standby", "Screen Off"):
                # "Screen Off" still counts as on for our purposes — TV is responsive.
                s.powered_on = True
            # else: leave whatever we had

        app_id = getattr(tv_state, "current_app_id", None)
        if app_id:
            s.powered_on = True
        s.current_app_id = app_id

        s.muted = getattr(tv_state, "muted", None)
        vol = getattr(tv_state, "volume", None)
        s.volume_level = (vol / 100.0) if isinstance(vol, (int, float)) and vol >= 0 else None

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
            # The single source of truth is the websocket: if we have a live
            # connection, the TV is reachable (=on); if not, it's off or
            # unreachable (=needs WoL). We DON'T trust self._state.powered_on
            # alone because the state callback can lag the actual TV state by
            # tens of seconds in some scenarios (e.g. TV woken via remote).
            #
            # Try a probe connection first to handle the "user turned TV on
            # via physical remote" case. If we can connect, the TV is on,
            # so toggle means turn off.
            try:
                await self._ensure_connected()
                has_live_connection = (
                    self._client is not None and self._client.is_connected()
                )
            except AdapterConnectionError:
                has_live_connection = False

            _LOGGER.debug(
                "POWER toggle: probe result has_live_connection=%s -> %s",
                has_live_connection,
                "turn_off" if has_live_connection else "turn_on",
            )
            if has_live_connection:
                await self.turn_off()
            else:
                await self.turn_on()
            return
        if button == Button.POWER_ON:
            _LOGGER.debug("POWER_ON pressed")
            await self.turn_on()
            return
        if button == Button.POWER_OFF:
            _LOGGER.debug("POWER_OFF pressed")
            await self.turn_off()
            return

        if button in _BUTTON_MAP:
            code = _BUTTON_MAP[button]
            await self._safe_send(lambda c: c.button(code))
            return

        if button in _MEDIA_COMMAND_MAP:
            cmd = _MEDIA_COMMAND_MAP[button]
            await self._safe_send(lambda c: c.request(cmd))
            return

        raise UnsupportedButtonError(f"LG WebOS adapter does not support button {button!r}")

    async def turn_on(self) -> None:
        """Turn the TV on.

        Strategy:
        1. If we have a MAC, send Wake-on-LAN magic packet via HA's wake_on_lan
           service. Many LG OLEDs need 2 packets ~250ms apart — first wakes the
           NIC, second is the one that actually triggers boot.
        2. Otherwise, if the websocket is still alive (TV in standby), try the
           webOS power_on command — only newer models with "LG Connect Apps"
           enabled support this.
        3. Otherwise, give a clear error.
        """
        _LOGGER.debug("turn_on: mac=%s client_connected=%s",
                      bool(self._mac),
                      self._client.is_connected() if self._client else False)

        if self._mac:
            if self._service_caller is None:
                raise AdapterConnectionError(
                    "No service caller available — adapter was built standalone"
                )
            try:
                _LOGGER.debug("Sending WoL magic packet #1 to %s", self._mac)
                await self._service_caller(
                    "wake_on_lan",
                    "send_magic_packet",
                    {"mac": self._mac},
                )
                await asyncio.sleep(0.25)
                _LOGGER.debug("Sending WoL magic packet #2 to %s", self._mac)
                await self._service_caller(
                    "wake_on_lan",
                    "send_magic_packet",
                    {"mac": self._mac},
                )
                _LOGGER.info("WoL packets sent to %s", self._mac)
                # Optimistically mark TV as on so the next POWER toggle has a
                # chance to take effect. The next state callback will correct
                # this if we were wrong.
                self._state.powered_on = True
                self._notify()
                return
            except Exception as err:  # noqa: BLE001
                raise AdapterConnectionError(
                    f"Wake-on-LAN call failed: {err}"
                ) from err

        if self._client and self._client.is_connected():
            try:
                _LOGGER.debug("Sending power_on via websocket")
                await self._client.power_on()
                self._state.powered_on = True
                self._notify()
                return
            except Exception as err:  # noqa: BLE001
                raise AdapterConnectionError(
                    "TV is off and no MAC address configured for Wake-on-LAN"
                ) from err

        raise AdapterConnectionError(
            "TV is off and no MAC address configured for Wake-on-LAN"
        )

    async def turn_off(self) -> None:
        """Tell the TV to power off.

        Unlike other commands, power_off is a *terminal* operation — the TV
        will close the websocket as it shuts down. Any exception raised while
        the connection drops is expected, NOT an error. We do NOT use
        _safe_send here because retry would either fail (TV now off) or send
        the command twice (unnecessary).
        """
        _LOGGER.debug("turn_off: ensuring connection then sending power_off")
        try:
            await self._ensure_connected()
        except AdapterConnectionError as err:
            # TV already unreachable — assume already off.
            _LOGGER.debug("turn_off: TV unreachable, treating as already off: %s", err)
            self._state.powered_on = False
            self._state.available = False
            self._notify()
            return

        try:
            await self._client.power_off()  # type: ignore[union-attr]
            _LOGGER.debug("turn_off: power_off sent successfully")
        except Exception as err:  # noqa: BLE001
            # Expected when the TV closes the socket mid-send. The TV WILL turn
            # off — the command got through before the socket died.
            _LOGGER.debug(
                "turn_off: exception after power_off (expected during shutdown): %s: %s",
                type(err).__name__, err,
            )

        # Optimistically mark TV as off and tear down the dead socket so the
        # next command starts clean.
        self._state.powered_on = False
        self._state.available = False
        self._notify()
        try:
            if self._client:
                await self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self._client = None

    async def volume_up(self) -> None:
        await self._safe_send(lambda c: c.volume_up())

    async def volume_down(self) -> None:
        await self._safe_send(lambda c: c.volume_down())

    async def set_volume(self, level: float) -> None:
        await self._safe_send(lambda c: c.set_volume(int(round(level * 100))))

    async def mute(self, muted: bool) -> None:
        await self._safe_send(lambda c: c.set_mute(muted))

    async def select_source(self, source: str) -> None:
        # The label-vs-id distinction trips people up — try label first, then id.
        async def _run(c: WebOsClient) -> None:
            try:
                await c.launch_app_with_params(source, {})
            except WebOsTvCommandError:
                await c.set_input(source)
        await self._safe_send(_run)

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

    async def _safe_send(self, action) -> None:
        """Run a websocket action, retrying once if the connection is stale.

        A stale connection is a websocket that is_connected() reports True for
        but actually closed silently — common after the TV is idle for a while.
        The first attempt fails, we force a reconnect, and try once more.
        After two failures we surface the error.
        """
        await self._ensure_connected()
        try:
            await action(self._client)
            return
        except (
            WebOsTvCommandError,
            ConnectionError,
            asyncio.TimeoutError,
            OSError,
        ) as first_err:
            _LOGGER.debug("First attempt failed (stale connection?), retrying: %s", first_err)
            # Force reconnect and retry once.
            try:
                if self._client:
                    await self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
            self._state.available = False
            self._notify()

            try:
                await self.connect()
                await action(self._client)
            except Exception as retry_err:  # noqa: BLE001
                raise AdapterConnectionError(
                    f"Command failed after retry: {retry_err}"
                ) from retry_err
