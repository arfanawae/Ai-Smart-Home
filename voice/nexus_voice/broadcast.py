"""WebSocket feed of NEXUS events for the web UI (Blueprint §12.4, M1 step 1.7 / M2).

Bound to 127.0.0.1 so only this laptop can connect. Opening it to the LAN needs HTTPS + login
first (Blueprint §12.7).

Server → client: one JSON object per message — nexus.events.to_dict(event) plus "ts". A new client
first gets a "hello" with the current AI state and the last known device state, so it can draw the
right picture immediately. "level" messages (mic / TTS loudness, ~16 per second) drive the waveform.

Client → server (validated here, then handed to `on_message`):
    {"type": "text", "text": "เปิดไฟ"}                       a typed message to NEXUS
    {"type": "command", "tool": "...", "args": {...}}       manual control (DeviceService validates again)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from nexus.events import AIState, CommandUpdate, Event, StateChanged, to_dict
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
CLIENT_QUEUE_SIZE = 200  # a stalled client loses its oldest messages instead of blocking NEXUS
MAX_TEXT_LENGTH = 500
MAX_MESSAGE_BYTES = 4096

ClientMessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class InvalidMessage(ValueError):
    pass


def parse_client_message(raw: str | bytes) -> dict[str, Any]:
    """Accept only the two message shapes the UI is allowed to send."""
    if isinstance(raw, bytes) or len(raw) > MAX_MESSAGE_BYTES:
        raise InvalidMessage("message must be a short JSON text frame")
    try:
        message = json.loads(raw)
    except ValueError as e:
        raise InvalidMessage("invalid JSON") from e
    if not isinstance(message, dict):
        raise InvalidMessage("message must be a JSON object")
    match message.get("type"):
        case "text":
            text = message.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
                raise InvalidMessage(f"text must be 1–{MAX_TEXT_LENGTH} characters")
            return {"type": "text", "text": text.strip()}
        case "command":
            tool, args = message.get("tool"), message.get("args", {})
            if not isinstance(tool, str) or not isinstance(args, dict):
                raise InvalidMessage("command needs a tool name and an args object")
            return {"type": "command", "tool": tool, "args": args}
    raise InvalidMessage("type must be 'text' or 'command'")


class EventBroadcaster:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        on_message: ClientMessageHandler | None = None,
        **hello: Any,
    ) -> None:
        self._host, self._port = host, port
        self._on_message = on_message
        self._hello = hello  # static facts for the UI, e.g. simulated=True, llm="gemini-…"
        self._clients: dict[ServerConnection, asyncio.Queue[str]] = {}
        self._state: dict[str, Any] = to_dict(StateChanged(AIState.IDLE))
        self._devices: dict[str, dict[str, Any]] = {}
        self._server: Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()

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
        self._broadcast(json.dumps(message, ensure_ascii=False))

    def publish_level(self, rms: float, source: str) -> None:
        """Loudness 0–1 of the mic ("mic") or of NEXUS's own voice ("tts"). Not kept in the snapshot."""
        if self._clients:
            self._broadcast(json.dumps({"type": "level", "rms": round(min(max(rms, 0.0), 1.0), 3), "source": source}))

    def _broadcast(self, payload: str) -> None:
        for queue in self._clients.values():
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(payload)

    def _hello_message(self) -> str:
        hello = {"type": "hello", **self._hello, "state": self._state, "devices": self._devices}
        return json.dumps(hello, ensure_ascii=False)

    async def _read(self, ws: ServerConnection, queue: asyncio.Queue[str]) -> None:
        async for raw in ws:
            try:
                message = parse_client_message(raw)
            except InvalidMessage as e:
                queue.put_nowait(json.dumps({"type": "error", "message": str(e)}))
                continue
            if self._on_message is None:
                queue.put_nowait(json.dumps({"type": "error", "message": "this server is read-only"}))
                continue
            # Run in the background so a long Gemini turn never stops us reading the socket.
            task = asyncio.create_task(self._on_message(message))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _handle(self, ws: ServerConnection) -> None:
        queue: asyncio.Queue[str] = asyncio.Queue(CLIENT_QUEUE_SIZE)
        queue.put_nowait(self._hello_message())
        self._clients[ws] = queue
        reader = asyncio.ensure_future(self._read(ws, queue))  # finishes when the client disconnects
        try:
            while True:
                getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
                if reader in done:  # notice a disconnect even when no events are flowing
                    getter.cancel()
                    break
                await ws.send(getter.result())
        except ConnectionClosed:
            pass
        finally:
            reader.cancel()
            self._clients.pop(ws, None)
