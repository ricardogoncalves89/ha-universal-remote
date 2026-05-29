"""Apple TV adapter — uses pyatv directly.

Pairing flow:
  1. Scan the network for Apple TVs (zeroconf).
  2. For the chosen device, pair the Companion protocol → user enters a PIN
     shown on the TV. We get back a credential string.
  3. Pair the AirPlay protocol → another PIN appears on the TV. Another credential.
  4. Both credentials are persisted in the config entry.

At runtime we open ONE pyatv.AppleTV that has both credentials attached, so
pyatv can route each operation (remote control, app launch, media metadata)
through whichever protocol supports it best.

Key tvOS notes:
  * Apple TV 4K (gen 3) on tvOS 17/18 → Companion is the protocol for power
    management and reliable remote control. Without Companion the TV can't
    be turned on remotely.
  * tvOS 18.4 has a known issue where the Companion connection drops
    immediately after pairing. We mitigate by reconnecting on demand
    in our _safe_send wrapper.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pyatv
from pyatv import exceptions as pyatv_exceptions
from pyatv.const import Protocol
from pyatv.interface import AppleTV, DeviceListener, PowerListener, PushListener

from ..const import (
    CONF_HOST,
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


# Config keys specific to Apple TV
CONF_APPLE_TV_IDENTIFIER = "atv_identifier"
CONF_CREDENTIALS_COMPANION = "credentials_companion"
CONF_CREDENTIALS_AIRPLAY = "credentials_airplay"


# Canonical button -> pyatv remote_control method name.
# pyatv exposes everything as async methods on RemoteControl, e.g.
# await atv.remote_control.menu()
_BUTTON_MAP: dict[str, str] = {
    Button.UP: "up",
    Button.DOWN: "down",
    Button.LEFT: "left",
    Button.RIGHT: "right",
    Button.OK: "select",
    Button.BACK: "menu",  # On Apple TV, "menu" is the back button
    Button.HOME: "home",
    Button.MENU: "top_menu",  # Long-press home equivalent
    Button.PLAY: "play",
    Button.PAUSE: "pause",
    Button.STOP: "stop",
    Button.NEXT: "next",
    Button.PREVIOUS: "previous",
    Button.FAST_FORWARD: "skip_forward",
    Button.REWIND: "skip_backward",
    # VOL_UP / VOL_DOWN / MUTE are NOT in this map — they go through the
    # Audio facade in pyatv 0.16+, not RemoteControl. Handled below.
    # Apple TV has no numeric keypad, no color buttons, no channel up/down,
    # no INPUT (it's the source itself). Those buttons are unsupported here.
}


class AppleTVAdapter(RemoteAdapter):
    """Adapter for Apple TVs over Companion + AirPlay protocols via pyatv."""

    SUPPORTED_BUTTONS = set(_BUTTON_MAP.keys()) | {
        Button.POWER,
        Button.POWER_ON,
        Button.POWER_OFF,
        Button.VOL_UP,
        Button.VOL_DOWN,
        Button.MUTE,
    }

    def __init__(
        self,
        config: dict[str, Any],
        service_caller=None,
    ) -> None:
        super().__init__(config, service_caller)
        self._host: str = config[CONF_HOST]
        self._identifier: str | None = config.get(CONF_APPLE_TV_IDENTIFIER)
        self._creds_companion: str | None = config.get(CONF_CREDENTIALS_COMPANION)
        self._creds_airplay: str | None = config.get(CONF_CREDENTIALS_AIRPLAY)

        self._atv: AppleTV | None = None
        self._connect_lock = asyncio.Lock()
        # Apps map maintained by push updater — apple_id -> display name
        self._app_map: dict[str, str] = {}
        # Source map (label -> app_id) for select_source
        self._source_map: dict[str, str] = {}
        self.on_source_map_changed = None  # type: ignore[assignment]
        # Last known volume level (0.0-100.0) we observed when not muted.
        # Used to restore volume on un-mute. None means we never saw a volume
        # reading yet (Apple TV in standby, no audio output, etc.).
        self._last_known_volume: float | None = None

        # Seed source_map from persisted known_sources, if any (set by coordinator).
        seed = config.get("known_sources")
        if isinstance(seed, list):
            for label in seed:
                if isinstance(label, str):
                    self._source_map[label] = ""  # placeholder until push update

    # ----- Lifecycle -----

    async def connect(self) -> None:
        """Open the connection to the Apple TV with credentials applied."""
        async with self._connect_lock:
            if self._atv is not None:
                # Already connected — pyatv objects don't expose is_connected,
                # but we track liveness via our listener callbacks.
                return

            if not self._identifier:
                raise AdapterAuthError(
                    "Apple TV identifier missing — re-add the device via the config flow"
                )
            if not self._creds_companion and not self._creds_airplay:
                raise AdapterAuthError(
                    "No credentials configured — pair the device first"
                )

            loop = asyncio.get_running_loop()

            # Re-scan to find the latest config for the device (zeroconf).
            # We do this so we always have current ports/protocols, even if
            # the Apple TV bounced and ports changed.
            try:
                atvs = await pyatv.scan(
                    loop, identifier=self._identifier, timeout=5
                )
            except Exception as err:  # noqa: BLE001
                raise AdapterConnectionError(
                    f"Scan for Apple TV failed: {err}"
                ) from err

            if not atvs:
                raise AdapterConnectionError(
                    f"Apple TV {self._identifier!r} not found on the network"
                )

            atv_conf = atvs[0]

            # Attach stored credentials to whichever protocol they belong to.
            if self._creds_companion:
                companion = atv_conf.get_service(Protocol.Companion)
                if companion is not None:
                    companion.credentials = self._creds_companion
            if self._creds_airplay:
                airplay = atv_conf.get_service(Protocol.AirPlay)
                if airplay is not None:
                    airplay.credentials = self._creds_airplay

            try:
                self._atv = await pyatv.connect(atv_conf, loop)
            except pyatv_exceptions.AuthenticationError as err:
                raise AdapterAuthError(
                    "Apple TV rejected credentials — re-pair the device"
                ) from err
            except Exception as err:  # noqa: BLE001
                self._atv = None
                raise AdapterConnectionError(
                    f"Unable to connect to Apple TV: {type(err).__name__}: {err}"
                ) from err

            # Register listeners — these push state changes to _on_*.
            self._atv.listener = _DeviceCallback(self)
            self._atv.power.listener = _PowerCallback(self)
            self._atv.push_updater.listener = _PushCallback(self)
            try:
                self._atv.push_updater.start()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("push_updater.start() raised; continuing", exc_info=True)

            self._state.available = True
            # Power state and app metadata will arrive via the listeners.
            self._notify()

            # Trigger an initial fetch so we don't sit at "unknown" until the
            # user touches the remote.
            asyncio.create_task(self._refresh_app_list())

    async def disconnect(self) -> None:
        async with self._connect_lock:
            if self._atv is not None:
                try:
                    self._atv.push_updater.stop()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self._atv.close()
                except Exception:  # noqa: BLE001
                    pass
                self._atv = None
            self._state.available = False
            self._notify()

    # ----- State callbacks (called by listeners below) -----

    async def _on_power_changed(self, old_state: Any, new_state: Any) -> None:
        # pyatv PowerState is an IntEnum: Off=0, On=1, Unknown=2.
        try:
            on = bool(int(new_state)) and int(new_state) == 1
        except Exception:  # noqa: BLE001
            on = None  # type: ignore[assignment]
        if on is True:
            self._state.powered_on = True
        elif int(new_state) == 0:
            self._state.powered_on = False
        self._notify()

    async def _on_playstatus(self, updater: Any, playstatus: Any) -> None:
        # Push update from the currently-playing app.
        try:
            self._state.media_title = getattr(playstatus, "title", None)
        except Exception:  # noqa: BLE001
            pass

        # Track current app if available
        try:
            metadata = self._atv.metadata if self._atv else None
            app = getattr(metadata, "app", None) if metadata else None
            if app is not None:
                self._state.current_app_id = app.identifier
                if app.identifier and app.identifier in self._app_map:
                    self._state.current_source = self._app_map[app.identifier]
        except Exception:  # noqa: BLE001
            pass
        self._notify()

    def _on_connection_lost(self, exc: Exception | None) -> None:
        _LOGGER.warning("Apple TV connection lost: %s", exc)
        self._state.available = False
        self._state.powered_on = None
        # Drop the client so next command forces a reconnect.
        self._atv = None
        self._notify()

    def _on_connection_closed(self) -> None:
        _LOGGER.debug("Apple TV connection closed cleanly")
        self._state.available = False
        self._atv = None
        self._notify()

    async def _refresh_app_list(self) -> None:
        """Fetch the launchable apps via Companion and build source_map.

        Called once shortly after connect(). The Companion protocol can take
        a few seconds to be ready after the websocket comes up, so we retry
        a few times with backoff before giving up.
        """
        if self._atv is None:
            return
        apps_facade = getattr(self._atv, "apps", None)
        if apps_facade is None:
            _LOGGER.warning(
                "Apple TV apps facade is None — Companion not paired? "
                "Source list will be empty."
            )
            return

        last_err: Exception | None = None
        for attempt in range(1, 6):  # 5 attempts: 1s, 2s, 4s, 8s, 16s
            try:
                apps = await apps_facade.app_list()
                break
            except Exception as err:  # noqa: BLE001
                last_err = err
                wait = 2 ** (attempt - 1)
                _LOGGER.debug(
                    "app_list() failed (attempt %d/5): %s; retrying in %ds",
                    attempt, err, wait,
                )
                await asyncio.sleep(wait)
        else:
            _LOGGER.warning(
                "Unable to fetch Apple TV app list after 5 attempts: %s. "
                "Source picker will be empty. The integration will keep working "
                "for remote-control commands.",
                last_err,
            )
            return

        new_app_map: dict[str, str] = {}
        new_source_map: dict[str, str] = {}
        for app in apps:
            ident = getattr(app, "identifier", None)
            name = getattr(app, "name", None)
            if ident and name:
                new_app_map[ident] = name
                new_source_map[name] = ident

        _LOGGER.info(
            "Apple TV reported %d apps; first few: %s",
            len(new_source_map),
            list(new_source_map.keys())[:5],
        )

        if new_source_map == self._source_map:
            return

        self._app_map = new_app_map
        self._source_map = new_source_map

        # Apply user filter (allowed_sources) to the visible source_list,
        # keep the full _source_map intact for the options flow.
        allowed = self._config.get("allowed_sources")
        if isinstance(allowed, list) and allowed:
            visible = {k: v for k, v in new_source_map.items() if k in allowed}
        else:
            visible = new_source_map
        self._state.source_list = sorted(visible.keys())

        # Persist for the options flow.
        if self.on_source_map_changed is not None:
            try:
                self.on_source_map_changed(sorted(new_source_map.keys()))
            except Exception:  # noqa: BLE001
                pass

        self._notify()

    # ----- Commands -----

    async def press_button(self, button: str) -> None:
        if button == Button.POWER:
            # Probe: if the connection is alive AND we have a positive power
            # signal, toggle to off. Otherwise toggle to on.
            try:
                await self._ensure_connected()
                live = self._atv is not None and self._state.powered_on is True
            except AdapterConnectionError:
                live = False
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
        # Volume buttons go through the Audio facade, not RemoteControl.
        if button == Button.VOL_UP:
            await self.volume_up()
            return
        if button == Button.VOL_DOWN:
            await self.volume_down()
            return
        if button == Button.MUTE:
            # Toggle: if last-known volume is None or > 0, mute; else un-mute.
            # We don't store an explicit "is_muted" flag because the Apple TV
            # itself doesn't expose one — we only know "what is the volume now".
            await self._ensure_connected()
            audio = getattr(self._atv, "audio", None) if self._atv else None
            current = getattr(audio, "volume", None) if audio else None
            currently_muted = isinstance(current, (int, float)) and current <= 0.5
            await self.mute(not currently_muted)
            return

        method_name = _BUTTON_MAP.get(button)
        if method_name is None:
            raise UnsupportedButtonError(
                f"Apple TV adapter does not support button {button!r}"
            )

        await self._ensure_connected()
        rc = self._atv.remote_control if self._atv else None
        if rc is None:
            raise AdapterConnectionError("No remote_control facade available")

        method = getattr(rc, method_name, None)
        if method is None:
            raise UnsupportedButtonError(
                f"pyatv RemoteControl has no method {method_name!r}"
            )
        try:
            await method()
        except Exception as err:  # noqa: BLE001
            raise AdapterConnectionError(
                f"{button} failed: {type(err).__name__}: {err}"
            ) from err

    async def turn_on(self) -> None:
        await self._ensure_connected()
        if self._atv is None or self._atv.power is None:
            raise AdapterConnectionError("Power facade not available")
        try:
            await self._atv.power.turn_on()
            self._state.powered_on = True
            self._notify()
        except Exception as err:  # noqa: BLE001
            raise AdapterConnectionError(
                f"turn_on failed: {type(err).__name__}: {err}"
            ) from err

    async def turn_off(self) -> None:
        await self._ensure_connected()
        if self._atv is None or self._atv.power is None:
            raise AdapterConnectionError("Power facade not available")
        try:
            await self._atv.power.turn_off()
            self._state.powered_on = False
            self._notify()
        except Exception as err:  # noqa: BLE001
            raise AdapterConnectionError(
                f"turn_off failed: {type(err).__name__}: {err}"
            ) from err

    async def volume_up(self) -> None:
        """Volume up via the Audio facade (pyatv 0.16+)."""
        await self._ensure_connected()
        audio = getattr(self._atv, "audio", None) if self._atv else None
        if audio is None:
            raise AdapterConnectionError(
                "Audio facade not available — pair the AirPlay protocol"
            )
        try:
            await audio.volume_up()
            # Track current volume for future mute toggle.
            self._snapshot_volume()
        except Exception as err:  # noqa: BLE001
            raise AdapterConnectionError(
                f"volume_up failed: {type(err).__name__}: {err}"
            ) from err

    async def volume_down(self) -> None:
        """Volume down via the Audio facade (pyatv 0.16+)."""
        await self._ensure_connected()
        audio = getattr(self._atv, "audio", None) if self._atv else None
        if audio is None:
            raise AdapterConnectionError(
                "Audio facade not available — pair the AirPlay protocol"
            )
        try:
            await audio.volume_down()
            self._snapshot_volume()
        except Exception as err:  # noqa: BLE001
            raise AdapterConnectionError(
                f"volume_down failed: {type(err).__name__}: {err}"
            ) from err

    async def set_volume(self, level: float) -> None:
        """Set volume to an absolute level (0.0–1.0 → 0–100 in pyatv)."""
        await self._ensure_connected()
        audio = getattr(self._atv, "audio", None) if self._atv else None
        if audio is None:
            raise AdapterConnectionError(
                "Audio facade not available — pair the AirPlay protocol"
            )
        try:
            await audio.set_volume(level * 100)
            if level > 0:
                self._last_known_volume = level * 100
        except Exception as err:  # noqa: BLE001
            raise AdapterConnectionError(
                f"set_volume failed: {type(err).__name__}: {err}"
            ) from err

    async def mute(self, muted: bool) -> None:
        """Mute / un-mute by toggling volume between 0 and last-known level.

        Apple TV doesn't expose a dedicated mute toggle, so we emulate one:
          - mute=True  → save current volume, set volume to 0
          - mute=False → restore volume to last-known level (or 30 as default)
        """
        await self._ensure_connected()
        audio = getattr(self._atv, "audio", None) if self._atv else None
        if audio is None:
            raise AdapterConnectionError(
                "Audio facade not available — pair the AirPlay protocol"
            )
        try:
            if muted:
                # Save the current volume before zeroing it.
                self._snapshot_volume()
                await audio.set_volume(0)
            else:
                restore_to = self._last_known_volume or 30.0
                await audio.set_volume(restore_to)
        except Exception as err:  # noqa: BLE001
            raise AdapterConnectionError(
                f"mute({muted}) failed: {type(err).__name__}: {err}"
            ) from err

    def _snapshot_volume(self) -> None:
        """Cache the current volume so mute can restore later. Silent on errors."""
        if not self._atv:
            return
        audio = getattr(self._atv, "audio", None)
        if audio is None:
            return
        try:
            level = audio.volume  # property, 0.0-100.0
            if isinstance(level, (int, float)) and level > 0:
                self._last_known_volume = float(level)
        except Exception:  # noqa: BLE001
            pass

    async def select_source(self, source: str) -> None:
        await self._ensure_connected()
        app_id = self._source_map.get(source)
        if not app_id or self._atv is None:
            raise AdapterConnectionError(f"Unknown source {source!r}")
        apps_facade = getattr(self._atv, "apps", None)
        if apps_facade is None:
            raise AdapterConnectionError("apps facade not available")
        try:
            await apps_facade.launch_app(app_id)
        except Exception as err:  # noqa: BLE001
            raise AdapterConnectionError(
                f"launch_app({source!r}) failed: {type(err).__name__}: {err}"
            ) from err

    async def play(self) -> None:
        await self.press_button(Button.PLAY)

    async def pause(self) -> None:
        await self.press_button(Button.PAUSE)

    async def stop(self) -> None:
        await self.press_button(Button.STOP)

    # ----- Helpers -----

    async def _ensure_connected(self) -> None:
        if self._atv is None:
            await self.connect()


# ----- Listener glue -----
#
# pyatv expects listener objects with specific method names. We can't make the
# adapter itself implement these because the method signatures collide with
# our async API. Use small forwarder classes instead.


class _DeviceCallback(DeviceListener):
    """Bridge pyatv's DeviceListener to AppleTVAdapter._on_connection_*."""

    def __init__(self, adapter: AppleTVAdapter) -> None:
        self._adapter = adapter

    def connection_lost(self, exception: Exception) -> None:
        self._adapter._on_connection_lost(exception)

    def connection_closed(self) -> None:
        self._adapter._on_connection_closed()


class _PowerCallback(PowerListener):
    """Bridge pyatv's PowerListener."""

    def __init__(self, adapter: AppleTVAdapter) -> None:
        self._adapter = adapter

    def powerstate_update(self, old_state: Any, new_state: Any) -> None:
        # pyatv calls this synchronously; fan out to an async task.
        asyncio.create_task(self._adapter._on_power_changed(old_state, new_state))


class _PushCallback(PushListener):
    """Bridge pyatv's PushListener (currently-playing metadata)."""

    def __init__(self, adapter: AppleTVAdapter) -> None:
        self._adapter = adapter

    def playstatus_update(self, updater: Any, playstatus: Any) -> None:
        asyncio.create_task(self._adapter._on_playstatus(updater, playstatus))

    def playstatus_error(self, updater: Any, exception: Exception) -> None:
        _LOGGER.debug("playstatus_error: %s", exception)
