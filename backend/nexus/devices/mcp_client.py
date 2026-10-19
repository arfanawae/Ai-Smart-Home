"""DeviceGateway over MCP: the backend's side of Blueprint §12.1.

Spawns `python -m nexus_mcp` as a stdio subprocess and talks MCP to it. Layer 1 of validation runs
here (allowlist + argument checks) before anything is sent; the server runs layer 2.

Robustness: if the server dies or stops answering, the command returns FAILED with a clear message
(never a silent success) and the next call starts a fresh server.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ..tools import TOOL_NAMES, ToolSpec
from .models import CommandResult, CommandStatus, LightState
from .service import ValidationError, validate

MOCK_FAILURE_TOOL = "nexus_debug_set_mock_failure"  # see nexus_mcp/server.py
DEFAULT_SERVER_COMMAND = (sys.executable, "-m", "nexus_mcp")


class McpDevices:
    def __init__(
        self,
        command: Sequence[str] = DEFAULT_SERVER_COMMAND,
        call_timeout_s: float = 10.0,  # > the server's own device timeout (3 s × 2 attempts)
        start_timeout_s: float = 15.0,
    ) -> None:
        self._command = list(command)
        self._call_timeout_s = call_timeout_s
        self._start_timeout_s = start_timeout_s
        self._session: ClientSession | None = None
        self._runner: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self._error: BaseException | None = None
        self._tools: tuple[ToolSpec, ...] = ()
        self._simulated = True
        self._lock = asyncio.Lock()

    # ── lifecycle ──────────────────────────────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._session is not None

    @property
    def simulated(self) -> bool:
        return self._simulated

    @property
    def tools(self) -> tuple[ToolSpec, ...]:
        return self._tools

    async def start(self) -> None:
        """Start (or restart) the server. Raises ConnectionError if it cannot be reached."""
        async with self._lock:
            if self.connected:
                return
            if self._runner is not None and not self._runner.done():
                # A previous connection is still shutting down: let it finish first, or its cleanup
                # could clear the new session.
                self._stop.set()
                await asyncio.wait({self._runner}, timeout=5)
            self._stop, self._ready, self._error = asyncio.Event(), asyncio.Event(), None
            self._runner = asyncio.create_task(self._run(self._stop, self._ready))
            try:
                await asyncio.wait_for(self._ready.wait(), self._start_timeout_s)
            except TimeoutError:
                self._stop.set()
                raise ConnectionError("MCP Server ไม่ตอบระหว่างเริ่มต้น") from None
            if not self.connected:
                raise ConnectionError(f"เริ่ม MCP Server ไม่ได้: {self._error}")
        # The server stamps every result with whether its driver is simulated — even a FAILED one,
        # so this is right when the real board is offline at start-up.
        probe = await self.execute("get_device_status", {"device_id": "main_light"})
        self._simulated = probe.simulated

    async def _run(self, stop: asyncio.Event, ready: asyncio.Event) -> None:
        """Owns the subprocess and the session for their whole life (anyio needs one task for both).
        Works only with its own `stop`/`ready` events and session, so a newer connection is never
        touched by this one's cleanup."""
        params = StdioServerParameters(command=self._command[0], args=self._command[1:], env=dict(os.environ))
        mine: ClientSession | None = None
        try:
            async with stdio_client(params) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    # The LLM only ever sees allowlisted tools, whatever the server advertises.
                    self._tools = tuple(
                        ToolSpec(t.name, t.description or "", dict(t.input_schema))
                        for t in listed.tools
                        if t.name in TOOL_NAMES
                    )
                    mine = self._session = session
                    ready.set()
                    await stop.wait()
        except BaseException as e:  # noqa: BLE001 — reported to start() / the next call
            self._error = e
        finally:
            if self._session is mine:
                self._session = None
            ready.set()

    async def close(self) -> None:
        self._stop.set()
        if self._runner is not None:
            try:
                await asyncio.wait_for(self._runner, 5)
            except (TimeoutError, asyncio.CancelledError):
                self._runner.cancel()
        self._session = None

    async def _ensure(self) -> ClientSession | None:
        if self._session is None:
            try:
                await self.start()
            except ConnectionError:
                return None
        return self._session

    # ── calls ──────────────────────────────────────────────────────────────────────────────────
    async def execute(
        self, tool: str, args: dict[str, Any], on_waiting: Callable[[], None] | None = None
    ) -> CommandResult:
        started = time.monotonic()

        def local(status: CommandStatus, error: str) -> CommandResult:
            return CommandResult(
                tool=tool,
                args=dict(args) if isinstance(args, dict) else {},
                status=status,
                simulated=self._simulated,
                error=error,
                latency_ms=round((time.monotonic() - started) * 1000),
                device_id=args.get("device_id") if isinstance(args, dict) else None,
            )

        try:
            validate(tool, args)  # layer 1 — the server validates again (layer 2)
        except ValidationError as e:
            return local(CommandStatus.REJECTED, str(e))

        session = await self._ensure()
        if session is None:
            return local(CommandStatus.FAILED, "เชื่อมต่อ MCP Server ไม่ได้ — ไม่ได้ส่งคำสั่งไปที่อุปกรณ์")

        waiting_reported = False

        async def on_progress(_progress: float, _total: float | None, _message: str | None) -> None:
            nonlocal waiting_reported
            if on_waiting and not waiting_reported:
                waiting_reported = True
                on_waiting()

        try:
            response = await asyncio.wait_for(
                session.call_tool(tool, args, progress_callback=on_progress), self._call_timeout_s
            )
        except TimeoutError:
            await self._restart_later()
            return local(CommandStatus.TIMEOUT, "MCP Server ไม่ตอบกลับ — สถานะอุปกรณ์ไม่แน่ชัด")
        except Exception as e:  # noqa: BLE001 — a dead pipe, a crashed server, a protocol error
            await self._restart_later()
            return local(CommandStatus.FAILED, f"MCP Server ขัดข้อง: {e}")

        payload = response.structured_content
        if not isinstance(payload, dict) or "status" not in payload:
            return local(CommandStatus.FAILED, "MCP Server ส่งผลลัพธ์ผิดรูปแบบ")
        return CommandResult.from_dict(payload)

    async def _restart_later(self) -> None:
        """Drop the broken connection; the next call starts a fresh server."""
        self._stop.set()
        self._session = None

    async def get_state(self, device_id: str) -> LightState | None:
        result = await self.execute("get_device_status", {"device_id": device_id})
        return result.reported if result.ok else None

    async def set_mock_failure(self, mode: str) -> bool:
        """Testing aid: forwarded to the server's mock driver."""
        session = await self._ensure()
        if session is None:
            return False
        response = await session.call_tool(MOCK_FAILURE_TOOL, {"mode": mode})
        return not response.is_error
