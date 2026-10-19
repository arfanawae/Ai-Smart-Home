"""Turn orchestrator: fast path → (otherwise) LLM with tools → device service → reply.

Truth rule: what the user is told about a device always comes from a CommandResult,
never from the LLM's own wording (see _guard_reply).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from . import fastpath, replies
from .devices.models import CommandResult, CommandStatus, LightState
from .devices.gateway import DeviceGateway
from .events import AIState, AssistantReply, CommandUpdate, EventSink, StateChanged
from .language import Language, detect_language
from .llm.base import ChatMessage, LLMError, LLMProvider, LLMResponse, ToolResult, stream_reply
from .prompts import build_system_prompt
from .quota import QuotaTracker
from .sentences import SentenceSplitter

MAX_TOOL_ROUNDS = 3
MAX_HISTORY_TURNS = 10

# "เปิดไฟแล้ว", "ปรับให้เรียบร้อย", "ปิดสำเร็จ" … — a completion claim about a device action
_CLAIM_TH = re.compile(r"(เปิด|ปิด|ปรับ|หรี่|ตั้ง|ลด|เพิ่ม)[^\n]{0,25}?(แล้ว|เรียบร้อย|สำเร็จ)")
# English claims are first-person ("I've turned on…") so state descriptions ("the light is on") don't count
_CLAIM_EN = re.compile(
    r"\bI(?:'ve| have| just)?\s+(?:turned|switched|dimmed|brightened|adjusted|lowered|raised|set)\b|\b(?:done|all set)\b",
    re.IGNORECASE,
)
_NEGATED_EN = re.compile(r"\b(?:not|n't|couldn't|unable|failed)\b", re.IGNORECASE)
# Offers and questions ("ต้องการให้ NEXUS เปิดไฟแล้วปรับ…ไหมครับ", "Would you like me to…") are not claims
_OFFER_BEFORE = ("ต้องการให้", "อยากให้", "ให้ผม", "จะให้", "would you like", "do you want", "shall i", "want me to")
_QUESTION_MARKERS = ("ไหม", "มั้ย", "หรือเปล่า", "หรือไม่", "?")


def _claims_completion(text: str) -> bool:
    # "ไฟปิดอยู่แล้ว" describes the current state and "ไม่สำเร็จ" admits failure — neither is a claim
    cleaned = text.replace("อยู่แล้ว", "").replace("ไม่สำเร็จ", "")
    for match in _CLAIM_TH.finditer(cleaned):
        rest_of_clause = re.split(r"\s", cleaned[match.end():], maxsplit=1)[0]  # Thai separates clauses with spaces
        before = cleaned[max(0, match.start() - 20) : match.start()]
        if any(q in rest_of_clause for q in _QUESTION_MARKERS) or any(o in before for o in _OFFER_BEFORE):
            continue
        return True
    for sentence in re.split(r"(?<=[.!?])\s+", cleaned):
        sentence = sentence.strip()
        if not sentence or sentence.endswith("?") or any(o in sentence.lower() for o in _OFFER_BEFORE):
            continue
        if _CLAIM_EN.search(sentence) and not _NEGATED_EN.search(sentence):
            return True
    return False


SpeechSink = Callable[[str], None]


def from_locale(locale: str | None) -> Language | None:
    """"th-TH" → "th", "en-US" → "en"; anything else → None (fall back to the text's script)."""
    prefix = (locale or "").split("-")[0].lower()
    return prefix if prefix in ("th", "en") else None  # type: ignore[return-value]


class _Speaker:
    """What NEXUS says out loud during a turn, streamed sentence by sentence.

    A sentence that claims a device action is done is held back until a device has actually confirmed
    something this turn — it is said at the end, followed by the guard's correction if there is one.
    """

    def __init__(self, sink: SpeechSink | None) -> None:
        self._sink = sink
        self._splitter = SentenceSplitter()
        self._held: list[str] = []
        self.confirmed = False
        self.streamed = ""  # the LLM text the final reply was built from (set by the LLM path)

    def feed(self, delta: str) -> None:
        for sentence in self._splitter.feed(delta):
            self._say(sentence)

    def flush(self) -> None:
        for sentence in self._splitter.flush():
            self._say(sentence)

    def _say(self, sentence: str) -> None:
        if not self.confirmed and _claims_completion(sentence):
            self._held.append(sentence)
        else:
            self._emit(sentence)

    def announce(self, text: str | None) -> None:
        """Say something right now (the acknowledgement before a device command)."""
        if text:
            self._emit(text)

    def _emit(self, text: str) -> None:
        spoken = replies.to_speech(text)
        if spoken and self._sink:
            self._sink(spoken)

    def finish(self, reply: str) -> None:
        """Say what the final reply adds beyond what was streamed: held claims, then any system note."""
        for sentence in self._held:
            self._emit(sentence)
        self._held.clear()
        if self.streamed and reply.startswith(self.streamed):
            self._emit(reply[len(self.streamed) :])
        elif not self.streamed:
            self._emit(reply)


@dataclass
class TurnResult:
    route: str  # "fastpath" | "llm" | "system"
    reply: str
    commands: list[CommandResult] = field(default_factory=list)
    fastpath_reason: str = ""


class Assistant:
    def __init__(
        self,
        devices: DeviceGateway,
        llm: LLMProvider | None,
        quota: QuotaTracker,
        emit: EventSink,
    ) -> None:
        self.devices = devices
        self.llm = llm
        self.quota = quota
        self._emit = emit
        self._history: list[ChatMessage] = []
        # Voice and the web UI can both send turns; one at a time keeps history and device state coherent.
        self._turn_lock = asyncio.Lock()

    def reset(self) -> None:
        self._history.clear()

    def _state(self, state: AIState, detail: str = "") -> None:
        self._emit(StateChanged(state, detail))

    async def run_manual(self, tool: str, args: dict) -> CommandResult:
        """A button/slider in the UI: same validation and confirmation as AI commands, but no LLM."""
        async with self._turn_lock:
            result = await self.devices.execute(tool, args)
            self._emit(CommandUpdate(result, "manual"))
            return result

    async def handle(self, text: str, language: str | None = None, on_speech: SpeechSink | None = None) -> TurnResult:
        """One turn. `language` is the STT locale ("th-TH"/"en-US") when the text came from speech;
        `on_speech` receives speakable sentences as soon as they are ready (voice mode)."""
        async with self._turn_lock:
            return await self._handle(text, from_locale(language) or detect_language(text), _Speaker(on_speech))

    async def _handle(self, text: str, language: Language, speaker: _Speaker) -> TurnResult:
        decision = fastpath.decide(text)
        if decision.execute:
            turn = await self._fast_path(text, decision, language, speaker)
        else:
            turn = await self._llm_path(text, language, speaker)
            turn.fastpath_reason = decision.reason
        speaker.finish(turn.reply)
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
    async def _fast_path(
        self, text: str, decision: fastpath.FastPathDecision, language: Language, speaker: _Speaker
    ) -> TurnResult:
        assert decision.tool is not None
        # Two-step feedback: "ได้ครับ กำลังเปิดไฟให้ครับ" now, the confirmed result when the device answers.
        speaker.announce(replies.acknowledgement(decision.tool, decision.args, language))
        result = await self._run_command(decision.tool, decision.args, "fastpath")
        reply = replies.describe_result(result, language)
        # Keep the exchange in history so a later LLM turn ("หรี่อีกหน่อย") has context.
        self._commit([ChatMessage("user", text), ChatMessage("assistant", reply)])
        return TurnResult("fastpath", reply, [result], decision.reason)

    # ── LLM path ─────────────────────────────────────────────────────────────
    async def _llm_path(self, text: str, language: Language, speaker: _Speaker) -> TurnResult:
        messages = replies.MESSAGES[language]
        if self.llm is None:
            self._state(AIState.ERROR, "ยังไม่ได้ตั้งค่า Gemini")
            return TurnResult("system", messages["no_llm"])
        if (wait := self.quota.paused_for) > 0 and not self.quota.exhausted:
            self._state(AIState.ERROR, "เรียก AI ถี่เกินไป")
            return TurnResult("system", messages["rate_limit"].format(seconds=max(1, round(wait))))
        if not self.quota.can_request():
            self._state(AIState.ERROR, "โควตาหมด")
            reset = f"{self.quota.next_reset_local():%H:%M}"
            return TurnResult("system", messages["quota"].format(reset=reset))

        turn: list[ChatMessage] = [ChatMessage("user", text)]
        commands: list[CommandResult] = []
        said = ""  # everything the LLM said this turn, across tool rounds (it may preface a tool call)
        light = await self._current_light()  # read once; later rounds use what commands reported back
        try:
            for _round in range(MAX_TOOL_ROUNDS + 1):
                self._state(AIState.THINKING)
                system = build_system_prompt(light, self.devices.simulated, language)
                self.quota.record()
                response: LLMResponse | None = None
                async for event in stream_reply(self.llm, system, [*self._history, *turn], self.devices.tools):
                    if isinstance(event, LLMResponse):
                        response = event
                    else:
                        speaker.feed(event)
                speaker.flush()
                assert response is not None
                said = f"{said} {response.text}".strip()
                if not response.tool_calls:
                    reply = self._guard_reply(said, commands, language)
                    speaker.streamed = said
                    turn.append(ChatMessage("assistant", response.text, raw=response.raw))
                    self._commit(turn)
                    return TurnResult("llm", reply, commands)
                if _round == MAX_TOOL_ROUNDS:
                    break
                turn.append(ChatMessage("assistant", response.text, response.tool_calls, raw=response.raw))
                if not said and not commands:  # nothing said yet: acknowledge before the device acts
                    first = next((c for c in response.tool_calls if c.name != "get_device_status"), None)
                    if first is not None:
                        speaker.announce(replies.acknowledgement(first.name, first.args, language))
                results = []
                for call in response.tool_calls:
                    result = await self._run_command(call.name, call.args, "llm")
                    commands.append(result)
                    results.append(ToolResult(call.id, call.name, result.to_tool_payload()))
                    if result.reported is not None and result.device_id == "main_light":
                        light = result.reported
                speaker.confirmed = any(c.ok and c.tool != "get_device_status" for c in commands)
                turn.append(ChatMessage("tool", tool_results=results))
        except LLMError as e:
            if e.kind == "quota":
                self.quota.mark_exhausted()
            elif e.kind == "rate_limit":
                self.quota.pause(e.retry_after_s or 30.0)
            self._state(AIState.ERROR, str(e))
            # The partial turn is dropped so history never holds an unanswered tool call.
            note = "".join(f" {replies.describe_result(c)}" for c in commands if c.tool != "get_device_status")
            if e.kind == "rate_limit":
                reply = messages["rate_limit"].format(seconds=max(1, round(e.retry_after_s or 30.0)))
            elif e.kind == "quota":
                reply = messages["quota"].format(reset=f"{self.quota.next_reset_local():%H:%M}")
            else:
                reply = messages["llm_failed"].format(reason=e.kind)
            return TurnResult("system", reply + note, commands)

        self._state(AIState.ERROR, "ใช้เครื่องมือหลายรอบเกินไป")
        return TurnResult("system", messages["too_many_steps"], commands)

    async def _current_light(self) -> LightState | None:
        return await self.devices.get_state("main_light")

    @staticmethod
    def _guard_reply(text: str, commands: list[CommandResult], language: Language = "th") -> str:
        """Never let the LLM's wording overstate what the device actually confirmed."""
        messages = replies.MESSAGES[language]
        text = text.strip() or "ครับ"
        changes = [c for c in commands if c.tool != "get_device_status"]
        succeeded = [c for c in changes if c.status is CommandStatus.SUCCEEDED]
        if not succeeded and _claims_completion(text):
            note = messages["command_failed"] if changes else messages["no_command"]
            return f"{text}\n{note}"
        mentions_simulation = "จำลอง" in text or "simulat" in text.lower()
        if any(c.simulated for c in succeeded) and not mentions_simulation:
            return f"{text} {messages['simulation_tag']}"
        return text

    def _commit(self, turn: list[ChatMessage]) -> None:
        self._history.extend(turn)
        # Trim whole turns from the front (a turn starts at a user message) so tool calls stay paired.
        user_starts = [i for i, m in enumerate(self._history) if m.role == "user" and m.text]
        if len(user_starts) > MAX_HISTORY_TURNS:
            del self._history[: user_starts[-MAX_HISTORY_TURNS]]
