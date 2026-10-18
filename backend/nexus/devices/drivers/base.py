"""Driver interface. MockDriver now; UnoQDriver (HTTP → Arduino UNO Q controller) later."""

from __future__ import annotations

from typing import Any, Protocol

from ..models import LightState


class DeviceUnavailable(Exception):
    """The device (or its controller) answered with an error or is unreachable."""


class DeviceDriver(Protocol):
    simulated: bool

    async def get_state(self, device_id: str) -> LightState: ...

    async def apply(self, device_id: str, desired: dict[str, Any]) -> LightState:
        """Apply an absolute desired state and return the state the device reports afterwards."""
        ...
