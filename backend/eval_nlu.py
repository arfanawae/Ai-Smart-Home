"""NLU check for the LLM path: indirect / informal / sarcastic / emotional phrasing.

    python eval_nlu.py                 ~12 cases on VOICE_PROVIDER (with its fallback), 15–20 requests
    python eval_nlu.py groq --gap 10   one provider only (no fallback), 10 s between cases so the
    python eval_nlu.py gemini --gap 6  free-tier per-minute limits are not what gets measured

Last results (2026-10-20, English system prompt): Groq gpt-oss-120b 12/12 intent, 12/12 language,
median 1.8 s; Gemini flash-lite 12/12, 12/12, 1.5 s.

Each case sets the simulated light, asks once, and checks two things:
  - did NEXUS change the light in the expected direction (or correctly leave it alone)?
  - did it answer in the user's language?
Run it before and after any change to prompts.py — a prompt that reads better can still make the
model stop calling tools (that happened once already, see prompts.py).
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from dataclasses import dataclass
from typing import Literal

from nexus.cli import build_assistant
from nexus.config import load_settings
from nexus.fastpath import decide
from nexus.language import detect_language

Expect = Literal["brighter", "dimmer", "no_change"]


@dataclass(frozen=True)
class Case:
    text: str
    on: bool
    brightness: int
    expect: Expect


CASES = [
    # indirect / informal / emotional cues that should change the light
    Case("มืดจังมองไม่เห็นเลย", False, 100, "brighter"),
    Case("ตาจะบอดแล้วเนี่ย สว่างจ้าเกิน", True, 100, "dimmer"),
    Case("ขอแสงหน่อยจ้า", False, 100, "brighter"),
    Case("It's pitch black in here, I can't see a thing", False, 100, "brighter"),
    Case("My eyes are burning, this light is way too much", True, 100, "dimmer"),
    # playful, sarcastic, emotional, or explicitly "leave it" — should not touch the light
    Case("โอ้โห NEXUS ฉลาดที่สุดในโลกเลย", True, 60, "no_change"),
    Case("วันนี้เหนื่อยมาก อยากนอนแล้ว", True, 60, "no_change"),
    Case("อย่าเพิ่งปิดไฟนะ ยังอ่านหนังสืออยู่", True, 60, "no_change"),
    Case("ฮ่าๆ ไฟนี่กะพริบเหมือนดิสโก้เลย", True, 60, "no_change"),
    Case("แสงสลัวๆ แบบนี้กำลังดีเลย", True, 30, "no_change"),
    Case("Ugh, whatever, just leave it", True, 60, "no_change"),
    Case("Wow, great job NEXUS, you're a genius", True, 60, "no_change"),
]


def effective(on: bool, brightness: int) -> int:
    return brightness if on else 0


async def run(only: str | None = None, gap_s: float = 0.0) -> None:
    # NLU is what is measured here, not transport: always in-process devices (and direct state setup).
    settings = dataclasses.replace(load_settings(), device_backend="local")
    if only:  # one provider, no fallback: drop the other key
        settings = dataclasses.replace(
            settings,
            voice_provider=only,
            gemini_api_key=settings.gemini_api_key if only == "gemini" else None,
            groq_api_key=settings.groq_api_key if only == "groq" else None,
        )
    correct = language_ok = tested = 0
    for case in CASES:
        assert not decide(case.text).execute, f"{case.text!r} goes to the fast path, not Gemini"
        assistant, devices = build_assistant(settings, emit=lambda _e: None)
        await devices.driver.apply(  # type: ignore[attr-defined]  # local backend: DeviceService
            "main_light", {"on": case.on, "brightness": case.brightness}
        )
        before = effective(case.on, case.brightness)
        turn = await assistant.handle(case.text)
        if gap_s:
            await asyncio.sleep(gap_s)
        if turn.route == "system":  # quota / network error: this case was not actually tested
            print(f"? {case.text[:34]:<36} NOT TESTED — {turn.reply[:80]}")
            continue
        tested += 1
        state = await devices.driver.get_state("main_light")  # type: ignore[attr-defined]
        after = effective(state.on, state.brightness)
        got: Expect = "brighter" if after > before else "dimmer" if after < before else "no_change"
        ok = got == case.expect
        same_language = detect_language(turn.reply) == detect_language(case.text)
        correct += ok
        language_ok += same_language
        tools = ",".join(c.tool for c in turn.commands) or "-"
        print(f"{'✓' if ok else '✗'} {case.text[:34]:<36} expect {case.expect:<9} got {got:<9} [{tools}]")
        print(f"     {'' if same_language else '⚠ language! '}{turn.reply[:110]}")
    print(f"\nintent correct {correct}/{tested} · user's language {language_ok}/{tested} · not tested {len(CASES) - tested}")
    print(f"quota: {assistant.quota.summary_th()}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    import argparse

    parser = argparse.ArgumentParser(description="NLU check for the LLM path")
    parser.add_argument("provider", nargs="?", choices=["gemini", "groq"], help="test one provider only")
    parser.add_argument("--gap", type=float, default=0.0, help="seconds between cases (free-tier limits)")
    args = parser.parse_args()
    asyncio.run(run(args.provider, args.gap))
