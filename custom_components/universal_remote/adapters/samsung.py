"""Samsung Tizen TV adapter — uses samsungtvws 2.7.x.

Pairing flow:
  1. Open WSS connection to https://<host>:8002/...
  2. The TV shows a popup "Allow this device to access this TV?"
  3. User accepts on the TV → the server returns a token (auto-stored in `remote.token`)
  4. The token is persisted in the config entry and reused on every reconnect.

Wake-up:
  Samsung Tizen TVs answer Wake-on-LAN while in standby (the network chip
  stays alive). We require the MAC in the config so we can wake via HA's
  wake_on_lan.send_magic_packet service.

State updates:
  Unlike LG webOS or pyatv, samsungtvws is request-response. We poll the
  REST endpoint (port 8001, no auth) every POLL_INTERVAL_SECONDS for power
  + current app. If the REST endpoint stops responding we treat the TV as
  off and drop the websocket.

Sources / apps:
  Listed via WebSocket app_list(). On many 2024+ Smart Monitors and Frame
  models the TV silently ignores the request — there's a documented issue
  for this. When the WS path fails we fall back to a curated hardcoded
  list so the source picker is still useful.
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


# Config keys specific to Samsung
CONF_SAMSUNG_TOKEN = "samsung_token"


# Canonical Button -> Tizen KEY_ string.
# Full list at https://developer.samsung.com/smarttv/develop/guides/user-interaction/key-codes.html
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


# Hardcoded fallback list. Used only when WS app_list() refuses to return
# the installed apps (common on 2024+ Tizen monitors).
# IDs are TIZEN app IDs (numeric strings); these come from Samsung community
# documentation and tend to be stable across regions.
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
    # Portugal. Will be updated when Ricardo confirms the right ID via
    # the WS app_list endpoint or by trial-and-error on the TV.
    "Vodafone TV":  "3201709014731",
}


class SamsungTizenAdapter(RemoteAdapter):
    """Adapter for Samsung Tizen TVs (2016+) over WebSocket SSL on port 8002."""

    SUPPORTED_BUTTONS = set(_BUTTON_MAP.keys()) | {
        Button.POWER,
        Button.POWER_ON,
        Button.POWER_OFF,
    }

    # Samsung doesn't push state — poll on this cadence.
    POLL_INTERVAL_SECONDS = 10

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

        self._remote: Any = None  # SamsungTVWSAsyncRemote instance
        self._rest: Any = None    # SamsungTVAsyncRest instance
        self._connect_lock = asyncio.Lock()
        self._poll_task: asyncio.Task | None = None

        # label -> tizen app_id (numeric string)
        self._source_map: dict[str, str] = {}
        self.on_source_map_changed = None  # set by coordinator

        # Seed source_map from persisted known_sources so the OptionsFlow
        # has something to show even immediately after a reload.
        seed = config.get("known_sources")
        if isinstance(seed, list):
            for label in seed:
                if isinstance(label, str):
                    self._source_map[label] = ""  # placeholder until WS refresh

    # ----- Lifecycle -----

    async def connect(self) -> None:
        """Open the websocket + REST clients."""
        async with self._connect_lock:
            if self._remote is not None:
                return

            try:
                from samsungtvws.async_remote import SamsungTVWSAsyncRemote
                from samsungtvws.async_rest import SamsungTVAsyncRest
            except ImportError as err:
                raise AdapterConnectionError(
                    "samsungtvws library not installed. The integration "
                    "manifest should declare it as a requirement."
                ) from err

            # REST client first — no auth, used for power state + device info.
            # Note: REST runs on port 8001 (not 8002).
            try:
                # samsungtvws 2.7.2 takes a session_factory or uses a default.
                import aiohttp
                session = aiohttp.ClientSession()
                self._rest = SamsungTVAsyncRest(host=self._host, session=session)
            except Exception as err:  # noqa: BLE001
                self._rest = None
                _LOGGER.debug("REST client init failed: %s", err)
                # Not fatal — we can still send commands over WS without REST.

            # WebSocket remote — first time triggers the popup on the TV.
            # Use 31s timeout for initial pairing (gives user time to accept).
            # Subsequent connects use the saved token and are fast.
            timeout = 31 if not self._token else 10
            try:
                self._remote = SamsungTVWSAsyncRemote(
                    host=self._host,
                    port=8002,
                    token=self._token,
                    name=self._name,
                    timeout=timeout,
                )
                await self._remote.start_listening()
            except Exception as err:  # noqa: BLE001
                self._remote = None
                err_name = type(err).__name__
                err_msg = str(err)
                # Common error types in samsungtvws:
                #   - UnauthorizedError: user clicked "deny" on the TV
                #   - ConnectionFailure: network unreachable / TV off
                #   - ConnectionClosedError: socket dropped mid-handshake
                if "Unauthorized" in err_name or "Unauthorized" in err_msg:
                    raise AdapterAuthError(
                        "Samsung TV refused the token — accept the prompt "
                        "on the TV and re-pair via the config flow."
                    ) from err
                raise AdapterConnectionError(
                    f"Unable to connect to Samsung TV: {err_name}: {err}"
                ) from err

            # If pairing produced a new token, capture it for persistence.
            new_token = getattr(self._remote, "token", None)
            if new_token and new_token != self._token:
                self._token = new_token
                self._state.extra_attributes["token"] = new_token
                _LOGGER.info("Samsung pairing succeeded — new token captured")

            self._state.available = True
            self._notify()

            # Start the polling loop.
            if self._poll_task is None or self._poll_task.done():
                self._poll_task = asyncio.create_task(self._poll_loop())

            # Trigger an initial app-list fetch.
            asyncio.create_task(self._refresh_app_list())

    async def disconnect(self) -> None:
        async with self._connect_lock:
            if self._poll_task and not self._poll_task.done():
                self._poll_task.cancel()
                self._poll_task = None
            if self._remote is not None:
                try:
                    await self._remote.close()
                except Exception:  # noqa: BLE001
                    pass
                self._remote = None
            if self._rest is not None:
                # SamsungTVAsyncRest holds a session we created — close it.
                try:
                    session = getattr(self._rest, "_session", None)
                    if session is not None:
                        await session.close()
                except Exception:  # noqa: BLE001
                    pass
                self._rest = None
            self._state.available = False
            self._notify()

    # ----- Polling -----

    async def _poll_loop(self) -> None:
        """Poll the REST endpoint for power state every POLL_INTERVAL_SECONDS.

        We do this because samsungtvws doesn't push updates. If the TV goes
        off, the WS is dropped here too so the next command forces a fresh
        connect.
        """
        try:
            while True:
                await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
                try:
                    await self._poll_once()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug(
                        "Samsung poll iteration failed; continuing",
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            return

    async def _poll_once(self) -> None:
        if self._rest is None:
            return
        try:
            info = await self._rest.rest_device_info()
        except Exception as err:  # noqa: BLE001
            # TV likely off/unreachable. Mark unavailable.
            if self._state.available:
                _LOGGER.debug("Samsung REST unreachable (%s) — marking off", err)
            self._state.available = False
            self._state.powered_on = False
            if self._remote is not None:
                try:
                    await self._remote.close()
                except Exception:  # noqa: BLE001
                    pass
                self._remote = None
            self._notify()
            return

        # rest_device_info returns a dict. Power signal varies by firmware;
        # we use PowerState if present, otherwise treat any successful
        # response as "on" (the REST endpoint is itself disabled in standby).
        device = info.get("device", {}) if isinstance(info, dict) else {}
        powered_on = device.get("PowerState", "").lower() == "on"
        if not powered_on and info:
            powered_on = True

        self._state.available = True
        self._state.powered_on = powered_on
        self._notify()

    async def _refresh_app_list(self) -> None:
        """Try to fetch installed apps via WebSocket. Fall back to hardcoded.

        Many 2024+ Tizen monitors/Frame TVs silently ignore the WS app_list
        request — they respond ms.channel.connect but never the followup
        installedApp.get. We give it a short timeout and fall back gracefully.
        """
        new_map: dict[str, str] = {}

        if self._remote is not None:
            try:
                # app_list() in samsungtvws 2.7 returns list of dicts.
                # Wrap in timeout because some TVs never respond.
                apps = await asyncio.wait_for(
                    self._remote.app_list(), timeout=10.0
                )
                for app in apps or []:
                    if isinstance(app, dict):
                        name = app.get("name")
                        app_id = app.get("appId") or app.get("app_id")
                        if name and app_id:
                            new_map[str(name)] = str(app_id)
                if new_map:
                    _LOGGER.info(
                        "Samsung TV reported %d apps via WS", len(new_map)
                    )
            except asyncio.TimeoutError:
                _LOGGER.info(
                    "Samsung WS app_list timed out — using hardcoded list"
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Samsung WS app_list failed (%s); using hardcoded list",
                    err,
                )

        if not new_map:
            new_map = dict(_HARDCODED_APPS)
            _LOGGER.info(
                "Samsung TV source list set from hardcoded apps (%d entries)",
                len(new_map),
            )

        if new_map == self._source_map:
            return

        self._source_map = new_map

        # Apply user filter to the visible source_list (keep _source_map full).
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

    # ----- Commands -----

    async def press_button(self, button: str) -> None:
        if button == Button.POWER:
            # Probe live REST endpoint to decide direction.
            live = await self._is_rest_alive()
            _LOGGER.debug("Samsung POWER toggle: rest_alive=%s -> %s",
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

        await self._ensure_connected()
        try:
            await self._remote.send_command(key)
        except Exception as err:  # noqa: BLE001
            # Connection may have died — drop client so next press reconnects.
            await self._reset_remote()
            raise AdapterConnectionError(
                f"send_command({key}) failed: {type(err).__name__}: {err}"
            ) from err

    async def turn_on(self) -> None:
        """Wake the TV via Wake-on-LAN.

        Samsung Tizen TVs in standby answer magic packets. Two packets ~250ms
        apart matches what the official Samsung integration does (some
        OLEDs need the second packet).
        """
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
            # Optimistically mark on; next poll will correct if wrong.
            self._state.powered_on = True
            self._notify()
        except Exception as err:  # noqa: BLE001
            raise AdapterConnectionError(
                f"Wake-on-LAN failed: {err}"
            ) from err

    async def turn_off(self) -> None:
        """Send KEY_POWER which puts the TV into standby.

        This is a terminal command — the TV will close the websocket as
        it goes to standby. Don't treat the connection drop as an error.
        """
        await self._ensure_connected()
        try:
            await self._remote.send_command("KEY_POWER")
            _LOGGER.debug("Samsung KEY_POWER sent for standby")
        except Exception as err:  # noqa: BLE001
            # Expected during shutdown.
            _LOGGER.debug(
                "turn_off: exception (expected during shutdown): %s", err
            )

        self._state.powered_on = False
        self._state.available = False
        self._notify()
        await self._reset_remote()

    async def volume_up(self) -> None:
        await self.press_button(Button.VOL_UP)

    async def volume_down(self) -> None:
        await self.press_button(Button.VOL_DOWN)

    async def mute(self, muted: bool) -> None:
        # Samsung KEY_MUTE is a toggle, not absolute. We don't know the
        # current state from the WS, so we just send the key.
        await self.press_button(Button.MUTE)

    async def set_volume(self, level: float) -> None:
        # Tizen WS doesn't expose absolute volume on consumer TVs. The user
        # should use VOL_UP/VOL_DOWN.
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
            raise AdapterConnectionError(
                f"run_app({source!r}) failed: {type(err).__name__}: {err}"
            ) from err

    async def play(self) -> None:
        await self.press_button(Button.PLAY)

    async def pause(self) -> None:
        await self.press_button(Button.PAUSE)

    async def stop(self) -> None:
        await self.press_button(Button.STOP)

    # ----- Helpers -----

    async def _ensure_connected(self) -> None:
        if self._remote is None:
            await self.connect()

    async def _reset_remote(self) -> None:
        """Tear down the WS so the next call forces a fresh connect."""
        if self._remote is not None:
            try:
                await self._remote.close()
            except Exception:  # noqa: BLE001
                pass
            self._remote = None

    async def _is_rest_alive(self) -> bool:
        """Probe the REST endpoint — alive means TV is on/reachable.

        Doesn't open the websocket so doesn't trigger pairing popups.
        """
        if self._rest is None:
            # No REST client; fall back to checking if we have a live WS.
            return self._remote is not None
        try:
            info = await asyncio.wait_for(
                self._rest.rest_device_info(), timeout=3.0
            )
            return bool(info)
        except Exception:  # noqa: BLE001
            return False
