"""Integration tests: a real `python -m nexus_mcp` subprocess over stdio (simulated light)."""

import asyncio
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from nexus.devices.mcp_client import MOCK_FAILURE_TOOL, McpDevices
from nexus.devices.models import CommandStatus
from nexus.tools import TOOL_NAMES

LIGHT = {"device_id": "main_light"}


def run(coro):
    return asyncio.run(coro)


async def with_devices(body):
    devices = McpDevices()
    await devices.start()
    try:
        return await body(devices)
    finally:
        await devices.close()


def test_llm_only_sees_the_allowlisted_tools():
    async def body(d):
        return {t.name for t in d.tools}

    names = run(with_devices(body))
    assert names == set(TOOL_NAMES)
    assert MOCK_FAILURE_TOOL not in names


def test_command_round_trip_with_progress_and_confirmed_state():
    waits = []

    async def body(d):
        assert d.connected and d.simulated
        return await d.execute("set_brightness", {**LIGHT, "level": 40}, on_waiting=lambda: waits.append(1))

    result = run(with_devices(body))
    assert result.status is CommandStatus.SUCCEEDED and result.simulated
    assert result.reported.on and result.reported.brightness == 40
    assert result.command_id
    assert waits == [1]  # the server's progress notification → WAITING_FOR_DEVICE


def test_backend_rejects_before_sending():
    async def body(d):
        return await d.execute("set_brightness", {**LIGHT, "level": 150}), await d.execute("set_gpio", {"pin": 13})

    too_bright, gpio = run(with_devices(body))
    assert too_bright.status is CommandStatus.REJECTED and too_bright.command_id is None
    assert gpio.status is CommandStatus.REJECTED


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("set_brightness", {**LIGHT, "level": 150}),
        ("set_brightness", {**LIGHT, "level": "50"}),
        ("turn_light_on", {"device_id": "bedroom_light"}),
        ("turn_light_on", {**LIGHT, "pin": 13}),
        ("set_gpio", {"pin": 13}),
    ],
)
def test_server_validates_again_even_if_the_client_does_not(tool, args):
    """Layer 2: talk to the server directly, skipping the backend's checks."""

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "nexus_mcp"])
        async with stdio_client(params) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return await session.call_tool(tool, args)

    response = run(go())
    assert response.is_error
    assert response.structured_content["status"] == "rejected"
    assert response.structured_content["error"]


def test_device_timeout_is_reported_not_hidden():
    async def body(d):
        assert await d.set_mock_failure("timeout")
        return await d.execute("turn_light_on", LIGHT)

    result = run(with_devices(body))
    assert result.status is CommandStatus.TIMEOUT
    assert result.reported is None  # unknown — never claimed


def test_unreachable_server_fails_clearly():
    async def go():
        devices = McpDevices(command=[sys.executable, "-c", "import sys; sys.exit(3)"], start_timeout_s=5)
        with pytest.raises(ConnectionError):
            await devices.start()
        return await devices.execute("turn_light_on", LIGHT)

    result = run(go())
    assert result.status is CommandStatus.FAILED
    assert "MCP" in result.error


def test_reconnects_after_the_connection_drops():
    async def body(d):
        await d._restart_later()  # as if the server had died
        assert not d.connected
        result = await d.execute("turn_light_on", LIGHT)
        return result, d.connected

    result, connected = run(with_devices(body))
    assert result.ok and connected
