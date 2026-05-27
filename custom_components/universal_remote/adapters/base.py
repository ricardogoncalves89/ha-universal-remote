"""Base adapter interface — every device-specific adapter implements this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeviceState:
    """Normalised state snapshot exposed to the HA entities.

    Every adapter produces this regardless of the underlying device.
    Fields are intentionally permissive — None means "unknown / not applicable".
    """

    available: bool = False
    powered_on: bool | None = None
    muted: bool | None = None
    volume_level: float | None = None  # 0.0 - 1.0
    current_source: str | None = None
    source_list: list[str] = field(default_factory=list)
    current_app_id: str | None = None
    media_title: str | None = None
    extra_attributes: dict[str, Any] = field(default_factory=dict)


StateCallback = Callable[[DeviceState], None]


class AdapterError(Exception):
    """Generic adapter error."""


class AdapterAuthError(AdapterError):
    """Raised when authentication / pairing fails."""


class AdapterConnectionError(AdapterError):
    """Raised when the device is unreachable."""


class UnsupportedButtonError(AdapterError):
    """Raised when a canonical button has no mapping for this device."""


class RemoteAdapter(ABC):
    """Common interface every device adapter must implement.

    The adapter owns the connection lifecycle and translates canonical
    button names + media_player operations into device-specific calls.
    """

    # Subclasses declare which canonical buttons they actually support.
    # Calling press_button with a button outside this set raises UnsupportedButtonError.
    SUPPORTED_BUTTONS: set[str] = set()

    def __init__(self, config: dict[str, Any]) -> None:
        """Store config — connect() happens later, called by the coordinator."""
        self._config = config
        self._state = DeviceState()
        self._listeners: list[StateCallback] = []

    # ----- Lifecycle -----

    @abstractmethod
    async def connect(self) -> None:
        """Open connection and start receiving state updates.

        Must be idempotent — calling twice should be safe.
        Raise AdapterAuthError on auth failures, AdapterConnectionError otherwise.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection cleanly. Must be idempotent."""

    # ----- State observation -----

    @property
    def state(self) -> DeviceState:
        """Current snapshot."""
        return self._state

    def add_listener(self, callback: StateCallback) -> Callable[[], None]:
        """Subscribe to state changes. Returns an unsubscribe function."""
        self._listeners.append(callback)

        def _unsub() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return _unsub

    def _notify(self) -> None:
        """Internal — call after mutating self._state."""
        for cb in list(self._listeners):
            try:
                cb(self._state)
            except Exception:  # noqa: BLE001  — never let a listener break the adapter
                pass

    # ----- Commands -----

    @abstractmethod
    async def press_button(self, button: str) -> None:
        """Send a canonical button press."""

    @abstractmethod
    async def turn_on(self) -> None: ...

    @abstractmethod
    async def turn_off(self) -> None: ...

    @abstractmethod
    async def volume_up(self) -> None: ...

    @abstractmethod
    async def volume_down(self) -> None: ...

    @abstractmethod
    async def set_volume(self, level: float) -> None:
        """level is 0.0 - 1.0."""

    @abstractmethod
    async def mute(self, muted: bool) -> None: ...

    @abstractmethod
    async def select_source(self, source: str) -> None: ...

    @abstractmethod
    async def play(self) -> None: ...

    @abstractmethod
    async def pause(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
