"""A simulated UNO Q on the laptop: runs the board's real main.py Link against a fake MCU.

    python unoq/simulate.py --token-file relay/.env      (or --token <TOKEN>)

It polls the relay exactly like the board does, so NEXUS → relay → "board" can be tested without
hardware. Use --fail error|hang|mismatch to see how NEXUS reports a misbehaving MCU.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app" / "python"))
from main import Link  # noqa: E402


class FakeMcu:
    """Behaves like sketch.ino: set_led("<0-100>") / get_led() → "on,brightness"."""

    def __init__(self):
        self.on, self.level, self.fail = False, 100, None

    def call(self, function, *args):
        if self.fail == "error":
            raise RuntimeError("simulated MCU failure")
        if self.fail == "hang":
            time.sleep(30)
        if function == "set_led":
            value = max(0, min(100, int(args[0])))
            if self.fail != "mismatch":  # mismatch: replies, but the light does not change
                self.on = value > 0
                self.level = value if value else self.level
        elif function != "get_led":
            raise RuntimeError("unknown function %s" % function)
        return "%d,%d" % (self.on, self.level)


def token_from(args):
    if args.token:
        return args.token
    for line in Path(args.token_file).read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("NEXUS_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("NEXUS_TOKEN not found in %s" % args.token_file)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8770")
    parser.add_argument("--token")
    parser.add_argument("--token-file", default=str(Path(__file__).resolve().parents[1] / "relay" / ".env"))
    parser.add_argument("--fail", choices=("error", "hang", "mismatch"))
    args = parser.parse_args()
    mcu = FakeMcu()
    mcu.fail = args.fail
    link = Link(args.url, token_from(args), mcu.call, log=lambda m: print("[sim board] " + m, flush=True))
    print("[sim board] polling %s (Ctrl+C to stop)" % args.url, flush=True)
    try:
        while True:
            time.sleep(link.step())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
