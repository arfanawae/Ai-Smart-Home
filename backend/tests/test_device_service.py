import asyncio

import pytest

from nexus.devices.drivers.mock import MockDriver
from nexus.devices.models import CommandStatus
from nexus.devices.service import DeviceService

LIGHT = {"device_id": "main_light"}


def make(failure="none", **kw):
    driver = MockDriver(latency_s=(0, 0))
    driver.failure = failure
    return DeviceService(driver, **{"timeout_s": 0.05, **kw}), driver


def run(coro):
    return asyncio.run(coro)


def test_turn_on_succeeds_and_is_marked_simulated():
    service, _ = make()
    r = run(service.execute("turn_light_on", LIGHT))
    assert r.status is CommandStatus.SUCCEEDED
    assert r.simulated and r.reported.on and r.reported.brightness == 100
    assert r.command_id
    assert r.to_tool_payload()["simulated"] is True


def test_set_brightness_and_zero_turns_off():
    service, _ = make()
    assert run(service.execute("set_brightness", {**LIGHT, "level": 40})).reported.brightness == 40
    r = run(service.execute("set_brightness", {**LIGHT, "level": 0}))
    assert r.ok and not r.reported.on


def test_llm_float_integers_are_accepted():
    service, _ = make()
    assert run(service.execute("set_brightness", {**LIGHT, "level": 30.0})).ok


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("set_brightness", {**LIGHT, "level": 150}),
        ("set_brightness", {**LIGHT, "level": -1}),
        ("set_brightness", {**LIGHT, "level": True}),
        ("set_brightness", {**LIGHT, "level": "50"}),
        ("set_brightness", {**LIGHT, "level": 30.5}),
        ("set_brightness", {**LIGHT}),
        ("turn_light_on", {"device_id": "bedroom_light"}),
        ("turn_light_on", {**LIGHT, "pin": 13}),
        ("set_gpio", {"pin": 13}),
        ("adjust_brightness", {**LIGHT, "delta": 0}),
        ("adjust_brightness", {**LIGHT, "delta": 90}),
    ],
)
def test_invalid_commands_are_rejected_before_the_device(tool, args):
    service, _ = make()
    r = run(service.execute(tool, args))
    assert r.status is CommandStatus.REJECTED
    assert r.error and r.command_id is None


def test_adjust_is_relative_to_reported_state_and_clamped():
    service, _ = make()
    run(service.execute("set_brightness", {**LIGHT, "level": 25}))
    assert run(service.execute("adjust_brightness", {**LIGHT, "delta": -20})).reported.brightness == 10
    assert run(service.execute("adjust_brightness", {**LIGHT, "delta": 50})).reported.brightness == 60


def test_adjust_down_while_off_is_rejected():
    service, _ = make()
    r = run(service.execute("adjust_brightness", {**LIGHT, "delta": -20}))
    assert r.status is CommandStatus.REJECTED


def test_offline_device_fails():
    service, _ = make("offline")
    assert run(service.execute("turn_light_on", LIGHT)).status is CommandStatus.FAILED


def test_timeout_is_reported_as_unknown_not_success():
    service, _ = make("timeout")
    r = run(service.execute("turn_light_on", LIGHT))
    assert r.status is CommandStatus.TIMEOUT
    assert r.reported is None
    assert "note_timeout" in r.to_tool_payload()


def test_mismatch_is_a_failure():
    service, _ = make("mismatch")
    r = run(service.execute("turn_light_on", LIGHT))
    assert r.status is CommandStatus.FAILED and not r.reported.on


def test_rate_limit():
    service, _ = make(max_commands_per_s=2)
    statuses = [run(service.execute("turn_light_on", LIGHT)).status for _ in range(3)]
    assert statuses[-1] is CommandStatus.REJECTED


def test_status_query_does_not_change_state():
    service, _ = make()
    r = run(service.execute("get_device_status", LIGHT))
    assert r.ok and r.command_id is None and not r.reported.on
