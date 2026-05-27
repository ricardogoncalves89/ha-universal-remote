"""Constants for the Universal Remote integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "universal_remote"

# Config entry keys
CONF_DEVICE_TYPE: Final = "device_type"
CONF_HOST: Final = "host"
CONF_NAME: Final = "name"
CONF_MAC: Final = "mac"
CONF_CLIENT_KEY: Final = "client_key"
CONF_UNIQUE_ID: Final = "unique_id"

# Device types
DEVICE_TYPE_LG_WEBOS: Final = "lg_webos"
DEVICE_TYPE_SAMSUNG: Final = "samsung"
DEVICE_TYPE_ANDROID_TV: Final = "android_tv"
DEVICE_TYPE_APPLE_TV: Final = "apple_tv"

DEVICE_TYPES: Final = [
    DEVICE_TYPE_LG_WEBOS,
    DEVICE_TYPE_SAMSUNG,
    DEVICE_TYPE_ANDROID_TV,
    DEVICE_TYPE_APPLE_TV,
]

DEVICE_TYPE_LABELS: Final = {
    DEVICE_TYPE_LG_WEBOS: "LG WebOS",
    DEVICE_TYPE_SAMSUNG: "Samsung Tizen",
    DEVICE_TYPE_ANDROID_TV: "Android TV / Google TV",
    DEVICE_TYPE_APPLE_TV: "Apple TV",
}

# Canonical button vocabulary — every adapter MUST map these to its native commands.
# Anything not listed here is rejected at the remote.send_command boundary.
class Button:
    """Canonical button names used across all adapters."""

    # Power
    POWER = "POWER"
    POWER_ON = "POWER_ON"
    POWER_OFF = "POWER_OFF"

    # Directional pad
    UP = "UP"
    DOWN = "DOWN"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    OK = "OK"
    BACK = "BACK"
    EXIT = "EXIT"

    # Navigation
    HOME = "HOME"
    MENU = "MENU"
    INFO = "INFO"
    GUIDE = "GUIDE"
    SETTINGS = "SETTINGS"

    # Channel
    CH_UP = "CH_UP"
    CH_DOWN = "CH_DOWN"
    CH_LIST = "CH_LIST"

    # Volume
    VOL_UP = "VOL_UP"
    VOL_DOWN = "VOL_DOWN"
    MUTE = "MUTE"

    # Media transport
    PLAY = "PLAY"
    PAUSE = "PAUSE"
    STOP = "STOP"
    REWIND = "REWIND"
    FAST_FORWARD = "FAST_FORWARD"
    NEXT = "NEXT"
    PREVIOUS = "PREVIOUS"
    RECORD = "RECORD"

    # Color buttons
    RED = "RED"
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    BLUE = "BLUE"

    # Numeric keypad
    NUM_0 = "NUM_0"
    NUM_1 = "NUM_1"
    NUM_2 = "NUM_2"
    NUM_3 = "NUM_3"
    NUM_4 = "NUM_4"
    NUM_5 = "NUM_5"
    NUM_6 = "NUM_6"
    NUM_7 = "NUM_7"
    NUM_8 = "NUM_8"
    NUM_9 = "NUM_9"

    # Source / input
    INPUT = "INPUT"

    # Toggle alias (some cards use POWER_TOGGLE for explicit toggle semantics)
    POWER_TOGGLE = "POWER"

    @classmethod
    def all(cls) -> set[str]:
        """Return every canonical button name."""
        return {
            v for k, v in vars(cls).items()
            if not k.startswith("_") and isinstance(v, str)
        }


# Aliases — extra names that resolve to a canonical button.
# Keys are matched case-insensitively after lowercasing. Lets users feed
# whatever their Lovelace card emits without having to know our conventions.
_ALIASES: Final[dict[str, str]] = {
    # Power
    "power_toggle": Button.POWER,
    "toggle_power": Button.POWER,
    "turn_on": Button.POWER_ON,
    "turn_off": Button.POWER_OFF,
    # D-pad
    "enter": Button.OK,
    "center": Button.OK,
    "select": Button.OK,
    "dpad_center": Button.OK,
    "dpad_up": Button.UP,
    "dpad_down": Button.DOWN,
    "dpad_left": Button.LEFT,
    "dpad_right": Button.RIGHT,
    # Navigation
    "return": Button.BACK,
    "escape": Button.EXIT,
    # Channel
    "channel_up": Button.CH_UP,
    "channel_down": Button.CH_DOWN,
    "channelup": Button.CH_UP,
    "channeldown": Button.CH_DOWN,
    "ch+": Button.CH_UP,
    "ch-": Button.CH_DOWN,
    # Volume
    "volume_up": Button.VOL_UP,
    "volume_down": Button.VOL_DOWN,
    "volumeup": Button.VOL_UP,
    "volumedown": Button.VOL_DOWN,
    "vol+": Button.VOL_UP,
    "vol-": Button.VOL_DOWN,
    # Mute variants (cards often send mute/unmute separately even though TV toggles)
    "volume_mute": Button.MUTE,
    "volume_mute_true": Button.MUTE,
    "volume_mute_false": Button.MUTE,
    "unmute": Button.MUTE,
    "un_mute": Button.MUTE,
    # Media
    "media_play": Button.PLAY,
    "media_pause": Button.PAUSE,
    "media_stop": Button.STOP,
    "media_next": Button.NEXT,
    "media_previous": Button.PREVIOUS,
    "ff": Button.FAST_FORWARD,
    "rew": Button.REWIND,
    "fwd": Button.FAST_FORWARD,
    # Keypad — bare digits
    "0": Button.NUM_0, "1": Button.NUM_1, "2": Button.NUM_2, "3": Button.NUM_3,
    "4": Button.NUM_4, "5": Button.NUM_5, "6": Button.NUM_6, "7": Button.NUM_7,
    "8": Button.NUM_8, "9": Button.NUM_9,
}


def canonicalize_button(raw: str) -> str | None:
    """Map a free-form button name to a canonical Button constant.

    Returns the canonical name (always UPPER) or None if no mapping exists.
    Resolution order:
      1. Exact match against canonical names (case-insensitive)
      2. Match against the _ALIASES table (case-insensitive)
    """
    if not raw:
        return None
    norm = raw.strip().lower()
    upper = norm.upper()

    canonicals = Button.all()
    if upper in canonicals:
        return upper

    if norm in _ALIASES:
        return _ALIASES[norm]

    return None


# Default reconnect / poll behaviour
RECONNECT_INTERVAL_SECONDS: Final = 10
STATE_POLL_INTERVAL_SECONDS: Final = 30
