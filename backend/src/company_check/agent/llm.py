"""Single place where chat models are created. Swapping provider = changing this file."""

import asyncio
import threading
import time
from collections import deque
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import BaseRateLimiter
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI

from company_check.config import get_settings

# Per-call timeout. Retries beyond the model fallback are Temporal's job (activity retry policy),
# so the client only retries once to ride out a single transient blip.
LLM_TIMEOUT_S = 90
CLIENT_RETRIES = 1


class PerMinuteLimiter(BaseRateLimiter):
    """At most `rpm` requests in any 60 s window (how the provider counts its RPM limit).

    Unlike a token bucket it allows an immediate burst up to the limit, so a check's first
    calls don't wait; only the call that would exceed the limit waits for a slot."""

    def __init__(self, rpm: int) -> None:
        self.rpm = rpm
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()

    def _wait_time(self) -> float:
        """Reserve a slot if one is free (returns 0), else how long until one frees up."""
        with self._lock:
            now = time.monotonic()
            while self._sent and now - self._sent[0] >= 60:
                self._sent.popleft()
            if len(self._sent) < self.rpm:
                self._sent.append(now)
                return 0.0
            return 60 - (now - self._sent[0]) + 0.05

    def acquire(self, *, blocking: bool = True) -> bool:
        while (wait := self._wait_time()) > 0:
            if not blocking:
                return False
            time.sleep(wait)
        return True

    async def aacquire(self, *, blocking: bool = True) -> bool:
        while (wait := self._wait_time()) > 0:
            if not blocking:
                return False
            await asyncio.sleep(wait)  # cancellable, so a pause still stops the agent at once
        return True


_limiters: dict[str, PerMinuteLimiter] = {}


def _limiter(name: str, rpm: int | None) -> PerMinuteLimiter | None:
    """One limiter per model per process (the worker is the only caller)."""
    if not rpm:
        return None
    if name not in _limiters:
        _limiters[name] = PerMinuteLimiter(rpm)
    return _limiters[name]


def _base_model(name: str, rpm: int | None) -> BaseChatModel:
    s = get_settings()
    return ChatGoogleGenerativeAI(
        model=name,
        google_api_key=s.google_api_key,
        timeout=LLM_TIMEOUT_S,
        max_retries=CLIENT_RETRIES,
        rate_limiter=_limiter(name, rpm),
    )


def chat_model(tools: Sequence[Any] | None = None, tool_choice: str | None = None) -> Runnable:
    """Primary model, falling back to the secondary on any error (503 overload, 429 quota...).

    Fallback only triggers before the first streamed chunk, so a stream is never mixed
    across models.
    """
    s = get_settings()

    def build(name: str, rpm: int | None) -> Runnable:
        m = _base_model(name, rpm)
        return m.bind_tools(tools, tool_choice=tool_choice) if tools else m

    primary = build(s.llm_model, s.llm_rpm)
    if s.llm_fallback_model and s.llm_fallback_model != s.llm_model:
        return primary.with_fallbacks([build(s.llm_fallback_model, s.llm_fallback_rpm)])
    return primary
