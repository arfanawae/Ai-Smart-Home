"""MQTT on the UNO Q (Linux side): light commands from the web dashboard through a broker.

Outbound only (like the relay link), so the board's firewall is not in the way. Standard library only:
a minimal MQTT 3.1.1 client (QoS 0, TLS) is enough for one topic in and one out.

    <topic>/led    ← {"state": true, "value": 60}   (web app → board)   state false = off
    <topic>/state  → {"online": true, "state": true, "value": 60, "mcu_ok": true}   retained
                     {"online": false}  — the broker publishes this "last will" if the board drops

Config: mqtt.json next to this file (never commit): {"host", "port", "tls", "topic"}.
The public test broker has no password: the long random topic is the only protection, so this is for
trying things out with the on-board LED — not for a mains-powered light.
"""

import json
import os
import socket
import ssl
import struct
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
KEEPALIVE_S = 30
MAX_COMMANDS_PER_S = 15
MAX_PAYLOAD = 256


def load_config(path=os.path.join(HERE, "mqtt.json")):
    """→ dict or None when MQTT is not configured."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    topic = str(config.get("topic", ""))
    if len(topic) < 16 or any(ch in topic for ch in "#+/ "):
        raise SystemExit("mqtt.json: topic must be a long random name without / # + or spaces")
    return {"host": config.get("host", "broker.emqx.io"), "port": int(config.get("port", 8883)), "tls": bool(config.get("tls", True)), "topic": topic}


# ── MQTT 3.1.1 wire format ────────────────────────────────────────────────────────────────────────
def _varint(n):
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _string(s):
    data = s.encode("utf-8") if isinstance(s, str) else s
    return struct.pack("!H", len(data)) + data


def packet(kind, body=b""):
    return bytes([kind]) + _varint(len(body)) + body


def connect_packet(client_id, keepalive, will_topic=None, will_payload=b"", will_retain=True):
    flags = 0x02  # clean session
    payload = _string(client_id)
    if will_topic:
        flags |= 0x04 | (0x20 if will_retain else 0)  # will flag, QoS 0, retain
        payload += _string(will_topic) + _string(will_payload)
    return packet(0x10, _string("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive) + payload)


def publish_packet(topic, payload, retain=False):
    return packet(0x30 | (0x01 if retain else 0), _string(topic) + payload)


def subscribe_packet(packet_id, topic):
    return packet(0x82, struct.pack("!H", packet_id) + _string(topic) + b"\x00")


def parse_publish(first_byte, body):
    """→ (topic, payload, retained) for a QoS 0 PUBLISH body."""
    (length,) = struct.unpack("!H", body[:2])
    topic = body[2 : 2 + length].decode("utf-8", "replace")
    offset = 2 + length
    if (first_byte >> 1) & 0x03:  # QoS 1/2 carry a packet id (we subscribe at QoS 0, but be safe)
        offset += 2
    return topic, body[offset:], bool(first_byte & 0x01)


class MqttClient:
    def __init__(self, host, port, tls, client_id, will_topic=None, will_payload=b""):
        self.host, self.port, self.tls = host, port, tls
        self.client_id = client_id
        self.will = (will_topic, will_payload)
        self.sock = None
        self._send_lock = threading.Lock()

    def connect(self, timeout=10):
        raw = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host) if self.tls else raw
        self.send(connect_packet(self.client_id, KEEPALIVE_S, *self.will))
        kind, body = self.read_packet()
        if kind >> 4 != 2 or len(body) < 2 or body[1] != 0:
            raise ConnectionError("broker refused the connection (CONNACK %r)" % (body,))
        self.sock.settimeout(1.0)  # read loop wakes up every second (keepalive, stop flag)

    def send(self, data):
        with self._send_lock:
            self.sock.sendall(data)

    def _read_exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("broker closed the connection")
            data += chunk
        return data

    def read_packet(self):
        first = self._read_exact(1)[0]
        length, shift = 0, 0
        while True:
            byte = self._read_exact(1)[0]
            length += (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        return first, self._read_exact(length) if length else b""

    def close(self):
        try:
            self.send(packet(0xE0))
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


# ── the board side ────────────────────────────────────────────────────────────────────────────
def command_value(payload):
    """{"state": bool, "value": 0-100} → set_led value (0 = off), or None if invalid."""
    if len(payload) > MAX_PAYLOAD:
        return None
    try:
        message = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(message, dict) or not isinstance(message.get("state"), bool):
        return None
    if not message["state"]:
        return 0
    value = message.get("value", 100)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        return None  # never guess "full on" from a bad value
    return value


class BoardMqtt:
    """Runs in its own thread next to the relay link; both drive the MCU through the same Link."""

    def __init__(self, config, link, log=print):
        self.topic = config["topic"]
        self.config = config
        self.link = link
        self.log = log
        self.client = None
        self._stop = threading.Event()
        self._recent = []
        self.last_state = None
        # Every set_led — from MQTT or from the relay — publishes the state once, through this hook.
        link.on_report = self.publish_state

    def state_payload(self, report):
        if not report.get("ok"):
            return json.dumps({"online": True, "mcu_ok": False, "error": report.get("error", "")[:120]}).encode()
        return json.dumps({"online": True, "state": report["on"], "value": report["brightness"], "mcu_ok": True}).encode()

    def publish_state(self, report):
        """Called after every MCU call, from MQTT or from the relay — the web app always sees the truth."""
        self.last_state = self.state_payload(report)
        client = self.client
        if client is not None:
            try:
                client.send(publish_packet(self.topic + "/state", self.last_state, retain=True))
            except Exception:
                pass  # the read loop notices the broken connection and reconnects

    def _rate_ok(self):
        now = time.monotonic()
        self._recent = [t for t in self._recent if now - t < 1.0]
        if len(self._recent) >= MAX_COMMANDS_PER_S:
            return False
        self._recent.append(now)
        return True

    def handle(self, topic, payload, retained):
        if topic != self.topic + "/led" or retained:
            return  # a retained command is an old one: never replay it on (re)connect
        value = command_value(payload)
        if value is None or not self._rate_ok():
            self.log("mqtt: ignored command %r" % (payload[:80],))
            return
        report = self.link.call_mcu("set_led", str(value))  # publishes the new state via on_report
        self.log("mqtt: set_led %d -> %s" % (value, report))

    def run_once(self):
        offline = json.dumps({"online": False}).encode()
        client = MqttClient(self.config["host"], self.config["port"], self.config["tls"],
                            "nexus-unoq-" + uuid.uuid4().hex[:10], self.topic + "/state", offline)
        client.connect()
        self.client = client
        client.send(subscribe_packet(1, self.topic + "/led"))
        self.publish_state(self.link.call_mcu("get_led"))
        self.log("mqtt: connected to %s:%d" % (self.config["host"], self.config["port"]))
        last_ping = time.monotonic()
        try:
            while not self._stop.is_set():
                if time.monotonic() - last_ping > KEEPALIVE_S / 2:
                    client.send(packet(0xC0))  # PINGREQ
                    last_ping = time.monotonic()
                try:
                    first, body = client.read_packet()
                except socket.timeout:
                    continue
                if first >> 4 == 3:
                    self.handle(*parse_publish(first, body))
        finally:
            self.client = None
            client.close()

    def run_forever(self):
        delay = 1.0
        while not self._stop.is_set():
            try:
                self.run_once()
                delay = 1.0
            except Exception as e:
                self.log("mqtt: %s — reconnecting in %.0f s" % (e, delay))
                self._stop.wait(delay)
                delay = min(delay * 2, 30.0)

    def start(self):
        threading.Thread(target=self.run_forever, name="nexus-mqtt", daemon=True).start()

    def stop(self):
        self._stop.set()
