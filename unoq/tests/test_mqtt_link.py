"""Board MQTT link without a network: wire format, command checks, one state message per change."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "app" / "python")]
from main import Link  # noqa: E402
from mqtt_link import BoardMqtt, _varint, command_value, connect_packet, parse_publish, publish_packet  # noqa: E402
from simulate import FakeMcu  # noqa: E402

CONFIG = {"host": "x", "port": 1, "tls": False, "topic": "nexus-test-0123456789abcdef"}


def test_remaining_length_encoding():
    assert _varint(0) == b"\x00" and _varint(127) == b"\x7f" and _varint(128) == b"\x80\x01" and _varint(16383) == b"\xff\x7f"


def test_publish_round_trip_and_retain_flag():
    pkt = publish_packet("a/b", b'{"x":1}', retain=True)
    assert pkt[0] == 0x31
    assert parse_publish(pkt[0], pkt[2:]) == ("a/b", b'{"x":1}', True)


def test_connect_carries_a_retained_last_will():
    pkt = connect_packet("cid", 30, "t/state", b'{"online": false}')
    assert b"MQTT" in pkt and b"t/state" in pkt and pkt[9] & 0x24 == 0x24  # will flag + will retain


def test_command_validation():
    assert command_value(b'{"state": true, "value": 60}') == 60
    assert command_value(b'{"state": false, "value": 99}') == 0
    for bad in (b'{"state": true, "value": 101}', b'{"state": true, "value": 0}', b'{"state": "on"}',
                b'{"state": true, "value": true}', b"not json", b"[]", b"x" * 300):
        assert command_value(bad) is None, bad


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data)


def board():
    mcu = FakeMcu()
    link = Link("http://127.0.0.1:9", "x" * 16, mcu.call, log=lambda _m: None, mcu_timeout_s=0.5)
    b = BoardMqtt(CONFIG, link, log=lambda _m: None)
    b.client = Recorder()
    return b, link, mcu


def states(b):
    return [json.loads(parse_publish(p[0], p[2:])[1]) for p in b.client.sent]


def test_one_state_message_per_command_and_for_relay_changes():
    b, link, mcu = board()
    b.handle(CONFIG["topic"] + "/led", b'{"state": true, "value": 40}', False)
    assert (mcu.on, mcu.level) == (True, 40) and states(b) == [{"online": True, "state": True, "value": 40, "mcu_ok": True}]
    link.call_mcu("set_led", "0")  # a command that came through the relay (voice)
    assert states(b)[-1]["state"] is False and len(states(b)) == 2


def test_retained_and_foreign_messages_are_never_applied():
    b, _, mcu = board()
    b.handle(CONFIG["topic"] + "/led", b'{"state": true, "value": 90}', True)  # an old retained command
    b.handle("someone/else/led", b'{"state": true, "value": 90}', False)
    assert mcu.on is False and b.client.sent == []


def test_flood_is_rate_limited():
    b, _, _ = board()
    for i in range(40):
        b.handle(CONFIG["topic"] + "/led", json.dumps({"state": True, "value": 1 + i}).encode(), False)
    assert len(states(b)) == 15
