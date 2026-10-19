"""Test helper: start the real Node relay (relay/server.js) on a free port."""

import os
import shutil
import subprocess
from pathlib import Path

RELAY_DIR = Path(__file__).resolve().parents[1] / "relay"


def relay_available():
    return shutil.which("node") is not None and (RELAY_DIR / "node_modules" / "express").is_dir()


def start_relay(token):
    """→ (process, base_url). The caller terminates the process."""
    env = {**os.environ, "NEXUS_TOKEN": token, "RELAY_PORT": "0", "RELAY_HOST": "127.0.0.1"}
    proc = subprocess.Popen(
        ["node", "server.js"], cwd=RELAY_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
    )
    line = proc.stdout.readline()  # "NEXUS relay listening on http://127.0.0.1:<port>"
    if "listening on" not in line:
        proc.kill()
        raise RuntimeError("relay did not start: %r" % line)
    # keep draining its log so the pipe never fills up
    import threading
    threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()
    return proc, line.split("listening on ", 1)[1].strip()
