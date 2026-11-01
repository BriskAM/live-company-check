"""Feedback round building blocks, without the LLM: tool handlers, dedupe, graph entry."""

from types import SimpleNamespace

from company_check.agent import graph, toolbox
from company_check.worker.activities import DONE, _graph_input
from company_check.worker.types import RunAgentInput


class ListSink:
    def __init__(self):
        self.events: list[tuple[str, dict, str | None]] = []

    async def emit(self, type, payload, key=None):
        self.events.append((type, payload, key))

    def of(self, type):
        return [p for t, p, _ in self.events if t == type]


def _state():
    return {
        "mode": "feedback",
        "as_of": "2026-11-01",
        "reviews": [
            {"id": "r1", "date": "2026-01-10", "rating": 5, "text": ""},
            {"id": "r2", "date": "2026-02-10", "rating": 1, "text": ""},
        ],
        "news": [
            {"id": "n1", "date": "2026-01-05", "headline": "Lawsuit", "text": ""},
            {"id": "n2", "date": "2026-02-05", "headline": "Inspection", "text": ""},
            {"id": "n3", "date": "2026-03-05", "headline": "New depot", "text": ""},
        ],
        "concerns": [
            {
                "id": "c1",
                "title": "Lawsuit",
                "detail": "",
                "severity": "high",
                "evidence_ids": ["n1"],
            },
            {
                "id": "c2",
                "title": "Bad service",
                "detail": "",
                "severity": "medium",
                "evidence_ids": ["r2", "n2"],
            },
            {
                "id": "c3",
                "title": "Growth",
                "detail": "",
                "severity": "low",
                "evidence_ids": ["n3"],
            },
        ],
        "recommendation": {"decision": "do_not_proceed", "reason": "lawsuit"},
        "charts": {
            "chart-1": {
                "tool": "chart_news_timeline",
                "params": {},
                "title": "News",
                "version": 1,
                "digest": "old",
            },
            "chart-2": {
                "tool": "chart_rating_trend",
                "params": {},
                "title": "Ratings",
                "version": 1,
            },
        },
    }


async def test_exclude_evidence_touches_only_dependent_concerns_and_charts():
    sink = ListSink()
    turn = toolbox.Turn(_state(), sink)
    # Draw chart-2 once so its digest is current; the exclusion doesn't change its data.
    spec = await toolbox.build_spec(turn.view(), "chart_rating_trend", {})
    turn.charts["chart-2"]["digest"] = toolbox._digest(spec)

    reply = await toolbox.run_call(
        turn,
        {
            "name": "exclude_evidence",
            "id": "t1",
            "args": {"evidence_ids": ["n1", "n2"], "reason": "about the clinic"},
        },
    )

    concerns = {c["id"]: c for c in turn.update["concerns"]}
    assert "c1" not in concerns  # its only evidence is gone
    assert concerns["c2"]["evidence_ids"] == ["r2"]  # trimmed
    assert concerns["c3"] == _state()["concerns"][2]  # untouched
    assert turn.update["excluded_ids"] == ["n1", "n2"]
    assert turn.update["report_dirty"] is True
    # Only the timeline is redrawn; the rating chart's data didn't change.
    assert [p["chart_id"] for p in sink.of("chart")] == ["chart-1"]
    assert [e["id"] for e in sink.of("chart")[0]["spec"]["events"]] == ["n3"]
    assert "c1" in reply


async def test_excluding_uncited_news_does_not_rewrite_the_report():
    state = {**_state(), "charts": {}}
    state["news"].append({"id": "n9", "date": "2026-04-01", "headline": "Other", "text": ""})
    turn = toolbox.Turn(state, ListSink())
    await toolbox.run_call(
        turn,
        {"name": "exclude_evidence", "id": "t2", "args": {"evidence_ids": ["n9"], "reason": "x"}},
    )
    assert turn.update["excluded_ids"] == ["n9"]
    assert not turn.update.get("report_dirty")


async def test_excluding_again_changes_nothing():
    state = {**_state(), "excluded_ids": ["n1"]}
    sink = ListSink()
    turn = toolbox.Turn(state, sink)
    reply = await toolbox.run_call(
        turn,
        {
            "name": "exclude_evidence",
            "id": "t1",
            "args": {"evidence_ids": ["n1"], "reason": "again"},
        },
    )
    assert "already excluded" in reply
    assert turn.update == {"round_ops": ["exclude_evidence"]}  # no state changes
    assert sink.events == []


async def test_update_findings_patches_named_concerns_only():
    sink = ListSink()
    turn = toolbox.Turn(_state(), sink)
    reply = await toolbox.run_call(
        turn,
        {
            "name": "update_findings",
            "id": "t2",
            "args": {
                "remove_concern_ids": ["c1"],
                "upsert_concerns": [
                    {
                        "id": "c2",
                        "title": "Service complaints",
                        "detail": "x",
                        "severity": "low",
                        "evidence_ids": ["r2"],
                    },
                    {"title": "New", "detail": "y", "severity": "low", "evidence_ids": ["n3"]},
                ],
                "recommendation": "proceed_with_caution",
            },
        },
    )
    concerns = turn.update["concerns"]
    assert [c["id"] for c in concerns] == ["c2", "c3", "c4"]
    assert concerns[1] == _state()["concerns"][2]  # untouched
    assert turn.update["recommendation"] == {
        "decision": "proceed_with_caution",
        "reason": "lawsuit",
    }
    assert "removed c1" in reply
    assert sink.of("findings")[0]["changes"][0] == "removed c1"


async def test_update_findings_rejects_excluded_evidence():
    turn = toolbox.Turn({**_state(), "excluded_ids": ["n3"]}, ListSink())
    reply = await toolbox.run_call(
        turn,
        {
            "name": "update_findings",
            "id": "t3",
            "args": {
                "upsert_concerns": [
                    {"title": "x", "detail": "", "severity": "low", "evidence_ids": ["n3"]}
                ]
            },
        },
    )
    assert reply.startswith("Error:") and "n3" in reply


async def test_new_chart_ids_never_reuse_old_ones():
    turn = toolbox.Turn({**_state(), "charts": {}, "chart_seq": 4}, ListSink())
    await toolbox.run_call(turn, {"name": "chart_rating_trend", "id": "t4", "args": {}})
    assert list(turn.charts) == ["chart-5"]


async def test_repeated_feedback_is_a_no_op():
    fb = {"id": "f2", "text": "ev_news_004_02 is about the clinic. Leave it out!", "target": None}
    log = [
        {
            "id": "f1",
            "text": "",
            "outcome": "applied",
            "norm": graph._norm({**fb, "text": "EV_NEWS_004_02 is about the clinic, leave it out"}),
        }
    ]
    sink = ListSink()
    rt = SimpleNamespace(context=SimpleNamespace(sink=sink))
    update = await graph.feedback_intake({"feedback": fb, "feedback_log": log}, rt)
    assert sink.of("feedback_result")[0]["outcome"] == "duplicate"
    assert graph.after_intake({"feedback": fb, **update}) == graph.END


def _snapshot(**kw):
    base = {"created_at": "t", "interrupts": (), "next": (), "values": {}}
    return SimpleNamespace(**{**base, **kw})


def test_graph_input_starts_a_feedback_round_once():
    fb = {"id": "f1", "text": "x", "target": None}
    inp = RunAgentInput("c", "Acme", {}, fb)
    assert _graph_input(_snapshot(), inp) == ({"feedback": fb}, [])
    # A retry after the round finished must not run it again.
    assert _graph_input(_snapshot(values={"feedback": fb}), inp) == (DONE, [])
    # Mid-round (e.g. paused): continue from the checkpoint.
    assert _graph_input(_snapshot(next=("analyst",), values={"feedback": fb}), inp) == (None, [])
    # No feedback and nothing left: done.
    assert _graph_input(_snapshot(), RunAgentInput("c", "Acme")) == (DONE, [])


def test_repeat_of_an_earlier_exclusion_is_a_no_op_even_after_other_feedback():
    exclude = {
        "norm": "|leave out n1",
        "outcome": "applied",
        "ops": ["exclude_evidence"],
        "excluded": ["n1"],
    }
    chart = {"norm": "|timeline please", "outcome": "applied", "ops": ["chart_news_timeline"]}
    state = {"feedback_log": [exclude, chart], "excluded_ids": ["n1"]}
    assert graph._already_applied(state, "|leave out n1")
    assert not graph._already_applied(state, "|something new")
    # A repeated chart request after other changes is not assumed to be a no-op.
    other = {"norm": "|x", "outcome": "applied", "ops": ["update_findings"]}
    assert not graph._already_applied({**state, "feedback_log": [chart, other]}, "|timeline please")


async def test_chart_update_that_changes_nothing_keeps_the_version():
    sink = ListSink()
    turn = toolbox.Turn({**_state(), "charts": {}}, sink)
    await toolbox.run_call(turn, {"name": "chart_rating_trend", "id": "a", "args": {}})
    reply = await toolbox.run_call(
        turn, {"name": "chart_rating_trend", "id": "b", "args": {"chart_id": "chart-1"}}
    )
    assert "nothing changed" in reply
    assert turn.charts["chart-1"]["version"] == 1
    assert len(sink.of("chart")) == 1
