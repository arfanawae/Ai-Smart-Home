"""Simulated light with configurable latency and failure injection (for testing every UI/AI state)."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Any, Literal

from ..models import LightState
from .base import DeviceUnavailable

FailureMode = Literal["none", "offline", "timeout", "mismatch", "error"]
FAILURE_MODES: tuple[FailureMode, ...] = ("none", "offline", "timeout", "mismatch", "error")


class MockDriver:
    simulated = True

    def __init__(self, latency_s: tuple[float, float] = (0.1, 0.4), seed: int | None = None) -> None:
        self._latency_s = latency_s
        self._rng = random.Random(seed)
        self._states: dict[str, LightState] = {
            "main_light": LightState(online=True, on=False, brightness=100, updated_at=datetime.now(), source="mock"),
        }
        self.failure: FailureMode = "none"

    async def _delay(self) -> None:
        low, high = self._latency_s
        if high > 0:
            await asyncio.sleep(self._rng.uniform(low, high))

    def _state(self, device_id: str) -> LightState:
        if device_id not in self._states:
            raise DeviceUnavailable(f"ไม่พบอุปกรณ์ {device_id} (จำลอง)")
        return self._states[device_id]

    async def get_state(self, device_id: str) -> LightState:
        await self._delay()
        if self.failure == "offline":
            raise DeviceUnavailable("อุปกรณ์ออฟไลน์ (จำลอง)")
        return self._state(device_id)

    async def apply(self, device_id: str, desired: dict[str, Any]) -> LightState:
        await self._delay()
        current = self._state(device_id)
        match self.failure:
            case "offline":
                raise DeviceUnavailable("อุปกรณ์ออฟไลน์ (จำลอง)")
            case "error":
                raise DeviceUnavailable("อุปกรณ์แจ้งข้อผิดพลาด (จำลอง)")
            case "timeout":
                await asyncio.sleep(3600)  # never answers; the service's timeout cancels this
            case "mismatch":
                return current  # pretends to accept the command but nothing changes
        new_state = LightState(
            online=True,
            on=bool(desired["on"]),
            brightness=int(desired["brightness"]),
            updated_at=datetime.now(),
            source="mock",
        )
        self._states[device_id] = new_state
        return new_state
