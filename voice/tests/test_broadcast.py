import asyncio
import json
from datetime import datetime

from nexus.devices.models import CommandResult, CommandStatus, LightState
from nexus.events import AIState, AssistantReply, CommandUpdate, StateChanged, UserMessage
from websockets.asyncio.client import connect

from nexus_voice.broadcast import EventBroadcaster


def light(on, brightness):
    return LightState(online=True, on=on, brightness=brightness, updated_at=datetime(2026, 10, 19), source="mock")


async def recv(ws):
    return json.loads(await asyncio.wait_for(ws.recv(), 2))


def run(coro):
    return asyncio.run(coro)


def test_hello_snapshot_then_events_in_order():
    async def go():
        hub = EventBroadcaster(port=0, simulated=True, llm="gemini-flash-lite-latest")
        hub.set_device("main_light", light(False, 100).to_dict())
        await hub.start()
        hub.publish(StateChanged(AIState.LISTENING))  # before the client connects → goes into the snapshot
        async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
            hello = await recv(ws)
            assert hello["type"] == "hello" and hello["simulated"] is True
            assert hello["state"]["state"] == "LISTENING"
            assert hello["devices"]["main_light"]["on"] is False

            result = CommandResult(
                "turn_light_on", {"device_id": "main_light"}, CommandStatus.SUCCEEDED, True,
                command_id="abc", previous=light(False, 100), reported=light(True, 100), device_id="main_light",
            )
            hub.publish(UserMessage("เปิดไฟ", "voice", "th-TH"))
            hub.publish(StateChanged(AIState.EXECUTING, "turn_light_on"))
            hub.publish(CommandUpdate(result, "fastpath"))
            hub.publish(AssistantReply("จำลองการเปิดไฟแล้วครับ", "fastpath"))
            types = [(await recv(ws))["type"] for _ in range(4)]
            assert types == ["user", "state", "command", "reply"]
        await hub.stop()
        return hub

    run(go())


def test_thai_text_is_sent_unescaped():
    async def go():
        hub = EventBroadcaster(port=0)
        await hub.start()
        async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
            await recv(ws)
            hub.publish(AssistantReply("สวัสดีครับ", "llm"))
            raw = await asyncio.wait_for(ws.recv(), 2)
            assert "สวัสดีครับ" in raw
        await hub.stop()

    run(go())


def test_device_snapshot_follows_commands_for_late_joiners():
    async def go():
        hub = EventBroadcaster(port=0)
        await hub.start()
        result = CommandResult(
            "set_brightness", {"device_id": "main_light", "level": 30}, CommandStatus.SUCCEEDED, True,
            reported=light(True, 30), device_id="main_light",
        )
        hub.publish(CommandUpdate(result, "llm"))
        async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
            assert (await recv(ws))["devices"]["main_light"]["brightness"] == 30
        await hub.stop()

    run(go())


def test_disconnected_client_is_forgotten():
    async def go():
        hub = EventBroadcaster(port=0)
        await hub.start()
        async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
            await recv(ws)
            assert hub.client_count == 1
        for _ in range(50):  # no events are flowing; the server must still notice the close
            if hub.client_count == 0:
                break
            await asyncio.sleep(0.02)
        assert hub.client_count == 0
        hub.publish(StateChanged(AIState.IDLE))  # publishing with no clients is fine
        await hub.stop()

    run(go())


def test_stop_delivers_queued_events_first():
    async def go():
        hub = EventBroadcaster(port=0)
        await hub.start()
        async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
            await recv(ws)
            hub.publish(StateChanged(AIState.SPEAKING))
            hub.publish(StateChanged(AIState.IDLE))
            await hub.stop()
            received = [json.loads(m)["state"] async for m in ws]
        assert received == ["SPEAKING", "IDLE"]

    run(go())


def test_listens_on_localhost_only_by_default():
    assert EventBroadcaster()._host == "127.0.0.1"


# ── client → server messages (M2) ───────────────────────────────────────────────────────────
import pytest

from nexus_voice.broadcast import InvalidMessage, parse_client_message


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"type": "text", "text": "  เปิดไฟ  "}', {"type": "text", "text": "เปิดไฟ"}),
        (
            '{"type": "command", "tool": "set_brightness", "args": {"device_id": "main_light", "level": 30}}',
            {"type": "command", "tool": "set_brightness", "args": {"device_id": "main_light", "level": 30}},
        ),
    ],
)
def test_parse_valid_messages(raw, expected):
    assert parse_client_message(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[1, 2]",
        '{"type": "shell", "cmd": "rm -rf /"}',
        '{"type": "text", "text": ""}',
        '{"type": "text", "text": 42}',
        '{"type": "text", "text": "' + "ก" * 501 + '"}',
        '{"type": "command", "tool": 5}',
        '{"type": "command", "tool": "turn_light_on", "args": "main_light"}',
        b'{"type": "text", "text": "hi"}',
    ],
)
def test_parse_rejects_everything_else(raw):
    with pytest.raises(InvalidMessage):
        parse_client_message(raw)


def test_client_messages_reach_the_handler_and_errors_go_back():
    async def go():
        received = []

        async def handler(message):
            received.append(message)

        hub = EventBroadcaster(port=0, on_message=handler)
        await hub.start()
        async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
            await recv(ws)
            await ws.send('{"type": "text", "text": "สวัสดี"}')
            await ws.send('{"type": "nope"}')
            error = await recv(ws)
            assert error["type"] == "error"
            for _ in range(50):
                if received:
                    break
                await asyncio.sleep(0.02)
        await hub.stop()
        assert received == [{"type": "text", "text": "สวัสดี"}]

    run(go())


def test_read_only_server_says_so():
    async def go():
        hub = EventBroadcaster(port=0)
        await hub.start()
        async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
            await recv(ws)
            await ws.send('{"type": "text", "text": "hi"}')
            assert (await recv(ws))["message"] == "this server is read-only"
        await hub.stop()

    run(go())


def test_levels_are_clamped_and_not_kept_for_late_joiners():
    async def go():
        hub = EventBroadcaster(port=0)
        await hub.start()
        async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
            await recv(ws)
            hub.publish_level(1.7, "mic")
            level = await recv(ws)
            assert level == {"type": "level", "rms": 1.0, "source": "mic"}
        async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
            assert "level" not in (await recv(ws))
        await hub.stop()

    run(go())
