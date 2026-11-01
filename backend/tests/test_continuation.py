from company_check.agent.continuation import decide

PRIOR = (
    "## Summary\nCopperleaf Software is an active company founded in 2017. "
    "It was previously known as Leafline Apps and has generally positive revi"
)


def test_plain_continuation_is_appended():
    assert decide(PRIOR, "ews from customers over the last two years.") == ("append", 0)


def test_repeated_tail_is_dropped():
    head = "previously known as Leafline Apps and has generally positive reviews overall."
    mode, drop = decide(PRIOR, head)
    assert mode == "append"
    assert head[drop:] == "ews overall."


def test_short_coincidental_overlap_is_kept():
    # "revi" matching is too short to be treated as a repeat.
    assert decide(PRIOR, "revisions to its pricing were also noted.") == ("append", 0)


def test_starting_over_is_detected():
    head = "## Summary\nCopperleaf Software is an active company founded in 2017, formerly"
    assert decide(PRIOR, head) == ("restart", 0)


def test_permanent_llm_errors_are_not_retried():
    from company_check.worker.activities import _is_permanent

    assert _is_permanent(Exception("402 RESOURCE_EXHAUSTED. Your prepayment credits are depleted"))
    assert _is_permanent(Exception("400 API_KEY_INVALID"))
    assert not _is_permanent(Exception("429 RESOURCE_EXHAUSTED. quota exceeded"))
    assert not _is_permanent(Exception("503 UNAVAILABLE high demand"))


def test_errors_are_described_for_the_reviewer():
    from company_check.worker.errors import describe

    assert "rate limit" in describe("ResourceExhausted: 429 quota exceeded")["message"]
    assert "API key" in describe("LLMConfigError: 402 credits are depleted")["message"]
    long = describe("x" * 1000)
    assert long["message"].startswith("The agent hit an unexpected error")
    assert len(long["detail"]) == 301


async def test_limiter_allows_a_burst_then_waits(monkeypatch):
    from company_check.agent import llm

    clock = [1000.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock[0])
    lim = llm.PerMinuteLimiter(rpm=3)
    assert all([lim.acquire(blocking=False) for _ in range(3)])
    assert not lim.acquire(blocking=False)  # 4th within the minute must wait
    clock[0] += 60
    assert lim.acquire(blocking=False)
