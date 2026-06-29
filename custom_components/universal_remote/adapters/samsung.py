"""Samsung Tizen TV adapter — uses samsungtvws 2.7.x.

Design philosophy (changed in v0.5.4):

  * Zero background polling. We don't ping the TV when nobody's using it.
    This is the most lightweight design possible — the integration is
    completely idle until the user actually presses a button in HA.

  * Reactive liveness check via TCP probe. Before sending any command we
    do a 2-second TCP-connect to port 8002. If the port accepts, the TV
    is reachable; if it times out, the TV is off and we fail fast.

  * `available` stays True after initial setup. Since we don't poll, we
    can't tell HA "the TV is on" reliably between user actions. Marking
    the entity available unconditionally keeps the UI usable.

  * `powered_on` is updated on every user interaction. When the probe
    succeeds we mark on, when it fails we mark off. Users who switch the
    TV on via the physical remote will see HA catch up the moment they
    next interact with the integration.

Lifecycle notes:

  * `SamsungTVWSAsyncRemote` is designed to be opened ONCE and reused via
    `start_listening(callback)` which keeps the websocket alive across
    `send_commands` calls.

  * Plain `send_command("KEY_FOO")` opens a one-shot connection and often
    raises `ConnectionClosedError: no close frame received or sent` on
    second use. We use the typed-command API instead:
      `send_commands([SendRemoteKey.click("KEY_FOO")])`
    which is what the official HA samsungtv integration does.

  * Pairing is its own short-lived flow that uses `open()` once and
    captures `remote.token`. That's done in the config flow, not here.

  * Many Smart Monitor / Frame TV firmwares answer the WS handshake but
    silently ignore `app_list`. We treat that as expected and fall back
    to a hardcoded source list.

Wake-up:
  WoL works while the TV is in standby (network chip stays alive). MAC
  is required in the config.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..const import (
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


CONF_SAMSUNG_TOKEN = "samsung_token"


# Canonical Button -> Tizen KEY_ string.
_BUTTON_MAP: dict[str, str] = {
    Button.UP: "KEY_UP",
    Button.DOWN: "KEY_DOWN",
    Button.LEFT: "KEY_LEFT",
    Button.RIGHT: "KEY_RIGHT",
    Button.OK: "KEY_ENTER",
    Button.BACK: "KEY_RETURN",
    Button.EXIT: "KEY_EXIT",
    Button.HOME: "KEY_HOME",
    Button.MENU: "KEY_MENU",
    Button.INFO: "KEY_INFO",
    Button.GUIDE: "KEY_GUIDE",
    Button.SETTINGS: "KEY_TOOLS",
    Button.PLAY: "KEY_PLAY",
    Button.PAUSE: "KEY_PAUSE",
    Button.STOP: "KEY_STOP",
    Button.NEXT: "KEY_FF",
    Button.PREVIOUS: "KEY_REWIND",
    Button.FAST_FORWARD: "KEY_FF",
    Button.REWIND: "KEY_REWIND",
    Button.RECORD: "KEY_REC",
    Button.CH_UP: "KEY_CHUP",
    Button.CH_DOWN: "KEY_CHDOWN",
    Button.CH_LIST: "KEY_CH_LIST",
    Button.VOL_UP: "KEY_VOLUP",
    Button.VOL_DOWN: "KEY_VOLDOWN",
    Button.MUTE: "KEY_MUTE",
    Button.NUM_0: "KEY_0",
    Button.NUM_1: "KEY_1",
    Button.NUM_2: "KEY_2",
    Button.NUM_3: "KEY_3",
    Button.NUM_4: "KEY_4",
    Button.NUM_5: "KEY_5",
    Button.NUM_6: "KEY_6",
    Button.NUM_7: "KEY_7",
    Button.NUM_8: "KEY_8",
    Button.NUM_9: "KEY_9",
    Button.RED: "KEY_RED",
    Button.GREEN: "KEY_GREEN",
    Button.YELLOW: "KEY_YELLOW",
    Button.BLUE: "KEY_BLUE",
    Button.INPUT: "KEY_SOURCE",
}


# Hardcoded fallback. We always seed with this because most 2024+ Smart
# Monitors silently ignore the WS app_list endpoint.
#
# Values starting with "KEY_" are treated as remote keys (sent via
# SendRemoteKey.click), everything else is treated as an app_id and
# launched via ChannelEmitCommand.launch_app. This lets us mix
# native TV functions (like Live TV via the tuner) with app launches
# in the same source picker.
_HARDCODED_APPS: dict[str, str] = {
    # Native TV functions (sent as keys, not app launches)
    "Live TV":      "KEY_TV",       # switches to the built-in tuner
    # Streaming apps (sent as app launches)
    "Netflix":      "3201907018807",
    "YouTube":      "111299001912",
    "Disney+":      "3201901017640",
    "Prime Video":  "3201512006785",
    "Apple TV":     "3201807016597",
    "Spotify":      "3201606009684",
    "HBO Max":      "3201601007230",
    "Plex":         "3201512006963",
    "Twitch":       "3202203026841",
    "Vodafone TV":  "3201709014731",
}


class SamsungTizenAdapter(RemoteAdapter):
    """Adapter for Samsung Tizen TVs (2016+) over WebSocket SSL on port 8002."""

    SUPPORTED_BUTTONS = set(_BUTTON_MAP.keys()) | {
        Button.POWER,
        Button.POWER_ON,
        Button.POWER_OFF,
    }

    # TCP probe timeout — short, since we just check if port 8002 accepts
    # a TCP connection. No WebSocket handshake, no auth, no side-effects.
    PROBE_TIMEOUT_SECONDS = 2.0

    def __init__(
        self,
        config: dict[str, Any],
        service_caller=None,
    ) -> None:
        super().__init__(config, service_caller)
        self._host: str = config[CONF_HOST]
        self._mac: str | None = config.get(CONF_MAC)
        self._token: str | None = config.get(CONF_SAMSUNG_TOKEN)
        self._name: str = config.get("name", "Universal Remote")

        self._remote: Any = None  # SamsungTVWSAsyncRemote
        self._connect_lock = asyncio.Lock()

        self._source_map: dict[str, str] = {}
        self.on_source_map_changed = None

        # Seed source_map from persisted known_sources so OptionsFlow has
        # something to show right after a reload.
        seed = config.get("known_sources")
        if isinstance(seed, list):
            for label in seed:
                if isinstance(label, str):
                    self._source_map[label] = ""

    # ----- Lifecycle -----

    async def connect(self) -> None:
        """Open the websocket and start listening.

        Idempotent — calling twice is safe. The actual websocket open
        happens inside `start_listening` which keeps the connection
        alive for subsequent `send_commands` calls.

        This is called lazily — only when the user actually wants to send
        a command. We never poll the TV in the background.
        """
        async with self._connect_lock:
            if self._remote is not None and await self._is_alive_safe():
                return

            # Clear any stale instance.
            if self._remote is not None:
                try:
                    await self._remote.close()
                except Exception:  # noqa: BLE001
                    pass
                self._remote = None

            try:
                from samsungtvws.async_remote import SamsungTVWSAsyncRemote
            except ImportError as err:
                raise AdapterConnectionError(
                    "samsungtvws library not installed."
                ) from err

            if not self._token:
                raise AdapterAuthError(
                    "Samsung token missing — pair the TV via the config flow"
                )

            try:
                self._remote = SamsungTVWSAsyncRemote(
                    host=self._host,
                    port=8002,
                    token=self._token,
                    name=self._name,
                    timeout=5,
                )
                # start_listening keeps the websocket alive between commands.
                # The callback receives every event from the TV.
                await self._remote.start_listening(self._on_ws_event)
            except Exception as err:  # noqa: BLE001
                self._remote = None
                err_name = type(err).__name__
                msg = str(err)
                if "Unauthorized" in err_name or "Unauthorized" in msg:
                    raise AdapterAuthError(
                        "Samsung TV refused the token — re-pair via the "
                        "config flow."
                    ) from err
                raise AdapterConnectionError(
                    f"Unable to connect to Samsung TV: {err_name}: {err}"
                ) from err

            # The TV may have issued a fresh token on reconnect.
            new_token = getattr(self._remote, "token", None)
            if new_token and new_token != self._token:
                self._token = new_token
                self._state.extra_attributes["token"] = new_token

            # Once the WS opens we KNOW the TV is on. available stays True.
            self._state.available = True
            self._state.powered_on = True
            self._notify()

            # Build the source list (one-shot — best-effort).
            asyncio.create_task(self._refresh_app_list())

    def _on_ws_event(self, event: Any, response: Any) -> None:
        """Callback from samsungtvws — fires for every TV event."""
        try:
            if self._remote is not None:
                new_token = getattr(self._remote, "token", None)
                if new_token and new_token != self._token:
                    _LOGGER.debug("Samsung token rotated; updating cached token")
                    self._token = new_token
                    self._state.extra_attributes["token"] = new_token
                    self._notify()
        except Exception:  # noqa: BLE001
            pass

    async def disconnect(self) -> None:
        async with self._connect_lock:
            if self._remote is not None:
                try:
                    await self._remote.close()
                except Exception:  # noqa: BLE001
                    pass
                self._remote = None
            # Keep available=True even on disconnect — the integration is
            # still set up, we just have no live socket right now.
            self._notify()

    # ----- Probe -----

    async def _tcp_probe(self) -> bool:
        """Quick liveness probe — TCP-connect to port 8002 with short timeout.

        Returns True if the TV's WebSocket port accepts a connection. This
        does NOT open a WebSocket — we just verify the socket is reachable
        and immediately close. No auth, no side-effects on the TV.

        Why this instead of WebSocket connect:
          * Faster (~tens of milliseconds vs seconds for full WSS handshake)
          * No token exchange, no risk of triggering pairing flows
          * Cheap enough to call before every user command

        Returns False on:
          * Connection refused (TV off / network chip asleep — rare)
          * Timeout (TV unreachable, network down)
          * Any other socket error
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, 8002),
                timeout=self.PROBE_TIMEOUT_SECONDS,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            return True
        except (asyncio.TimeoutError, OSError):
            return False
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Samsung TCP probe unexpected error: %s", err)
            return False

    async def _is_alive_safe(self) -> bool:
        """Wrap is_alive() defensively."""
        if self._remote is None:
            return False
        try:
            method = getattr(self._remote, "is_alive", None)
            if method is None:
                return True  # older versions, optimistic
            result = method()
            if asyncio.iscoroutine(result):
                result = await result
            return bool(result)
        except Exception:  # noqa: BLE001
            return False

    async def _refresh_app_list(self) -> None:
        """Build the source map. Always seeded from hardcoded; WS list
        replaces app entries when available, but KEY_-prefixed entries
        (native TV functions like Live TV) are always preserved."""
        new_map: dict[str, str] = dict(_HARDCODED_APPS)

        if self._remote is not None:
            try:
                apps = await asyncio.wait_for(
                    self._remote.app_list(), timeout=8.0
                )
                ws_map: dict[str, str] = {}
                for app in apps or []:
                    if isinstance(app, dict):
                        name = app.get("name")
                        app_id = app.get("appId") or app.get("app_id")
                        if name and app_id:
                            ws_map[str(name)] = str(app_id)
                if ws_map:
                    _LOGGER.info(
                        "Samsung TV reported %d apps via WS — using WS list",
                        len(ws_map),
                    )
                    # Preserve native TV functions (KEY_-prefixed) from the
                    # hardcoded map; WS only returns apps, never native funcs.
                    native = {
                        k: v for k, v in _HARDCODED_APPS.items()
                        if v.startswith("KEY_")
                    }
                    new_map = {**native, **ws_map}
            except asyncio.TimeoutError:
                _LOGGER.info(
                    "Samsung WS app_list timed out — using hardcoded list (%d entries)",
                    len(new_map),
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Samsung WS app_list failed (%s); using hardcoded list",
                    err,
                )
        else:
            _LOGGER.info(
                "Samsung TV source list set from hardcoded apps (%d entries)",
                len(new_map),
            )

        if new_map == self._source_map:
            return

        self._source_map = new_map

        allowed = self._config.get("allowed_sources")
        if isinstance(allowed, list) and allowed:
            visible = {k: v for k, v in new_map.items() if k in allowed}
        else:
            visible = new_map
        self._state.source_list = sorted(visible.keys())

        if self.on_source_map_changed is not None:
            try:
                self.on_source_map_changed(sorted(new_map.keys()))
            except Exception:  # noqa: BLE001
                pass

        self._notify()

    # ----- Sending commands -----

    async def _send_key(self, key: str) -> None:
        """Send a Tizen KEY_xxx via the typed-command API.

        Critical: uses `send_commands([SendRemoteKey.click(...)])` rather
        than `send_command(string)` because the latter has issues reusing
        the paired session in samsungtvws 2.7.x — it often raises
        ConnectionClosedError on second use.
        """
        try:
            from samsungtvws.remote import SendRemoteKey
        except ImportError as err:
            raise AdapterConnectionError(
                f"samsungtvws missing SendRemoteKey: {err}"
            ) from err

        await self._ensure_connected()

        cmd = SendRemoteKey.click(key)
        try:
            await self._remote.send_commands([cmd])
            _LOGGER.debug("Samsung sent %s", key)
        except Exception as err:  # noqa: BLE001
            err_name = type(err).__name__
            _LOGGER.debug(
                "Samsung send_commands(%s) failed: %s: %s — reconnecting",
                key, err_name, err,
            )
            await self._reset_remote()
            try:
                await self._ensure_connected()
                await self._remote.send_commands([cmd])
                _LOGGER.debug("Samsung sent %s (after reconnect)", key)
            except Exception as err2:  # noqa: BLE001
                await self._reset_remote()
                raise AdapterConnectionError(
                    f"send {key} failed even after reconnect: "
                    f"{type(err2).__name__}: {err2}"
                ) from err2

    # ----- Commands (public API) -----

    async def press_button(self, button: str) -> None:
        if button == Button.POWER:
            # Lightweight TCP probe to decide direction. No WS handshake,
            # no side-effects on the TV.
            live = await self._tcp_probe()
            _LOGGER.debug("Samsung POWER toggle: tcp_probe=%s -> %s",
                          live, "turn_off" if live else "turn_on")
            if live:
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

        key = _BUTTON_MAP.get(button)
        if key is None:
            raise UnsupportedButtonError(
                f"Samsung adapter does not support button {button!r}"
            )

        # Probe before sending — if TV is off, fail fast instead of waiting
        # for a 5s websocket timeout.
        if not await self._tcp_probe():
            _LOGGER.debug(
                "Samsung %s requested but TV not reachable — ignoring", key
            )
            # Reflect off state in HA.
            if self._state.powered_on:
                self._state.powered_on = False
                self._notify()
            raise AdapterConnectionError(
                f"Samsung TV is off — cannot send {key}"
            )

        # TV is reachable — make sure powered_on reflects that.
        if not self._state.powered_on:
            self._state.powered_on = True
            self._notify()

        await self._send_key(key)

    async def turn_on(self) -> None:
        """Wake the TV via Wake-on-LAN."""
        if not self._mac:
            raise AdapterConnectionError(
                "No MAC address configured — Wake-on-LAN unavailable"
            )
        if self._service_caller is None:
            raise AdapterConnectionError(
                "No service caller available — adapter built standalone"
            )

        try:
            _LOGGER.debug("Samsung WoL packet #1 to %s", self._mac)
            await self._service_caller(
                "wake_on_lan", "send_magic_packet", {"mac": self._mac}
            )
            await asyncio.sleep(0.25)
            _LOGGER.debug("Samsung WoL packet #2 to %s", self._mac)
            await self._service_caller(
                "wake_on_lan", "send_magic_packet", {"mac": self._mac}
            )
            _LOGGER.info("Samsung WoL packets sent to %s", self._mac)
            self._state.powered_on = True
            self._notify()
        except Exception as err:  # noqa: BLE001
            raise AdapterConnectionError(
                f"Wake-on-LAN failed: {err}"
            ) from err

    async def turn_off(self) -> None:
        """Send KEY_POWER (terminal — TV closes the websocket).

        We don't probe before sending — if turn_off was called explicitly
        (or via POWER toggle which already probed), we just try. If the TV
        is already off, _send_key will fail and that's fine.

        Marks powered_on=False and keeps available=True so subsequent
        commands can still flow (next button press will probe and
        reconnect as needed).
        """
        try:
            await self._send_key("KEY_POWER")
            _LOGGER.debug("Samsung KEY_POWER sent for standby")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "turn_off: send exception (expected during shutdown): %s", err
            )

        self._state.powered_on = False
        self._notify()
        await self._reset_remote()

    async def volume_up(self) -> None:
        await self.press_button(Button.VOL_UP)

    async def volume_down(self) -> None:
        await self.press_button(Button.VOL_DOWN)

    async def mute(self, muted: bool) -> None:
        await self.press_button(Button.MUTE)

    async def set_volume(self, level: float) -> None:
        raise UnsupportedButtonError(
            "Samsung adapter doesn't support absolute volume — use "
            "VOL_UP/VOL_DOWN buttons instead."
        )

    async def select_source(self, source: str) -> None:
        value = self._source_map.get(source)
        if not value:
            raise AdapterConnectionError(f"Unknown source {source!r}")

        # Probe before attempting — avoids 5s WS timeout when TV is off.
        if not await self._tcp_probe():
            _LOGGER.debug(
                "Samsung select_source(%s -> %s) requested but TV not reachable",
                source, value,
            )
            if self._state.powered_on:
                self._state.powered_on = False
                self._notify()
            raise AdapterConnectionError(
                f"Samsung TV is off — cannot select {source!r}"
            )

        if not self._state.powered_on:
            self._state.powered_on = True
            self._notify()

        # Two flavours of source entry:
        #   * "KEY_xxx"  → send as a remote key (e.g. Live TV via KEY_TV)
        #   * "<digits>" → launch as a Tizen app (e.g. Netflix bundle ID)
        if value.startswith("KEY_"):
            _LOGGER.debug(
                "Samsung select_source(%r) -> sending key %s", source, value
            )
            await self._send_key(value)
            return

        # App launch path.
        try:
            from samsungtvws.remote import ChannelEmitCommand
        except ImportError as err:
            raise AdapterConnectionError(
                f"samsungtvws missing ChannelEmitCommand: {err}"
            ) from err

        await self._ensure_connected()

        cmd = ChannelEmitCommand.launch_app(value)
        try:
            await self._remote.send_commands([cmd])
            _LOGGER.debug(
                "Samsung launch_app(%s) sent for source %r", value, source
            )
        except Exception as err:  # noqa: BLE001
            err_name = type(err).__name__
            _LOGGER.debug(
                "Samsung launch_app(%s) failed: %s: %s — reconnecting",
                value, err_name, err,
            )
            await self._reset_remote()
            try:
                await self._ensure_connected()
                await self._remote.send_commands([cmd])
                _LOGGER.debug(
                    "Samsung launch_app(%s) sent after reconnect", value
                )
            except Exception as err2:  # noqa: BLE001
                await self._reset_remote()
                raise AdapterConnectionError(
                    f"launch_app({source!r}) failed: "
                    f"{type(err2).__name__}: {err2}"
                ) from err2

    async def play(self) -> None:
        await self.press_button(Button.PLAY)

    async def pause(self) -> None:
        await self.press_button(Button.PAUSE)

    async def stop(self) -> None:
        await self.press_button(Button.STOP)

    # ----- Helpers -----

    async def _ensure_connected(self) -> None:
        if self._remote is None or not await self._is_alive_safe():
            await self.connect()

    async def _reset_remote(self) -> None:
        """Tear down the WS so the next call forces a fresh connect."""
        if self._remote is not None:
            try:
                await self._remote.close()
            except Exception:  # noqa: BLE001
                pass
            self._remote = None
