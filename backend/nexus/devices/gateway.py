"""How the assistant reaches devices (Blueprint §12.1).

    Assistant ──▶ DeviceGateway ──┬─ DeviceService      in-process (tests, DEVICE_BACKEND=local)
                                  └─ McpDevices  ──stdio──▶ MCP server ──▶ DeviceService ──▶ driver

Both implementations validate every command; with MCP the server validates a second time before
anything reaches a driver, so a bug or a hostile caller on one side cannot drive the hardware.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from ..tools import ToolSpec
from .models import CommandResult, LightState


class DeviceGateway(Protocol):
    @property
    def simulated(self) -> bool: ...

    @property
    def tools(self) -> Sequence[ToolSpec]:
        """Tools the LLM may call — already filtered to the backend's allowlist."""
        ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def execute(
        self, tool: str, args: dict[str, Any], on_waiting: Callable[[], None] | None = None
    ) -> CommandResult: ...

    async def get_state(self, device_id: str) -> LightState | None: ...
