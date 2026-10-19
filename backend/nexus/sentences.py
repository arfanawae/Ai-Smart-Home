"""Cut a streaming LLM reply into speakable pieces, so TTS can start on the first sentence while the
rest is still being generated. Thai has no full stop: clauses end with a space (often after ครับ/ค่ะ)."""

from __future__ import annotations

import re

MIN_CHARS = 12  # shorter pieces sound choppy; they wait to be joined with the next one
MAX_CHARS = 140  # a run-on without a boundary is cut at the last space

_BOUNDARY = re.compile(r"(?<=[.!?…])\s+|\n+|(?<=ครับ)\s+|(?<=ค่ะ)\s+|(?<=คะ)\s+|(?<=นะ)\s+")


class SentenceSplitter:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        out: list[str] = []
        while True:
            cut = self._next_cut()
            if cut is None:
                break
            piece, self._buffer = self._buffer[:cut].strip(), self._buffer[cut:].lstrip()
            if piece:
                out.append(piece)
        return out

    def flush(self) -> list[str]:
        piece, self._buffer = self._buffer.strip(), ""
        return [piece] if piece else []

    def _next_cut(self) -> int | None:
        for match in _BOUNDARY.finditer(self._buffer):
            if len(self._buffer[: match.start()].strip()) >= MIN_CHARS:
                return match.end()
        if len(self._buffer) > MAX_CHARS:
            space = self._buffer.rfind(" ", MIN_CHARS, MAX_CHARS)
            return space + 1 if space > 0 else MAX_CHARS
        return None
