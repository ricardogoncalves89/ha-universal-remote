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

    @classmethod
    def all(cls) -> set[str]:
        """Return every canonical button name."""
        return {
            v for k, v in vars(cls).items()
            if not k.startswith("_") and isinstance(v, str)
        }


# Default reconnect / poll behaviour
RECONNECT_INTERVAL_SECONDS: Final = 10
STATE_POLL_INTERVAL_SECONDS: Final = 30
