"""UnoQDriver → the real Node relay (relay/server.js) ← the board's real main.py Link with a simulated MCU."""

import asyncio
import secrets
import sys
import threading
import time
from pathlib import Path

import pytest

from nexus.devices.drivers.base import DeviceUnavailable
from nexus.devices.drivers.unoq import UnoQDriver
from nexus.devices.models import CommandStatus
from nexus.devices.service import DeviceService

UNOQ = Path(__file__).resolve().parents[2] / "unoq"
sys.path[:0] = [str(UNOQ), str(UNOQ / "app" / "python")]
from main import Link  # noqa: E402
from relay_harness import relay_available, start_relay  # noqa: E402
from simulate import FakeMcu  # noqa: E402

pytestmark = pytest.mark.skipif(not relay_available(), reason="needs node + `npm install` in relay/")
TOKEN = secrets.token_urlsafe(18)  # throwaway, per test run
LIGHT = {"device_id": "main_light"}


@pytest.fixture
def relay():
    proc, url = start_relay(TOKEN)
    yield url
    proc.terminate()
    proc.wait(5)


@pytest.fixture
def board(relay):
    mcu, stop = FakeMcu(), threading.Event()
    link = Link(relay, TOKEN, lambda *a: mcu.call(*a), log=lambda _m: None, mcu_timeout_s=0.5)

    def run():
        while not stop.is_set():
            time.sleep(link.step())

    threading.Thread(target=run, daemon=True).start()
    deadline = time.time() + 5
    while not link.connected:
        assert time.time() < deadline, "simulated board never connected"
        time.sleep(0.05)
    yield mcu, relay
    stop.set()


def run_with(url, token, body):
    async def go():
        driver = UnoQDriver(url, token, timeout_s=1.5)
        try:
            return await body(driver)
        finally:
            await driver.close()

    return asyncio.run(go())


def test_state_and_apply_come_from_the_board(board):
    mcu, url = board

    async def body(d):
        before = await d.get_state("main_light")
        after = await d.apply("main_light", {"on": True, "brightness": 60, "command_id": "abc"})
        return before, after

    before, after = run_with(url, TOKEN, body)
    assert (before.on, after.on, after.brightness, after.source) == (False, True, 60, "device")
    assert (mcu.on, mcu.level) == (True, 60)


def test_wrong_token_is_a_clear_error(board):
    _, url = board
    with pytest.raises(DeviceUnavailable, match="UNOQ_TOKEN"):
        run_with(url, "wrong-token-0123456789", lambda d: d.get_state("main_light"))


def test_relay_not_running(board):
    with pytest.raises(DeviceUnavailable, match="relay"):
        run_with("http://127.0.0.1:9", TOKEN, lambda d: d.get_state("main_light"))


def test_board_not_connected_is_unavailable(relay):
    with pytest.raises(DeviceUnavailable, match="ไม่ได้เชื่อมต่อ"):
        run_with(relay, TOKEN, lambda d: d.apply("main_light", {"on": True, "brightness": 100}))


def test_mcu_down_is_unavailable_not_success(board):
    mcu, url = board
    mcu.fail = "error"
    with pytest.raises(DeviceUnavailable, match="ไมโครคอนโทรลเลอร์"):
        run_with(url, TOKEN, lambda d: d.apply("main_light", {"on": True, "brightness": 100}))


def test_only_the_main_light_exists(board):
    _, url = board
    with pytest.raises(DeviceUnavailable):
        run_with(url, TOKEN, lambda d: d.get_state("bedroom_light"))


# ── through DeviceService: what the MCP server actually runs ──────────────────────────────────
def service_run(url, body):
    async def go():
        driver = UnoQDriver(url, TOKEN, timeout_s=1.5)
        try:
            return await body(DeviceService(driver, timeout_s=2.0))
        finally:
            await driver.close()

    return asyncio.run(go())


def test_real_device_results_are_not_marked_simulated(board):
    _, url = board
    result = service_run(url, lambda s: s.execute("turn_light_on", LIGHT))
    assert result.status is CommandStatus.SUCCEEDED and result.simulated is False
    assert result.reported.source == "device"


def test_board_that_ignores_the_command_is_a_failure(board):
    mcu, url = board
    mcu.fail = "mismatch"  # replies, but the light did not change
    result = service_run(url, lambda s: s.execute("turn_light_on", LIGHT))
    assert result.status is CommandStatus.FAILED and "ไม่ตรง" in result.error


def test_hanging_mcu_ends_as_timeout_or_failure_never_success(board):
    mcu, url = board
    mcu.fail = "hang"
    result = service_run(url, lambda s: s.execute("turn_light_on", LIGHT))
    assert result.status in (CommandStatus.FAILED, CommandStatus.TIMEOUT)
    assert not result.ok


def test_stuck_mcu_is_reported_while_the_board_still_polls(board):
    mcu, url = board
    mcu.fail = "hang"
    with pytest.raises((DeviceUnavailable, TimeoutError)):
        run_with(url, TOKEN, lambda d: d.apply("main_light", {"on": True, "brightness": 100}))
    # the board keeps polling (heartbeat), but its state read now says the MCU is not answering
    with pytest.raises(DeviceUnavailable, match="ไมโครคอนโทรลเลอร์"):
        run_with(url, TOKEN, lambda d: d.get_state("main_light"))


def test_driver_bug_becomes_a_readable_failure():
    class Broken:
        simulated = False

        async def get_state(self, device_id):
            raise KeyError("brightness")

        async def apply(self, device_id, desired):
            raise AssertionError

    result = asyncio.run(DeviceService(Broken()).execute("turn_light_on", LIGHT))
    assert result.status is CommandStatus.FAILED and "KeyError" in result.error
    assert asyncio.run(DeviceService(Broken()).get_state("main_light")) is None
