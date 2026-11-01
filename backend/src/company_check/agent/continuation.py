"""Joining a continued LLM message onto text that was already streamed.

When the report is interrupted (pause, worker crash) the reviewer has already seen part of
it. On the next attempt the model is asked to continue from exactly where the text stops.
Models don't always comply, so the first part of the continuation is checked:

- it repeats the tail of what was shown  → drop the repeated part (overlap)
- it starts the whole report again       → restart the message cleanly (no duplicate text)
- otherwise                              → append as is
"""

from typing import Literal

# Shortest repeated tail treated as an overlap; shorter matches are likely coincidence.
MIN_OVERLAP = 12
MAX_OVERLAP = 400
# The continuation is judged on this many characters before anything is streamed.
DECIDE_AFTER = 160
# A continuation starting with the first this-many characters of the prior text is a restart.
RESTART_PREFIX = 60


def decide(prior: str, head: str) -> tuple[Literal["append", "restart"], int]:
    """Given the prior text and the first characters of the continuation, return the mode and
    how many leading characters of the continuation to drop."""
    probe = prior.lstrip()[:RESTART_PREFIX]
    if probe and head.lstrip().startswith(probe):
        return "restart", 0
    longest = min(len(prior), len(head), MAX_OVERLAP)
    for k in range(longest, MIN_OVERLAP - 1, -1):
        if prior.endswith(head[:k]):
            return "append", k
    return "append", 0
