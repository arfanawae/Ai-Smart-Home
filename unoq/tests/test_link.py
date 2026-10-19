"""The board's main.py Link against the real Node relay, with a simulated MCU."""

import json
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "app" / "python")]
from main import Link, parse_reading  # noqa: E402
from relay_harness import relay_available, start_relay  # noqa: E402
from simulate import FakeMcu  # noqa: E402

pytestmark = pytest.mark.skipif(not relay_available(), reason="needs node + `npm install` in relay/")
TOKEN = secrets.token_urlsafe(18)


def api(url, method, path, body=None, token=TOKEN):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


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
    while api(relay, "GET", "/api/v1/control/status")[1].get("online") is not True:
        assert time.time() < deadline, "simulated board never connected"
        time.sleep(0.05)
    yield relay, mcu
    stop.set()


def test_parse_reading():
    assert parse_reading("1,73") == (True, 73) and parse_reading(" 0,100\n") == (False, 100)
    for bad in ("", "2,50", "1,101", "on", None):
        with pytest.raises(ValueError):
            parse_reading(bad)


def test_every_endpoint_needs_the_token(relay):
    for method, path in (("GET", "/api/v1/mcu/poll"), ("POST", "/api/v1/mcu/report"),
                         ("POST", "/api/v1/control/led"), ("GET", "/api/v1/control/status")):
        assert api(relay, method, path, {}, token="wrong-token-0123456789")[0] == 401


def test_command_round_trip_reports_the_mcu_state(board):
    url, mcu = board
    status, body = api(url, "POST", "/api/v1/control/led?wait=3000", {"on": True, "brightness": 60})
    assert status == 200 and (body["on"], body["brightness"]) == (True, 60)
    assert (mcu.on, mcu.level) == (True, 60)
    status, body = api(url, "POST", "/api/v1/control/led?wait=3000", {"on": False})
    assert status == 200 and body["on"] is False and body["brightness"] == 60  # level kept while off


def test_queue_without_wait_is_picked_up(board):
    url, mcu = board
    status, body = api(url, "POST", "/api/v1/control/led", {"value": 25})
    assert status == 202 and body["queued"]
    deadline = time.time() + 3
    while api(url, "GET", "/api/v1/control/status")[1]["brightness"] != 25:  # the board's report
        assert time.time() < deadline
        time.sleep(0.05)
    assert (mcu.on, mcu.level) == (True, 25)


def test_invalid_values_are_rejected(relay):
    for body in ({"on": True, "brightness": 101}, {"value": -1}, {"on": "yes"}, {"value": 1.5}, {}):
        assert api(relay, "POST", "/api/v1/control/led", body)[0] == 400


def test_waiting_with_no_board_is_refused_and_not_queued(relay):
    status, body = api(relay, "POST", "/api/v1/control/led?wait=1000", {"on": True})
    assert status == 503 and body["pending"] is None


def test_mcu_failure_is_reported_as_an_error(board):
    url, mcu = board
    mcu.fail = "error"
    status, body = api(url, "POST", "/api/v1/control/led?wait=3000", {"on": True})
    assert status == 502 and "simulated MCU failure" in body["error"]


def test_hung_mcu_never_stops_the_link():
    calls = []

    def bridge(*args):
        calls.append(args)
        time.sleep(5)  # the MCU never answers

    link = Link("http://127.0.0.1:9", TOKEN, bridge, log=lambda _m: None, mcu_timeout_s=0.2)
    started = time.time()
    first = link._call_mcu("set_led", "50")
    second = link._call_mcu("set_led", "0")  # while the first call is still stuck
    assert time.time() - started < 1.0
    assert first["ok"] is False and "did not answer" in first["error"]
    assert second["ok"] is False and "Stop" in second["error"] and len(calls) == 1


def test_stale_command_is_not_replayed_after_the_board_hangs(relay):
    status, body = api(relay, "POST", "/api/v1/control/led", {"on": True})
    assert status == 202
    assert api(relay, "GET", "/api/v1/mcu/poll")[1]["command"]["value"] == 100  # taken, never reported
    time.sleep(5.5)  # the board "hangs" past the command lifetime, then comes back
    assert api(relay, "GET", "/api/v1/mcu/poll")[1]["command"] is None
    assert api(relay, "GET", "/api/v1/control/status")[1]["pending"] is None


def test_long_poll_delivers_a_command_the_moment_it_is_queued(relay):
    got = {}

    def poll():
        started = time.time()
        got["body"] = api(relay, "GET", "/api/v1/mcu/poll?wait=3000")[1]
        got["after"] = time.time() - started

    worker = threading.Thread(target=poll)
    worker.start()
    time.sleep(0.3)
    assert api(relay, "GET", "/api/v1/control/status")[1]["online"]  # an open poll is the heartbeat
    api(relay, "POST", "/api/v1/control/led", {"value": 40})
    worker.join(5)
    assert got["body"]["command"]["value"] == 40 and got["after"] < 1.0


def test_long_poll_with_nothing_to_do_returns_empty(relay):
    started = time.time()
    assert api(relay, "GET", "/api/v1/mcu/poll?wait=400")[1] == {"command": None}
    assert 0.35 < time.time() - started < 2
