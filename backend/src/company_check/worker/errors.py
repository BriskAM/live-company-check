"""Turn agent failures into a short message the reviewer can act on.

Pure string handling, so the workflow can use it (deterministic). The raw text is kept,
shortened, for whoever debugs it.
"""

# (markers, message): first match wins.
KNOWN = [
    (
        ("LLMConfigError", "API_KEY_INVALID", "PERMISSION_DENIED", " 401", " 402", " 403"),
        "The AI provider rejected the request (API key or billing). Fix the key, then retry.",
    ),
    (
        ("429", "RESOURCE_EXHAUSTED", "quota"),
        "The AI provider's rate limit was reached. Wait a minute, then retry.",
    ),
    (
        ("503", "UNAVAILABLE", "overloaded", "high demand"),
        "The AI provider is overloaded right now. Retry in a moment.",
    ),
    (
        ("timed out", "Timeout", "DEADLINE_EXCEEDED"),
        "The AI provider took too long to answer. Retry.",
    ),
    (
        ("could not finish",),
        "The agent could not finish its analysis. Retry, or start a new check.",
    ),
]
DETAIL_CHARS = 300


def describe(raw: str) -> dict[str, str]:
    text = f" {raw} "
    message = next(
        (msg for markers, msg in KNOWN if any(m in text for m in markers)),
        "The agent hit an unexpected error. Retry; nothing done so far is lost.",
    )
    detail = " ".join(raw.split())
    if len(detail) > DETAIL_CHARS:
        detail = detail[:DETAIL_CHARS] + "…"
    return {"message": message, "detail": detail}
