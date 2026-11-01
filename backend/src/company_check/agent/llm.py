"""Single place where chat models are created. Swapping provider = changing this file."""

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI

from company_check.config import get_settings

# Per-call timeout. Retries beyond the model fallback are Temporal's job (activity retry policy),
# so the client only retries once to ride out a single transient blip.
LLM_TIMEOUT_S = 90
CLIENT_RETRIES = 1


def _base_model(name: str) -> BaseChatModel:
    s = get_settings()
    return ChatGoogleGenerativeAI(
        model=name,
        google_api_key=s.google_api_key,
        timeout=LLM_TIMEOUT_S,
        max_retries=CLIENT_RETRIES,
    )


def chat_model(tools: Sequence[Any] | None = None, tool_choice: str | None = None) -> Runnable:
    """Primary model, falling back to the secondary on any error (503 overload, 429 quota...).

    Fallback only triggers before the first streamed chunk, so a stream is never mixed
    across models.
    """
    s = get_settings()

    def build(name: str) -> Runnable:
        m = _base_model(name)
        return m.bind_tools(tools, tool_choice=tool_choice) if tools else m

    primary = build(s.llm_model)
    if s.llm_fallback_model and s.llm_fallback_model != s.llm_model:
        return primary.with_fallbacks([build(s.llm_fallback_model)])
    return primary
