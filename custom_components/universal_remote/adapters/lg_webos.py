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
        # Internal map: source label -> (kind, identifier).
        # kind is "app" or "input"; identifier is the webOS-internal ID.
        # Built on every state update by _on_tv_state.
        self._source_map: dict[str, tuple[str, str]] = {}

    # ----- Lifecycle -----

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._client and self._is_connected_safe():
                return

            self._client = WebOsClient(self._host, self._client_key)
            # register_state_update_callback is async in aiowebostv 0.7+ — must be awaited.
            await self._client.register_state_update_callback(self._on_tv_state)

            try:
                await self._client.connect()
            except WebOsTvPairError as err:
                self._client = None
                raise AdapterAuthError(
                    "TV refused pairing — accept the prompt on the TV and retry"
                ) from err
            except Exception as err:  # noqa: BLE001
                # aiowebostv 0.7.x can raise a variety of exceptions during connect:
                # OSError (network), asyncio.TimeoutError, WebOsTvServiceNotFoundError,
                # aiohttp.WSMessageTypeError (TV in standby refusing handshake with code 1008),
                # and others. Catch broadly so we can always degrade to ConfigEntryNotReady
                # and let HA retry instead of failing setup permanently.
                # Clear the client so we don't leave a corrupt instance around — calling
                # is_connected() on it later can re-raise the same exception.
                self._client = None
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
        is famously inconsistent across models.

        For sources, we combine:
          - tv_state.inputs   → HDMI ports, Live TV, etc.
          - tv_state.apps     → Netflix, Disney+, YouTube, etc.
        Both must be selectable from the media_player UI, so we merge them
        into a single source_list while keeping an internal map to translate
        a user-facing label back to its webOS-internal id (and type) when
        select_source is called.
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
                s.powered_on = True

        app_id = getattr(tv_state, "current_app_id", None)
        if app_id:
            s.powered_on = True
        s.current_app_id = app_id

        s.muted = getattr(tv_state, "muted", None)
        vol = getattr(tv_state, "volume", None)
        s.volume_level = (vol / 100.0) if isinstance(vol, (int, float)) and vol >= 0 else None

        # ----- Build source map (label -> (kind, id)) and source list -----
        source_map: dict[str, tuple[str, str]] = {}

        inputs = getattr(tv_state, "inputs", None) or {}
        for inp in inputs.values():
            if not isinstance(inp, dict):
                continue
            label = inp.get("label") or inp.get("id")
            inp_id = inp.get("id")
            if label and inp_id:
                source_map[label] = ("input", inp_id)

        apps = getattr(tv_state, "apps", None) or {}
        for app in apps.values():
            if not isinstance(app, dict):
                continue
            # Skip system apps users don't want to launch directly
            if app.get("systemApp") is True:
                continue
            label = app.get("title") or app.get("id")
            app_id_val = app.get("id")
            if label and app_id_val and label not in source_map:
                # Don't overwrite inputs if an app happens to share a label
                source_map[label] = ("app", app_id_val)

        # Apply user filter from config_entry options, if any.
        # Stored as a list of allowed labels under the "sources" key.
        allowed = self._config.get("allowed_sources")
        if isinstance(allowed, list) and allowed:
            source_map = {k: v for k, v in source_map.items() if k in allowed}

        self._source_map = source_map
        s.source_list = sorted(source_map.keys())

        # Determine current source label from current_app_id
        if app_id:
            for label, (kind, ident) in source_map.items():
                if kind == "app" and ident == app_id:
                    s.current_source = label
                    break

        # Also honour explicit current_input for HDMI etc.
        current_input = getattr(tv_state, "current_input", None)
        if isinstance(current_input, dict):
            label = current_input.get("label") or current_input.get("id")
            if label:
                s.current_source = label

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
                has_live_connection = self._is_connected_safe()
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
                      self._is_connected_safe())

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

        if self._client and self._is_connected_safe():
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
        """Select a source by user-facing label.

        Looks up the label in the source map built by _on_tv_state, then calls
        the right webOS method based on the kind:
          - "app"   → launch_app(app_id)  (or launch_app_with_params if older lib)
          - "input" → set_input(input_id)

        If the label isn't found (state hasn't pushed yet, or stale config),
        we fall back to trying both endpoints with the raw string.
        """
        mapping = self._source_map.get(source)

        async def _launch_app(c: WebOsClient, app_id: str) -> None:
            # aiowebostv 0.7+ has launch_app(id); older versions only had
            # launch_app_with_params(id, params). Try the newer one first.
            launch_fn = getattr(c, "launch_app", None)
            if launch_fn is not None:
                await launch_fn(app_id)
            else:
                await c.launch_app_with_params(app_id, {})

        async def _run(c: WebOsClient) -> None:
            if mapping is not None:
                kind, ident = mapping
                if kind == "app":
                    _LOGGER.debug("select_source: launching app id=%s", ident)
                    await _launch_app(c, ident)
                else:
                    _LOGGER.debug("select_source: setting input id=%s", ident)
                    await c.set_input(ident)
                return

            _LOGGER.debug(
                "select_source: %r not in source_map (%d entries); falling back",
                source, len(self._source_map),
            )
            try:
                await _launch_app(c, source)
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

    def _is_connected_safe(self) -> bool:
        """Return whether we have a live websocket, never raising.

        aiowebostv's is_connected() can occasionally re-raise the last
        WebSocket error (e.g. WSMessageTypeError 1008) instead of returning
        False. Wrap defensively so we always get a clean bool.
        """
        if self._client is None:
            return False
        try:
            return bool(self._client.is_connected())
        except Exception:  # noqa: BLE001
            return False

    async def _ensure_connected(self) -> None:
        if not self._client or not self._is_connected_safe():
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
