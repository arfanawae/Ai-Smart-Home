"""Primary LLM with a second provider behind it (VOICE_PROVIDER picks the order).

If the primary cannot answer — no connection, timeout, rate limit, daily quota, bad key or model —
the same request goes to the secondary, and the primary is skipped for a while (its retry time, or
10 minutes after a daily-quota / key error) so every turn does not pay for a failing call first.
Errors that the other provider would hit too (a bad request) are not retried.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Sequence

from ..banner import provider_banner
from ..tools import ToolSpec
from .base import ChatMessage, LLMError, LLMProvider, LLMResponse, stream_reply

log = logging.getLogger(__name__)

FALLBACK_KINDS = {"unavailable", "timeout", "rate_limit", "quota", "auth", "model_not_found"}
LONG_SKIP_S = 600.0


class FallbackLLM:
    def __init__(self, primary: LLMProvider, secondary: LLMProvider) -> None:
        self.primary = primary
        self.secondary = secondary
        self._skip_primary_until = 0.0
        self.last_used: LLMProvider = primary

    @property
    def provider(self) -> str:
        return f"{self.primary.provider}→{self.secondary.provider}"

    @property
    def model(self) -> str:
        return self.primary.model

    @property
    def active(self) -> LLMProvider:
        """The provider the next request goes to first (the secondary while the primary is skipped)."""
        return self._order()[0]

    def _order(self) -> list[LLMProvider]:
        if time.monotonic() < self._skip_primary_until:
            return [self.secondary]
        return [self.primary, self.secondary]

    def _note_failure(self, provider: LLMProvider, e: LLMError) -> None:
        if provider is self.primary:
            skip = e.retry_after_s if e.kind == "rate_limit" and e.retry_after_s else LONG_SKIP_S
            if e.kind in ("unavailable", "timeout"):
                skip = 30.0
            self._skip_primary_until = time.monotonic() + skip
        log.warning("%s failed (%s: %s) — trying %s", provider.provider, e.kind, e, self.secondary.provider)
        provider_banner(self.secondary.provider, self.secondary.model, f"{provider.provider} failed: {e.kind}", kind="fallback")

    async def generate(self, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> LLMResponse:
        order = self._order()
        for i, provider in enumerate(order):
            try:
                response = await provider.generate(system, messages, tools)
                self.last_used = provider
                return response
            except LLMError as e:
                if e.kind not in FALLBACK_KINDS or i == len(order) - 1:
                    raise
                self._note_failure(provider, e)
        raise AssertionError("unreachable")

    async def generate_stream(
        self, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[str | LLMResponse]:
        """Streams from whichever provider answers; falls back only if nothing was said yet."""
        order = self._order()
        for i, provider in enumerate(order):
            said = False
            try:
                async for event in stream_reply(provider, system, messages, tools):
                    said = said or isinstance(event, str)
                    yield event
                self.last_used = provider
                return
            except LLMError as e:
                if said or e.kind not in FALLBACK_KINDS or i == len(order) - 1:
                    raise
                self._note_failure(provider, e)
