"""Samsung Tizen TV adapter — uses samsungtvws 2.7.x.

Lifecycle (the painful lessons baked in):

  * `SamsungTVWSAsyncRemote` is designed to be opened ONCE and reused.
    To keep the websocket alive between commands you must call
    `start_listening(callback)` and keep the instance around.

  * Plain `send_command("KEY_FOO")` (str API) opens a one-shot connection
    on every call. That works for occasional commands but cannot reuse the
    paired session correctly on this lib version and often raises
    `ConnectionClosedError: no close frame received or sent` on the second
    use. We avoid it and use the typed-command API instead:
      `send_commands([SendRemoteKey.click("KEY_FOO")])`
    which is what the official HA samsungtv integration does.

  * Pairing is its own short-lived flow that uses `open()` once and
    captures `remote.token`. We do that in the config flow, not here.

  * Many Smart Monitor / Frame TV firmwares answer the WS handshake but
    silently ignore `app_list` and don't expose REST `/api/v2/main`. We
    treat these as expected and fall back to a hardcoded source list.

Wake-up:
  WoL works while the TV is in standby (network chip stays alive). We
  require the MAC in the config.
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
_HARDCODED_APPS: dict[str, str] = {
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

    # How often we ping the websocket to keep state fresh.
    KEEPALIVE_INTERVAL_SECONDS = 30

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
        self._keepalive_task: asyncio.Task | None = None

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

        Even if the connect fails (TV off), we ensure the keepalive loop
        is running so it can attempt passive reconnects when the TV later
        comes online.
        """
        async with self._connect_lock:
            # Make sure keepalive is running regardless of connection outcome.
            # This way, if the TV is off now and the user powers it on later
            # (with the physical remote), the keepalive will pick it up.
            if self._keepalive_task is None or self._keepalive_task.done():
                self._keepalive_task = asyncio.create_task(self._keepalive_loop())

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

            self._state.available = True
            self._state.powered_on = True  # if WS opened, TV is on
            self._notify()

            # Build the source list.
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
            if self._keepalive_task and not self._keepalive_task.done():
                self._keepalive_task.cancel()
                self._keepalive_task = None
            if self._remote is not None:
                try:
                    await self._remote.close()
                except Exception:  # noqa: BLE001
                    pass
                self._remote = None
            self._state.available = False
            self._notify()

    # ----- Keepalive -----

    async def _keepalive_loop(self) -> None:
        """Light keepalive — keeps the websocket alive and reconnects when
        the TV comes back online (e.g. user powered it on via physical remote).

        Three states we handle on each tick:
         1. WS is alive → mark available + powered_on, do nothing else.
         2. WS exists but is dead → close it and try a fresh connect.
         3. _remote is None (after turn_off etc.) → try a passive connect.
            Connect will fail fast if the TV is still off; succeed if the
            user powered it on by other means.
        """
        try:
            while True:
                await asyncio.sleep(self.KEEPALIVE_INTERVAL_SECONDS)

                if self._remote is not None and await self._is_alive_safe():
                    # Healthy path — TV reachable, WS alive.
                    if not self._state.available or not self._state.powered_on:
                        self._state.available = True
                        self._state.powered_on = True
                        self._notify()
                    continue

                # WS not alive — either we have a dead remote (drop it) or
                # nothing at all. Either way, try a fresh connect.
                if self._remote is not None:
                    try:
                        await self._remote.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._remote = None

                # Passive connect attempt. If the TV is off this will fail
                # fast (~5s timeout) and we mark powered_on=False but keep
                # available=True so the next user command can wake the TV.
                try:
                    await asyncio.wait_for(self.connect(), timeout=8.0)
                    _LOGGER.debug(
                        "Samsung keepalive: passive reconnect succeeded"
                    )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug(
                        "Samsung keepalive: passive reconnect failed (%s) — "
                        "TV likely off",
                        type(err).__name__,
                    )
                    if self._state.powered_on:
                        self._state.powered_on = False
                        self._notify()
        except asyncio.CancelledError:
            return

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
        replaces it when available."""
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
                    new_map = ws_map
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
            live = await self._is_alive_safe()
            if not live:
                try:
                    await asyncio.wait_for(self._ensure_connected(), timeout=4.0)
                    live = await self._is_alive_safe()
                except Exception:  # noqa: BLE001
                    live = False
            _LOGGER.debug("Samsung POWER toggle: ws_alive=%s -> %s",
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

        We mark powered_on=False but keep available=True so the entity
        remains usable in HA — turning the TV back on (via WoL or by
        physical remote) should work without the user re-enabling the
        entity. The next command will trigger a fresh connect.
        """
        try:
            await self._send_key("KEY_POWER")
            _LOGGER.debug("Samsung KEY_POWER sent for standby")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "turn_off: send exception (expected during shutdown): %s", err
            )

        self._state.powered_on = False
        # Keep available=True so HA accepts subsequent commands. The next
        # command will reconnect, or the keepalive loop will mark off if
        # nothing reconnects within the keepalive interval.
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
        await self._ensure_connected()
        app_id = self._source_map.get(source)
        if not app_id:
            raise AdapterConnectionError(f"Unknown source {source!r}")
        try:
            await self._remote.run_app(app_id)
            _LOGGER.debug(
                "Samsung run_app(%s) sent for source %r", app_id, source
            )
        except Exception as err:  # noqa: BLE001
            await self._reset_remote()
            try:
                await self._ensure_connected()
                await self._remote.run_app(app_id)
                _LOGGER.debug(
                    "Samsung run_app(%s) sent after reconnect", app_id
                )
            except Exception as err2:  # noqa: BLE001
                await self._reset_remote()
                raise AdapterConnectionError(
                    f"run_app({source!r}) failed: "
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
