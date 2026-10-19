"""NEXUS link — Arduino App Lab entry point on the UNO Q (Linux side).

The board's container blocks inbound connections, so this side only makes outbound requests to the
NEXUS relay on the laptop (relay/server.js):

    long poll      GET  /api/v1/mcu/poll?wait=1000 → {"command": {"id", "action": "set_led", "value": 0-100} | null}
                   (the relay holds it until a command is queued, so commands arrive almost instantly)
    on a command   Bridge.call("set_led", "<value>") → MCU replies "on,brightness", e.g. "1,73"
    then           POST /api/v1/mcu/report   ← {"id", "ok", "on", "brightness"}  (or "ok": false, "error")

On (re)connect it also reports the MCU's current state (get_led), so the laptop never guesses.

Set LAPTOP_URL below. The token is read from token.txt next to this file — create it on the board
and never commit it. Standard library only (urllib), so nothing has to be installed on the board.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request

LAPTOP_URL = "http://172.20.10.3:8770"  # ← the laptop's IP on the same network as the board
POLL_WAIT_MS = 1000  # how long the relay may hold a poll open; also the heartbeat period
POLL_INTERVAL_S = 0.3  # pause between polls if the relay answers at once (older relay, no long poll)
RETRY_INTERVAL_S = 1.0  # while the relay is unreachable
HTTP_TIMEOUT_S = 2.0
MCU_TIMEOUT_S = 1.5  # a Bridge call that takes longer is reported as an MCU failure, never waited on

HERE = os.path.dirname(os.path.abspath(__file__))


def read_token(path=os.path.join(HERE, "token.txt")):
    with open(path, encoding="utf-8") as f:
        token = f.read().strip()
    if len(token) < 16:
        raise SystemExit("token.txt must hold the same token as the relay (at least 16 characters)")
    return token


def parse_reading(reply):
    """MCU reply "on,brightness" (e.g. "1,73") → (True, 73). Anything else raises ValueError."""
    on, level = str(reply).strip().split(",")
    on, level = int(on), int(level)
    if on not in (0, 1) or not 0 <= level <= 100:
        raise ValueError("MCU reply out of range: %r" % (reply,))
    return on == 1, level


class Link:
    """One poll → command → report cycle per step(); `bridge_call` is Bridge.call on the board."""

    def __init__(self, base_url, token, bridge_call, log=print, mcu_timeout_s=MCU_TIMEOUT_S):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.bridge_call = bridge_call
        self.log = log
        self.connected = False
        self._last_problem = None
        self.mcu_timeout_s = mcu_timeout_s
        self._stuck = None  # a Bridge call that never returned

    def _say(self, message):
        try:  # a console that cannot print a character must never look like a network failure
            self.log(message)
        except Exception:
            pass

    def _problem(self, message):
        if message != self._last_problem:  # log each new problem once, not every retry
            self._say(message)
        self._last_problem = message

    def _request(self, method, path, body=None, timeout=HTTP_TIMEOUT_S):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Authorization", "Bearer " + self.token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")

    def _call_mcu(self, function, *args):
        """→ report body for the relay. MCU failures are reported, never hidden.

        The call runs in a helper thread with a deadline, so a hung MCU can never stop this loop:
        polling (the relay's heartbeat) continues and every command gets an honest error report.
        """
        if self._stuck is not None:
            if self._stuck.is_alive():
                return {"ok": False, "error": "MCU not answering (still stuck): press Stop, then Run in App Lab"}
            self._stuck = None
        box = {}

        def call():
            try:
                box["reply"] = self.bridge_call(function, *args)
            except Exception as e:  # Bridge errors
                box["error"] = "%s: %s" % (type(e).__name__, e)

        worker = threading.Thread(target=call, name="mcu-call", daemon=True)
        worker.start()
        worker.join(self.mcu_timeout_s)
        if worker.is_alive():
            self._stuck = worker
            self._problem("MCU did not answer %s within %.1f s" % (function, self.mcu_timeout_s))
            return {"ok": False, "error": "MCU did not answer within %.1f s" % self.mcu_timeout_s}
        if "error" in box:
            return {"ok": False, "error": box["error"]}
        try:
            on, level = parse_reading(box.get("reply"))
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": "bad MCU reply %r (%s)" % (box.get("reply"), e)}
        return {"ok": True, "on": on, "brightness": level}

    def _execute(self, command):
        try:
            value = int(command.get("value"))
        except (TypeError, ValueError):
            value = -1
        if command.get("action", "set_led") != "set_led" or not 0 <= value <= 100:
            report = {"ok": False, "error": "rejected command %r" % (command,)}
        else:
            report = self._call_mcu("set_led", str(value))
        report["id"] = command.get("id")
        self._request("POST", "/api/v1/mcu/report", report)
        self._say("command %s: set_led %s -> %s" % (command.get("id"), value, report))

    def step(self):
        """Run once; returns how long to sleep before the next step."""
        try:
            if not self.connected:
                self._request("POST", "/api/v1/mcu/report", self._call_mcu("get_led"))
                self.connected = True
                self._last_problem = None
                self._say("connected to NEXUS relay at %s" % self.base_url)
            started = time.monotonic()
            command = self._request(
                "GET", "/api/v1/mcu/poll?wait=%d" % POLL_WAIT_MS, timeout=HTTP_TIMEOUT_S + POLL_WAIT_MS / 1000
            ).get("command")
            if command:
                self._execute(command)
                return 0  # poll again right away: the next slider value may already be waiting
            held = time.monotonic() - started >= POLL_WAIT_MS / 2000
            return 0 if held else POLL_INTERVAL_S
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self._problem("relay rejected the token: token.txt must match NEXUS_TOKEN in relay/.env")
            else:
                self._problem("relay replied HTTP %d" % e.code)
        except (urllib.error.URLError, OSError, ValueError) as e:
            self._problem("cannot reach the relay at %s (%s), retrying" % (self.base_url, e))
        self.connected = False
        return RETRY_INTERVAL_S


if __name__ == "__main__":
    from arduino.app_utils import App, Bridge  # provided by App Lab on the UNO Q

    link = Link(LAPTOP_URL, read_token(), Bridge.call)
    print("NEXUS link: long-polling %s" % LAPTOP_URL)

    def loop():
        time.sleep(link.step())

    App.run(user_loop=loop)  # App Lab calls loop() repeatedly and keeps the Bridge alive
