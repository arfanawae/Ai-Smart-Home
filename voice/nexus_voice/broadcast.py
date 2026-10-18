"""WebSocket feed of NEXUS events for the web UI (Blueprint §12.4, M1 step 1.7).

Read-only for now, and bound to 127.0.0.1 so only this laptop can connect. Opening it to the LAN
needs HTTPS + login first (Blueprint §12.7).

Every message is one JSON object: nexus.events.to_dict(event) plus "ts". A new client first gets a
"hello" message with the current AI state and the last known device state, so it can draw the right
picture immediately instead of waiting for the next event.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from nexus.events import AIState, CommandUpdate, Event, StateChanged, to_dict
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
CLIENT_QUEUE_SIZE = 200  # a stalled client loses its oldest messages instead of blocking NEXUS


class EventBroadcaster:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, **hello: Any) -> None:
        self._host, self._port = host, port
        self._hello = hello  # static facts for the UI, e.g. simulated=True, llm="gemini-…"
        self._clients: dict[ServerConnection, asyncio.Queue[str]] = {}
        self._state: dict[str, Any] = to_dict(StateChanged(AIState.IDLE))
        self._devices: dict[str, dict[str, Any]] = {}
        self._server: Server | None = None

    @property
    def port(self) -> int:
        """The bound port (useful when started with port=0 in tests)."""
        if self._server is None:
            return self._port
        return next(iter(self._server.sockets)).getsockname()[1]

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        self._server = await serve(self._handle, self._host, self._port)

    async def stop(self, flush_timeout_s: float = 1.0) -> None:
        deadline = asyncio.get_running_loop().time() + flush_timeout_s
        while any(not q.empty() for q in self._clients.values()) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)  # let clients receive the last events (e.g. the final IDLE)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def set_device(self, device_id: str, state: dict[str, Any]) -> None:
        self._devices[device_id] = state

    def publish(self, event: Event) -> None:
        """Call from the asyncio thread. Never blocks: messages are queued per client."""
        message = {**to_dict(event), "ts": datetime.now().isoformat(timespec="milliseconds")}
        if isinstance(event, StateChanged):
            self._state = message
        elif isinstance(event, CommandUpdate) and event.result.reported and event.result.device_id:
            self._devices[event.result.device_id] = event.result.reported.to_dict()
        payload = json.dumps(message, ensure_ascii=False)
        for queue in self._clients.values():
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(payload)

    def _hello_message(self) -> str:
        hello = {"type": "hello", **self._hello, "state": self._state, "devices": self._devices}
        return json.dumps(hello, ensure_ascii=False)

    async def _handle(self, ws: ServerConnection) -> None:
        queue: asyncio.Queue[str] = asyncio.Queue(CLIENT_QUEUE_SIZE)
        queue.put_nowait(self._hello_message())
        self._clients[ws] = queue
        closed = asyncio.ensure_future(ws.wait_closed())
        try:
            while True:
                getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({getter, closed}, return_when=asyncio.FIRST_COMPLETED)
                if closed in done:  # notice a disconnect even when no events are flowing
                    getter.cancel()
                    break
                await ws.send(getter.result())
        except ConnectionClosed:
            pass
        finally:
            closed.cancel()
            self._clients.pop(ws, None)
