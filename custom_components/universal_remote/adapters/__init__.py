"""Adapter package — exposes a factory that builds the right adapter for a device type."""
from __future__ import annotations

from typing import Any

from ..const import (
    DEVICE_TYPE_ANDROID_TV,
    DEVICE_TYPE_APPLE_TV,
    DEVICE_TYPE_LG_WEBOS,
    DEVICE_TYPE_SAMSUNG,
)
from .base import RemoteAdapter, ServiceCaller


def build_adapter(
    device_type: str,
    config: dict[str, Any],
    service_caller: ServiceCaller | None = None,
) -> RemoteAdapter:
    """Return the adapter instance for the given device type."""
    if device_type == DEVICE_TYPE_LG_WEBOS:
        from .lg_webos import LGWebOSAdapter

        return LGWebOSAdapter(config, service_caller)

    if device_type == DEVICE_TYPE_SAMSUNG:
        raise NotImplementedError("Samsung adapter not implemented yet")

    if device_type == DEVICE_TYPE_ANDROID_TV:
        raise NotImplementedError("Android TV adapter not implemented yet")

    if device_type == DEVICE_TYPE_APPLE_TV:
        raise NotImplementedError("Apple TV adapter not implemented yet")

    raise ValueError(f"Unknown device type: {device_type}")
