"""Driver for the Arduino UNO Q, through the NEXUS relay on the laptop (relay/server.js).

The board polls the relay (its container blocks inbound connections), so this driver queues a
command with POST /api/v1/control/led?wait=… and gets the reply only after the board has reported
what the MCU applied. Used inside the MCP server (DEVICE_MODE=unoq); UNOQ_URL is the relay's URL.

Failure mapping, so DeviceService reports the truth:
    reached the board, no report (504) / no reply → TimeoutError  (retried once, then TIMEOUT: state unknown)
    relay down / 401 / board offline (503) / MCU error (502) / bad → DeviceUnavailable (FAILED, nothing changed)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from ..models import LightState
from .base import DeviceUnavailable

SUPPORTED_DEVICES = ("main_light",)
WAIT_MS = 2000  # how long the relay holds the reply for the board's report


class UnoQDriver:
    simulated = False

    def __init__(self, base_url: str, token: str, timeout_s: float = 2.5, wait_ms: int = WAIT_MS) -> None:
        if not base_url or not token:
            raise ValueError("UNOQ_URL and UNOQ_TOKEN are required for DEVICE_MODE=unoq")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_s,  # below DeviceService's 3 s, so its retry logic stays in charge
        )
        self._wait_ms = min(wait_ms, int(timeout_s * 1000) - 300)  # the relay answers before httpx gives up

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=body)
        except httpx.ConnectTimeout as e:
            # Never connected: the command did not reach the board, so its state is known (unchanged).
            raise DeviceUnavailable("เชื่อมต่อ NEXUS relay ไม่ได้ (หมดเวลาเชื่อมต่อ)") from e
        except httpx.TimeoutException as e:
            # Sent, but no reply: the board may or may not have acted — the state is unknown.
            raise TimeoutError("UNO Q ไม่ตอบกลับ") from e
        except httpx.HTTPError as e:
            raise DeviceUnavailable(f"เชื่อมต่อ NEXUS relay ไม่ได้ — เปิด relay แล้วหรือยัง ({type(e).__name__})") from e
        if response.status_code == 401:
            raise DeviceUnavailable("relay ปฏิเสธการเชื่อมต่อ — UNOQ_TOKEN ไม่ตรงกับ NEXUS_TOKEN ใน relay/.env")
        if response.status_code == 503:
            raise DeviceUnavailable(f"Arduino UNO Q ไม่ได้เชื่อมต่อกับ relay ({_error_of(response)})")
        if response.status_code == 502:
            raise DeviceUnavailable(f"ไมโครคอนโทรลเลอร์บน UNO Q ไม่ตอบ ({_error_of(response)})")
        if response.status_code == 504:
            raise TimeoutError("UNO Q รับคำสั่งแล้วแต่ไม่รายงานผล")
        if response.status_code != 200:
            raise DeviceUnavailable(f"UNO Q ตอบ HTTP {response.status_code}: {_error_of(response)}")
        try:
            return response.json()
        except ValueError as e:
            raise DeviceUnavailable("UNO Q ตอบข้อมูลที่อ่านไม่ได้") from e

    @staticmethod
    def _to_state(data: dict[str, Any]) -> LightState:
        if not data.get("online"):
            raise DeviceUnavailable("Arduino UNO Q ไม่ได้เชื่อมต่อกับ relay")
        if not data.get("known"):
            raise DeviceUnavailable("ยังไม่ได้รับสถานะจาก UNO Q")
        if data.get("mcu_ok") is False:  # the board polls, but its microcontroller stopped answering
            raise DeviceUnavailable(f"ไมโครคอนโทรลเลอร์บน UNO Q ไม่ตอบ ({data.get('error') or 'ไม่ทราบสาเหตุ'})")
        try:
            return LightState(
                online=True,
                on=bool(data["on"]),
                brightness=int(data["brightness"]),
                updated_at=datetime.now(),
                source="device",
            )
        except (KeyError, TypeError, ValueError) as e:
            raise DeviceUnavailable("UNO Q ตอบสถานะไม่ครบ") from e

    async def get_state(self, device_id: str) -> LightState:
        _check(device_id)
        return self._to_state(await self._request("GET", "/api/v1/control/status"))

    async def apply(self, device_id: str, desired: dict[str, Any]) -> LightState:
        _check(device_id)
        body = {
            "command_id": str(desired.get("command_id", "")),
            "on": bool(desired["on"]),
            "brightness": int(desired["brightness"]),
        }
        path = f"/api/v1/control/led?wait={self._wait_ms}"
        return self._to_state(await self._request("POST", path, body))


def _check(device_id: str) -> None:
    if device_id not in SUPPORTED_DEVICES:
        raise DeviceUnavailable(f"ไม่พบอุปกรณ์ {device_id} บน UNO Q")


def _error_of(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", ""))
    except ValueError:
        return response.text[:80]
