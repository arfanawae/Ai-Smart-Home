"""Validation + execution of device commands.

Every command — from the fast path, the LLM, or (later) a UI button — goes through here.
A command only counts as SUCCEEDED when the device reports the desired state back.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from ..tools import ADJUST_FLOOR, DELTA_LIMIT, DEVICE_IDS, LEVEL_MAX, LEVEL_MIN, TOOL_NAMES
from .drivers.base import DeviceDriver, DeviceUnavailable
from .models import CommandResult, CommandStatus, LightState

_TOOL_ARGS = {
    "get_device_status": set(),
    "turn_light_on": set(),
    "turn_light_off": set(),
    "set_brightness": {"level"},
    "adjust_brightness": {"delta"},
}


class ValidationError(Exception):
    """Command refused before reaching the device. The message is shown/spoken to the user (Thai)."""


def _require_int(args: dict[str, Any], key: str, low: int, high: int) -> int:
    value = args.get(key)
    if isinstance(value, float) and value.is_integer():  # JSON numbers from the LLM may arrive as 30.0
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"ค่า {key} ต้องเป็นจำนวนเต็ม")
    if not low <= value <= high:
        raise ValidationError(f"ค่า {key} ต้องอยู่ระหว่าง {low} ถึง {high}")
    return value


def validate(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool not in TOOL_NAMES:
        raise ValidationError(f"ไม่รู้จักคำสั่ง {tool}")
    if not isinstance(args, dict):
        raise ValidationError("รูปแบบคำสั่งไม่ถูกต้อง")
    allowed = _TOOL_ARGS[tool] | {"device_id"}
    unknown = set(args) - allowed
    if unknown:
        raise ValidationError(f"คำสั่ง {tool} ไม่รับพารามิเตอร์ {', '.join(sorted(unknown))}")
    device_id = args.get("device_id")
    if device_id not in DEVICE_IDS:
        raise ValidationError(f"ไม่พบอุปกรณ์ {device_id!r} — ตอนนี้ควบคุมได้เฉพาะไฟห้องหลัก")
    normalized: dict[str, Any] = {"device_id": device_id}
    if tool == "set_brightness":
        normalized["level"] = _require_int(args, "level", LEVEL_MIN, LEVEL_MAX)
    elif tool == "adjust_brightness":
        normalized["delta"] = _require_int(args, "delta", -DELTA_LIMIT, DELTA_LIMIT)
        if normalized["delta"] == 0:
            raise ValidationError("ค่าที่จะปรับต้องไม่เป็น 0")
    return normalized


def desired_state(tool: str, args: dict[str, Any], current: LightState) -> dict[str, Any]:
    """Translate an intent into an absolute target state, so retries are idempotent."""
    last_level = current.brightness if current.brightness > 0 else LEVEL_MAX
    match tool:
        case "turn_light_on":
            return {"on": True, "brightness": last_level}
        case "turn_light_off":
            return {"on": False, "brightness": last_level}
        case "set_brightness":
            level = args["level"]
            return {"on": False, "brightness": last_level} if level == 0 else {"on": True, "brightness": level}
        case "adjust_brightness":
            delta = args["delta"]
            if not current.on and delta < 0:
                raise ValidationError("ไฟปิดอยู่แล้ว จึงลดความสว่างไม่ได้")
            base = current.brightness if current.on else 0
            return {"on": True, "brightness": max(ADJUST_FLOOR, min(LEVEL_MAX, base + delta))}
    raise ValidationError(f"คำสั่ง {tool} ไม่ใช่คำสั่งควบคุม")


def _matches(desired: dict[str, Any], reported: LightState) -> bool:
    if reported.on != desired["on"]:
        return False
    return not desired["on"] or reported.brightness == desired["brightness"]


class DeviceService:
    def __init__(
        self,
        driver: DeviceDriver,
        *,
        timeout_s: float = 3.0,
        retries: int = 1,
        max_commands_per_s: int = 5,
    ) -> None:
        self.driver = driver
        self._timeout_s = timeout_s
        self._retries = retries
        self._max_per_s = max_commands_per_s
        self._recent: dict[str, deque[float]] = {}

    @property
    def simulated(self) -> bool:
        return self.driver.simulated

    def _rate_ok(self, device_id: str) -> bool:
        now = time.monotonic()
        window = self._recent.setdefault(device_id, deque())
        while window and now - window[0] > 1.0:
            window.popleft()
        if len(window) >= self._max_per_s:
            return False
        window.append(now)
        return True

    async def execute(
        self,
        tool: str,
        args: dict[str, Any],
        on_waiting: Callable[[], None] | None = None,
    ) -> CommandResult:
        started = time.monotonic()

        def result(status: CommandStatus, **kw: Any) -> CommandResult:
            return CommandResult(
                tool=tool,
                args=dict(args) if isinstance(args, dict) else {},
                status=status,
                simulated=self.simulated,
                latency_ms=round((time.monotonic() - started) * 1000),
                device_id=args.get("device_id") if isinstance(args, dict) else None,
                **kw,
            )

        try:
            normalized = validate(tool, args)
        except ValidationError as e:
            return result(CommandStatus.REJECTED, error=str(e))
        device_id = normalized["device_id"]

        try:
            current = await asyncio.wait_for(self.driver.get_state(device_id), self._timeout_s)
        except DeviceUnavailable as e:
            return result(CommandStatus.FAILED, error=str(e))
        except TimeoutError:
            return result(CommandStatus.TIMEOUT, error="อุปกรณ์ไม่ตอบกลับ — ไม่ทราบสถานะปัจจุบัน")

        if tool == "get_device_status":
            return result(CommandStatus.SUCCEEDED, previous=current, reported=current)

        try:
            desired = desired_state(tool, normalized, current)
        except ValidationError as e:
            return result(CommandStatus.REJECTED, previous=current, error=str(e))
        if not self._rate_ok(device_id):
            return result(CommandStatus.REJECTED, previous=current, error="ส่งคำสั่งถี่เกินไป กรุณารอสักครู่")

        command_id = uuid.uuid4().hex[:12]
        if on_waiting:
            on_waiting()
        reported: LightState | None = None
        for _attempt in range(self._retries + 1):
            try:
                reported = await asyncio.wait_for(self.driver.apply(device_id, desired), self._timeout_s)
                break
            except TimeoutError:
                continue  # safe to retry: the desired state is absolute
            except DeviceUnavailable as e:
                return result(CommandStatus.FAILED, command_id=command_id, previous=current, error=str(e))

        if reported is None:
            return result(
                CommandStatus.TIMEOUT,
                command_id=command_id,
                previous=current,
                error=f"อุปกรณ์ไม่ตอบกลับภายใน {self._timeout_s:g} วินาที — สถานะไฟไม่แน่ชัด",
            )
        if not _matches(desired, reported):
            return result(
                CommandStatus.FAILED,
                command_id=command_id,
                previous=current,
                reported=reported,
                error="อุปกรณ์รายงานสถานะไม่ตรงกับคำสั่ง",
            )
        return result(CommandStatus.SUCCEEDED, command_id=command_id, previous=current, reported=reported)
