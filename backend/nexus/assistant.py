"""Turn orchestrator: fast path → (otherwise) LLM with tools → device service → reply.

Truth rule: what the user is told about a device always comes from a CommandResult,
never from the LLM's own wording (see _guard_reply).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import fastpath, replies
from .devices.drivers.base import DeviceUnavailable
from .devices.models import CommandResult, CommandStatus, LightState
from .devices.service import DeviceService
from .events import AIState, AssistantReply, CommandUpdate, EventSink, StateChanged
from .llm.base import ChatMessage, LLMError, LLMProvider, ToolResult
from .prompts import build_system_prompt
from .quota import QuotaTracker
from .tools import TOOL_SPECS

MAX_TOOL_ROUNDS = 3
MAX_HISTORY_TURNS = 10

# "เปิดไฟแล้ว", "ปรับให้เรียบร้อย", "ปิดสำเร็จ" … — a completion claim about a device action
_CLAIM_RE = re.compile(r"(เปิด|ปิด|ปรับ|หรี่|ตั้ง|ลด|เพิ่ม)[^\n]{0,25}(แล้ว|เรียบร้อย|สำเร็จ)")


@dataclass
class TurnResult:
    route: str  # "fastpath" | "llm" | "system"
    reply: str
    commands: list[CommandResult] = field(default_factory=list)
    fastpath_reason: str = ""


class Assistant:
    def __init__(
        self,
        devices: DeviceService,
        llm: LLMProvider | None,
        quota: QuotaTracker,
        emit: EventSink,
    ) -> None:
        self.devices = devices
        self.llm = llm
        self.quota = quota
        self._emit = emit
        self._history: list[ChatMessage] = []

    def reset(self) -> None:
        self._history.clear()

    def _state(self, state: AIState, detail: str = "") -> None:
        self._emit(StateChanged(state, detail))

    async def handle(self, text: str) -> TurnResult:
        decision = fastpath.decide(text)
        if decision.execute:
            turn = await self._fast_path(text, decision)
        else:
            turn = await self._llm_path(text)
            turn.fastpath_reason = decision.reason
        self._emit(AssistantReply(turn.reply, turn.route))
        self._finish_state(turn.commands)
        return turn

    def _finish_state(self, commands: list[CommandResult]) -> None:
        changes = [c for c in commands if c.tool != "get_device_status"]
        if any(not c.ok for c in changes):
            self._state(AIState.ERROR, next(c.error or "" for c in changes if not c.ok))
        elif changes:
            self._state(AIState.SUCCESS, "จำลองสำเร็จ" if any(c.simulated for c in changes) else "")
        self._state(AIState.IDLE)

    async def _run_command(self, tool: str, args: dict, route: str) -> CommandResult:
        self._state(AIState.EXECUTING, tool)
        result = await self.devices.execute(
            tool, args, on_waiting=lambda: self._state(AIState.WAITING_FOR_DEVICE)
        )
        self._emit(CommandUpdate(result, route))
        return result

    # ── fast path ────────────────────────────────────────────────────────────
    async def _fast_path(self, text: str, decision: fastpath.FastPathDecision) -> TurnResult:
        assert decision.tool is not None
        result = await self._run_command(decision.tool, decision.args, "fastpath")
        reply = replies.describe_result(result)
        # Keep the exchange in history so a later LLM turn ("หรี่อีกหน่อย") has context.
        self._commit([ChatMessage("user", text), ChatMessage("assistant", reply)])
        return TurnResult("fastpath", reply, [result], decision.reason)

    # ── LLM path ─────────────────────────────────────────────────────────────
    async def _llm_path(self, text: str) -> TurnResult:
        if self.llm is None:
            self._state(AIState.ERROR, "ยังไม่ได้ตั้งค่า Gemini")
            return TurnResult("system", replies.NO_LLM)
        if not self.quota.can_request():
            self._state(AIState.ERROR, "โควตาหมด")
            reset = f"{self.quota.next_reset_local():%H:%M}"
            return TurnResult("system", replies.QUOTA_EXHAUSTED.format(reset=reset))

        turn: list[ChatMessage] = [ChatMessage("user", text)]
        commands: list[CommandResult] = []
        try:
            for _round in range(MAX_TOOL_ROUNDS + 1):
                self._state(AIState.THINKING)
                system = build_system_prompt(await self._current_light(), self.devices.simulated)
                self.quota.record()
                response = await self.llm.generate(system, [*self._history, *turn], TOOL_SPECS)
                if not response.tool_calls:
                    reply = self._guard_reply(response.text, commands)
                    turn.append(ChatMessage("assistant", response.text, raw=response.raw))
                    self._commit(turn)
                    return TurnResult("llm", reply, commands)
                if _round == MAX_TOOL_ROUNDS:
                    break
                turn.append(ChatMessage("assistant", response.text, response.tool_calls, raw=response.raw))
                results = []
                for call in response.tool_calls:
                    result = await self._run_command(call.name, call.args, "llm")
                    commands.append(result)
                    results.append(ToolResult(call.id, call.name, result.to_tool_payload()))
                turn.append(ChatMessage("tool", tool_results=results))
        except LLMError as e:
            if e.kind == "quota":
                self.quota.mark_exhausted()
            self._state(AIState.ERROR, str(e))
            # The partial turn is dropped so history never holds an unanswered tool call.
            note = "".join(f" {replies.describe_result(c)}" for c in commands if c.tool != "get_device_status")
            return TurnResult("system", replies.LLM_FAILED.format(reason=e.kind) + note, commands)

        self._state(AIState.ERROR, "ใช้เครื่องมือหลายรอบเกินไป")
        return TurnResult("system", replies.TOO_MANY_STEPS, commands)

    async def _current_light(self) -> LightState | None:
        try:
            return await self.devices.driver.get_state("main_light")
        except DeviceUnavailable:
            return None

    @staticmethod
    def _guard_reply(text: str, commands: list[CommandResult]) -> str:
        """Never let the LLM's wording overstate what the device actually confirmed."""
        text = text.strip() or "ครับ"
        changes = [c for c in commands if c.tool != "get_device_status"]
        succeeded = [c for c in changes if c.status is CommandStatus.SUCCEEDED]
        # "ไฟปิดอยู่แล้ว" describes the current state and "ไม่สำเร็จ" admits failure — neither is a claim
        claims_done = _CLAIM_RE.search(text.replace("อยู่แล้ว", "").replace("ไม่สำเร็จ", ""))
        if not succeeded and claims_done:
            note = replies.COMMAND_FAILED_NOTE if changes else replies.NO_COMMAND_NOTE
            return f"{text}\n{note}"
        if any(c.simulated for c in succeeded) and "จำลอง" not in text:
            return f"{text} {replies.SIMULATION_TAG}"
        return text

    def _commit(self, turn: list[ChatMessage]) -> None:
        self._history.extend(turn)
        # Trim whole turns from the front (a turn starts at a user message) so tool calls stay paired.
        user_starts = [i for i, m in enumerate(self._history) if m.role == "user" and m.text]
        if len(user_starts) > MAX_HISTORY_TURNS:
            del self._history[: user_starts[-MAX_HISTORY_TURNS]]
