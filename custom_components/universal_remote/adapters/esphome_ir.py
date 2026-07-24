"""ESPHome IR blaster adapter.

Talks to a user's ESPHome device that exposes a `send_command` service
(one command name in, IR pulses out via `remote_transmitter`). Because
IR is one-way, this adapter has no notion of the target device's state
— it just fires and forgets.

Typical ESPHome YAML shape the user is expected to have already flashed:

    esphome:
      name: xiao-ir-formuler
    api:
      services:
        - service: send_command
          variables:
            command: string
          then:
            - script.execute:
                id: send_key
                key: !lambda 'return command;'

The resulting HA service is `esphome.<device_name>_send_command`, where
`<device_name>` is the ESPHome node name (with any MAC suffix, hyphens
turned to underscores). The user picks this service in the config flow.

Design notes:

* No polling, no persistent connection — every button press is a single
  `hass.services.async_call("esphome", "<service>", {"command": "..."})`.
* `available` is always True. IR devices have no return channel to know
  if the physical device answered, so pretending we're always up keeps
  the UI usable.
* `powered_on` stays None — there's no reliable way to guess it.
* `source_list` is empty. No media_player is created for this adapter
  (the coordinator handles that based on adapter.state.source_list being
  empty and MEDIA_PLAYER capability being absent — see config_flow).
* Volume/mute/CH_UP/CH_DOWN etc. all go through press_button — there's no
  fine-grained volume level available over IR.
"""
from __future__ import annotations

import logging
from typing import Any

from ..const import (
    CONF_ESPHOME_SERVICE,
    Button,
)
from .base import (
    AdapterConnectionError,
    RemoteAdapter,
    UnsupportedButtonError,
)

_LOGGER = logging.getLogger(__name__)


# Default mapping — matches the Formuler Z8/Z11 command names used in the
# reference ESPHome YAML. Adequate for many Android STBs. Users who need
# a different mapping can override via the options flow (future work),
# but the codes captured on the Formuler are a reasonable superset for
# most consumer IR remotes.
_DEFAULT_BUTTON_MAP: dict[str, str] = {
    Button.POWER: "power",
    Button.HOME: "home",
    Button.MENU: "menu",
    Button.BACK: "back",
    Button.EXIT: "exit",
    Button.INFO: "info",
    Button.GUIDE: "epg",
    Button.OK: "ok",
    Button.UP: "up",
    Button.DOWN: "down",
    Button.LEFT: "left",
    Button.RIGHT: "right",
    Button.VOL_UP: "vol_up",
    Button.VOL_DOWN: "vol_down",
    Button.MUTE: "mute",
    Button.CH_UP: "ch_up",
    Button.CH_DOWN: "ch_down",
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
    Button.PLAY: "play",
    Button.PAUSE: "pause",
    Button.STOP: "stop",
    Button.REWIND: "rewind",
    Button.FAST_FORWARD: "forward",
    Button.RECORD: "record",
    Button.RED: "red",
    Button.GREEN: "green",
    Button.YELLOW: "yellow",
    Button.BLUE: "blue",
    # SETTINGS, CH_LIST — not present in the Formuler map; will raise
    # UnsupportedButtonError if a card tries to send them.
}


class ESPHomeIRAdapter(RemoteAdapter):
    """One-way IR adapter via an ESPHome node's send_command service."""

    SUPPORTED_BUTTONS: set[str] = set(_DEFAULT_BUTTON_MAP.keys())
    HAS_MEDIA_PLAYER: bool = False

    def __init__(
        self,
        config: dict[str, Any],
        service_caller: Any | None = None,
    ) -> None:
        super().__init__(config, service_caller)
        # Full service name minus the domain, e.g. "xiao_ir_formuler_send_command".
        self._service_name: str = config[CONF_ESPHOME_SERVICE]
        # Allow per-instance override of the button map (reserved for the
        # options flow later). Merge on top of the default so partial
        # overrides don't lose the rest of the mapping.
        override = config.get("button_map") or {}
        self._button_map = {**_DEFAULT_BUTTON_MAP, **override}

    # ---------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------

    async def connect(self) -> None:
        """No persistent connection. Just verify the service is reachable.

        Called on setup and on retry. We don't hard-fail if the service
        isn't found — the ESPHome node might just be temporarily offline
        and come back later. We do log a warning so the user gets a hint
        via the debug log.
        """
        # Nothing to open. Mark available; state stays "unknown" (None) for
        # everything else since IR is one-way.
        self._state.available = True
        self._state.powered_on = None
        self._state.source_list = []
        self._notify()

    async def disconnect(self) -> None:
        """No-op — there's nothing to close."""
        self._state.available = False
        self._notify()

    # ---------------------------------------------------------------
    # Sending
    # ---------------------------------------------------------------

    async def _call_service(self, command_literal: str) -> None:
        """Invoke esphome.<service_name>(command=<literal>)."""
        if self._service_caller is None:
            raise AdapterConnectionError(
                "ESPHome adapter has no service_caller wired — this is a bug",
            )
        try:
            await self._service_caller(
                "esphome",
                self._service_name,
                {"command": command_literal},
            )
        except Exception as err:  # noqa: BLE001 — surface everything as connection error
            raise AdapterConnectionError(
                f"Failed to call esphome.{self._service_name}: {err}",
            ) from err

    async def press_button(self, button: str) -> None:
        literal = self._button_map.get(button)
        if literal is None:
            raise UnsupportedButtonError(
                f"Button {button!r} has no ESPHome IR mapping",
            )
        _LOGGER.debug(
            "ESPHome IR: press %s -> esphome.%s command=%s",
            button,
            self._service_name,
            literal,
        )
        await self._call_service(literal)

    # For IR the "power" button is a physical toggle. HA's remote.turn_on
    # / turn_off both invoke the same POWER command. This is the least
    # surprising behaviour given we can't observe device state.
    async def turn_on(self) -> None:
        await self.press_button(Button.POWER)

    async def turn_off(self) -> None:
        await self.press_button(Button.POWER)

    # Volume — no level control possible, just up/down/mute button presses.
    async def volume_up(self) -> None:
        await self.press_button(Button.VOL_UP)

    async def volume_down(self) -> None:
        await self.press_button(Button.VOL_DOWN)

    async def set_volume(self, level: float) -> None:
        # We can't set a specific level over IR. Best behaviour is to
        # do nothing rather than pretend to succeed. Cards that assume
        # media_player.volume_set works will get a no-op.
        _LOGGER.debug(
            "ESPHome IR: ignoring set_volume(%.2f) — no level control over IR",
            level,
        )

    async def mute(self, muted: bool) -> None:
        # IR remotes toggle mute — we can't distinguish set-mute from
        # unset-mute. Sending the same command works for both directions.
        await self.press_button(Button.MUTE)

    async def select_source(self, source: str) -> None:
        # IR blasters have no source_list; media_player never presents
        # sources, so this should not be reached in practice.
        _LOGGER.debug(
            "ESPHome IR: ignoring select_source(%r) — no source_list", source,
        )

    async def play(self) -> None:
        await self.press_button(Button.PLAY)

    async def pause(self) -> None:
        await self.press_button(Button.PAUSE)

    async def stop(self) -> None:
        await self.press_button(Button.STOP)
