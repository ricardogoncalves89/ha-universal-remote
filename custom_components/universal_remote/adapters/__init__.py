"""Adapter package — exposes a factory that builds the right adapter for a device type."""
from __future__ import annotations

from typing import Any

from ..const import (
    DEVICE_TYPE_ANDROID_TV,
    DEVICE_TYPE_APPLE_TV,
    DEVICE_TYPE_LG_WEBOS,
    DEVICE_TYPE_SAMSUNG,
)
from .base import RemoteAdapter


def build_adapter(device_type: str, config: dict[str, Any]) -> RemoteAdapter:
    """Return the adapter instance for the given device type."""
    if device_type == DEVICE_TYPE_LG_WEBOS:
        from .lg_webos import LGWebOSAdapter

        return LGWebOSAdapter(config)

    if device_type == DEVICE_TYPE_SAMSUNG:
        # Placeholder — implemented in the next iteration.
        raise NotImplementedError("Samsung adapter not implemented yet")

    if device_type == DEVICE_TYPE_ANDROID_TV:
        raise NotImplementedError("Android TV adapter not implemented yet")

    if device_type == DEVICE_TYPE_APPLE_TV:
        raise NotImplementedError("Apple TV adapter not implemented yet")

    raise ValueError(f"Unknown device type: {device_type}")
