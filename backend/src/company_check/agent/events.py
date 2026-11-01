"""How the agent reports progress to the reviewer.

Nodes never know where events go. The sink is injected through LangGraph's runtime context:
- ConsoleSink: prints, for the CLI runner and debugging.
- PostgresSink (event_store.py): appends to check_events and NOTIFYs the SSE stream.

Event keys: events that may be re-emitted when a step is retried (activity retry, resume after
an interrupt) carry a deterministic `key`, so the durable sink can drop duplicates.
"""

from typing import Any, Protocol


class EventSink(Protocol):
    async def emit(self, type: str, payload: dict[str, Any], key: str | None = None) -> None: ...

    async def delta(self, message_id: str, text: str) -> None:
        """Append streamed text to a message (sinks may buffer these)."""
        ...

    async def end_message(self, message_id: str, **payload: Any) -> None: ...

    async def resume_message(self, message_id: str) -> tuple[str, bool]:
        """Text already streamed for this message by an earlier attempt, and whether it
        finished. Later deltas append to it."""
        ...

    async def restart_message(self, message_id: str) -> None:
        """Throw away earlier partial text for this message and start it again."""
        ...


class ConsoleSink:
    def __init__(self) -> None:
        self._open: str | None = None

    def _close_line(self) -> None:
        if self._open:
            print()
            self._open = None

    async def emit(self, type: str, payload: dict[str, Any], key: str | None = None) -> None:
        self._close_line()
        print(f"[{type}] {payload}")

    async def delta(self, message_id: str, text: str) -> None:
        if self._open != message_id:
            self._close_line()
            print(f"[{message_id}] ", end="")
            self._open = message_id
        print(text, end="", flush=True)

    async def end_message(self, message_id: str, **payload: Any) -> None:
        self._close_line()

    async def resume_message(self, message_id: str) -> tuple[str, bool]:
        return "", False

    async def restart_message(self, message_id: str) -> None:
        self._close_line()
