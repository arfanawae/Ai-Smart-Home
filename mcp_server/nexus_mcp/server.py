"""NEXUS MCP server (Blueprint §12.1 / §12.3): the only process that touches device drivers.

Runs as a stdio subprocess of the backend, so no network port is opened for device control.
Exposes the five intent-level tools from nexus.tools — never pins, PWM or GPIO — and validates every
call again with DeviceService before it reaches a driver (the backend already validated once).

Progress: when a command has been sent and the server is waiting for the device to confirm, it sends
an MCP progress notification; the backend turns that into the UI's WAITING_FOR_DEVICE state.

stdout carries the MCP protocol — never print() here; diagnostics go to stderr.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from nexus.devices.drivers.mock import FAILURE_MODES, MockDriver
from nexus.devices.service import DeviceService
from nexus.tools import TOOL_SPECS

SERVER_NAME = "nexus-devices"
# Test/demo hook: makes the simulated light fail on purpose (offline / timeout / mismatch / error).
# Only registered for the mock driver, and never offered to the LLM (the backend allowlist drops it).
MOCK_FAILURE_TOOL = "nexus_debug_set_mock_failure"


def _tool_list(service: DeviceService) -> list[types.Tool]:
    tools = [types.Tool(name=s.name, description=s.description, input_schema=s.parameters) for s in TOOL_SPECS]
    if isinstance(service.driver, MockDriver):
        tools.append(
            types.Tool(
                name=MOCK_FAILURE_TOOL,
                description="Testing only: make the simulated light fail in a chosen way.",
                input_schema={
                    "type": "object",
                    "properties": {"mode": {"type": "string", "enum": list(FAILURE_MODES)}},
                    "required": ["mode"],
                    "additionalProperties": False,
                },
            )
        )
    return tools


def _result(payload: dict[str, Any], is_error: bool) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=is_error,
    )


def build_server(service: DeviceService) -> Server:
    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=_tool_list(service))

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        name, args = params.name, dict(params.arguments or {})
        if name == MOCK_FAILURE_TOOL and isinstance(service.driver, MockDriver):
            mode = args.get("mode")
            if mode not in FAILURE_MODES:
                return _result({"error": f"mode must be one of {FAILURE_MODES}"}, is_error=True)
            service.driver.failure = mode
            return _result({"mock_failure": mode}, is_error=False)

        token = (ctx.meta or {}).get("progress_token")
        pending: list[asyncio.Task[None]] = []

        def on_waiting() -> None:
            if token is not None:
                pending.append(
                    asyncio.create_task(
                        ctx.session.send_progress_notification(
                            token, 1, 2, "waiting for device", related_request_id=ctx.request_id
                        )
                    )
                )

        # DeviceService validates again here: unknown tools, bad arguments and rate limits are rejected
        # before any driver call, whatever the client sent.
        result = await service.execute(name, args, on_waiting=on_waiting)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return _result(result.to_dict(), is_error=not result.ok)

    return Server(
        SERVER_NAME,
        version="0.1.0",
        instructions="Controls the NEXUS smart-home devices. Results report the device's confirmed state.",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def make_service() -> DeviceService:
    from nexus.config import load_settings

    settings = load_settings()
    if settings.device_mode != "mock":  # an UnoQDriver will plug in here in M5
        print(f"DEVICE_MODE={settings.device_mode!r} is not supported yet", file=sys.stderr)
        raise SystemExit(2)
    return DeviceService(MockDriver())
