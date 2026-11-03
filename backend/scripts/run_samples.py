"""Drive the five sample checks end to end through the API and check what they expect.

    uv run python scripts/run_samples.py                # all five
    uv run python scripts/run_samples.py check_3        # one
    uv run python scripts/run_samples.py --quick        # shorter pauses

Needs the API and worker running, and uses the real LLM. The sample checks are read from
data/dummy_data.json here, in this test script only: they never go into the database, the
agent's tools or its prompts.

What is not automated: restarting the backend during check_4's pause (verified by hand,
see docs/PROGRESS.md) and a pause of several hours (covered by the workflow tests).
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

API = "http://localhost:8000"
DATA = Path(__file__).resolve().parents[2] / "data" / "dummy_data.json"
WAIT_S = 420  # generous: calls queue behind the free tier's per-minute limit

results: list[tuple[str, str, bool, str]] = []


def check(case: str, what: str, ok: bool, detail: str = "") -> bool:
    results.append((case, what, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {what}" + (f" — {detail}" if detail else ""))
    return ok


class Check:
    """One check under test: its events, and the reviewer's actions."""

    def __init__(self, query: str) -> None:
        r = httpx.post(f"{API}/checks", json={"query": query}, timeout=10)
        r.raise_for_status()
        self.id = r.json()["id"]
        print(f"  check {self.id}")

    def events(self, after: int = 0) -> list[dict[str, Any]]:
        out = []
        url = f"{API}/checks/{self.id}/events?after={after}"
        with httpx.stream("GET", url, timeout=2) as r:
            try:
                for line in r.iter_lines():
                    if line.startswith("data: "):
                        out.append(json.loads(line[6:]))
            except httpx.ReadTimeout:
                pass
        return out

    def last_seq(self) -> int:
        evs = self.events()
        return evs[-1]["seq"] if evs else 0

    def wait(self, pred, after: int = 0, timeout: float = WAIT_S, what: str = "") -> list | None:
        """Poll until pred(events after `after`) is true; returns those events."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            evs = self.events(after)
            if pred(evs):
                return evs
            if any(e["type"] == "status" and e["payload"]["status"] == "error" for e in evs):
                err = [e for e in evs if e["payload"].get("status") == "error"][-1]["payload"]
                print(f"  ! error waiting for {what}: {err.get('message')} {err.get('detail', '')}")
                return None
            time.sleep(2)
        print(f"  ! timed out waiting for {what}")
        return None

    def wait_status(self, status: str, after: int = 0, what: str = "") -> list | None:
        return self.wait(
            lambda evs: any(
                e["type"] == "status" and e["payload"]["status"] == status for e in evs
            ),
            after,
            what=what or status,
        )

    def _post(self, path: str, body: dict[str, Any]) -> None:
        httpx.post(f"{API}/checks/{self.id}/{path}", json=body, timeout=10).raise_for_status()

    def answer(self, question_id: str, value: Any) -> None:
        self._post(
            "answers", {"command_id": str(uuid.uuid4()), "question_id": question_id, "value": value}
        )

    def feedback(self, text: str, target: dict | None = None) -> int:
        """Send feedback; returns the seq to look after for its round."""
        after = self.last_seq()
        self._post("feedback", {"command_id": str(uuid.uuid4()), "text": text, "target": target})
        return after

    def control(self, action: str) -> None:
        self._post(action, {"command_id": str(uuid.uuid4())})

    def round_done(self, after: int, n: int = 1, what: str = "feedback round") -> list | None:
        return self.wait(
            lambda evs: sum(e["type"] == "feedback_result" for e in evs) >= n, after, what=what
        )


# --- helpers over events ---------------------------------------------------------


def of(evs: list, type_: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in evs if e["type"] == type_]


def latest_findings(evs: list) -> dict[str, Any]:
    f = of(evs, "findings")
    return f[-1] if f else {"concerns": []}


def cited(evs: list) -> set[str]:
    return {i for c in latest_findings(evs)["concerns"] for i in c["evidence_ids"]}


def latest_report(evs: list) -> str:
    r = of(evs, "report")
    return r[-1]["markdown"] if r else ""


def stream_matches_reports(evs: list) -> tuple[bool, str]:
    """Fold deltas like the UI does: the visible text of every report equals the final one."""
    texts: dict[str, str] = {}
    for e in evs:
        p = e["payload"]
        if e["type"] == "message_start":
            texts[p["message_id"]] = ""
        elif e["type"] == "message_delta":
            texts[p["message_id"]] = texts.get(p["message_id"], "") + p["text"]
    bad = [
        r["message_id"] for r in of(evs, "report") if texts.get(r["message_id"]) != r["markdown"]
    ]
    return not bad, f"mismatched: {bad}" if bad else f"{len(of(evs, 'report'))} report(s)"


def pause_on_first_report_delta(c: Check, after: int = 0) -> str:
    """Follow the live stream and pause the moment the report starts arriving.

    Gemini sends the report in a few large chunks, so polling would miss the window.
    Returns "paused", "missed" (the report finished first) or "none" (no report)."""
    deadline = time.time() + WAIT_S
    url = f"{API}/checks/{c.id}/events?after={after}"
    while time.time() < deadline:
        try:
            with httpx.stream("GET", url, timeout=httpx.Timeout(10, read=WAIT_S)) as r:
                for line in r.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    e = json.loads(line[6:])
                    p = e["payload"]
                    if e["type"] == "message_delta" and p["message_id"].startswith("report:"):
                        c.control("pause")
                        return "paused"
                    if e["type"] == "report":
                        return "missed"
                    if e["type"] == "status" and p["status"] in ("completed", "error"):
                        return "none"
        except httpx.ReadTimeout:
            pass
    return "none"


def pause_mid_report(c: Check, name: str, hold_s: float, after: int = 0) -> None:
    outcome = pause_on_first_report_delta(c, after)
    if outcome != "paused":
        print(
            f"  (report {outcome}: no pause window this run; scripts/pause_mid_report.py covers it)"
        )
        return
    paused = c.wait_status("paused", after)
    check(name, "pause while the report streams", paused is not None)
    if paused is None:
        return
    time.sleep(hold_s)
    before = len(c.events())
    time.sleep(2)
    check(name, "nothing streams while paused", len(c.events()) == before)
    c.control("resume")


# --- the five checks ---------------------------------------------------------------


def is_rating_chart(ch: dict) -> bool:
    """A chart of average rating over time, however the agent set it up."""
    return ch["tool"] == "chart" and any(
        m["metric"] == "avg_rating" for m in ch["spec"].get("series", [])
    )


def check_1(case: dict, hold_s: float) -> None:
    name = case["id"]
    c = Check(case["reviewer_input"])
    evs = c.wait(lambda evs: of(evs, "question"), what="question")
    q = of(evs or [], "question")
    ok = check(
        name,
        "single-choice question with the similar names",
        bool(q) and q[0]["kind"] == "single" and len(q[0]["options"]) >= 3,
        f"{len(q[0]['options'])} options" if q else "",
    )
    if not ok:
        return
    c.answer(q[0]["question_id"], "cmp_001")  # Brightline Logistics Ltd
    pause_mid_report(c, name, hold_s)
    evs = c.wait_status("completed", what="first report")
    if evs is None:
        return
    check(name, "report continues cleanly after pause/resume", *stream_matches_reports(evs))

    after = c.feedback("Show me how their review ratings have changed month by month.")
    evs = c.round_done(after)
    charts = of(evs or [], "chart")
    check(
        name,
        "monthly rating chart on request",
        any(is_rating_chart(ch) for ch in charts)
        or any(is_rating_chart(ch) for ch in of(c.events(), "chart")),
    )

    after = c.feedback("Actually, I meant the Inc one. Redo the check for that company.")
    evs = c.round_done(after)
    picked = of(evs or [], "company_selected")
    check(
        name,
        "switches to Brightline Logistics Inc (cmp_002)",
        bool(picked) and picked[-1]["company_id"] == "cmp_002",
    )
    check(name, "new report for the Inc company", bool(of(evs or [], "report")))


def check_2(case: dict, hold_s: float) -> None:
    name = case["id"]
    wrong = {"ev_news_004_02", "ev_news_004_04"}
    c = Check(case["reviewer_input"])
    evs = c.wait_status("completed", what="first report")
    if evs is None:
        return
    print(f"  (first pass cites the clinic articles: {sorted(cited(evs) & wrong) or 'no'})")

    text = case["follow_ups"][1]["detail"]
    after = c.feedback(text)
    if c.round_done(after) is None:
        return
    evs = c.events()
    check(name, "excluded articles are gone from the findings", not (cited(evs) & wrong))
    check(name, "and from the report", not any(w in latest_report(evs) for w in wrong))

    after = c.feedback("Put the remaining news on a timeline.")
    evs = c.round_done(after)
    timelines = [ch for ch in of(c.events(), "chart") if ch["tool"] == "chart_news_timeline"]
    ok = bool(timelines)
    check(
        name,
        "news timeline without the excluded articles",
        ok and not ({e["id"] for e in timelines[-1]["spec"]["events"]} & wrong),
    )

    after = c.last_seq()
    for _ in range(2):
        c.feedback(text)
    evs = c.round_done(after, n=2, what="two repeats")
    outcomes = [r["outcome"] for r in of(evs or [], "feedback_result")]
    check(
        name,
        "same feedback twice → both no-ops",
        outcomes == ["duplicate", "duplicate"],
        str(outcomes),
    )
    check(name, "no LLM calls for the repeats", not of(evs or [], "usage"))


def check_3(case: dict, hold_s: float) -> None:
    name = case["id"]
    c = Check(case["reviewer_input"])
    evs = c.wait_status("completed", what="first report")
    if evs is None:
        return
    trend = [ch for ch in of(evs, "chart") if is_rating_chart(ch)]
    check(name, "rating drop charted without being asked", bool(trend))
    if not trend:
        return
    cid = trend[-1]["chart_id"]

    after = c.feedback("Only show the last 12 months on that chart.", {"kind": "chart", "id": cid})
    evs = c.round_done(after)
    upd = [ch for ch in of(evs or [], "chart") if ch["chart_id"] == cid]
    check(
        name,
        "same chart updated to the last 12 months",
        bool(upd) and upd[-1]["params"].get("last_months") == 12 and upd[-1]["version"] > 1,
    )

    after = c.feedback("Mark the date the company was sold on the chart.")
    evs = c.round_done(after)
    chart = [ch for ch in of(c.events(), "chart") if ch["chart_id"] == cid][-1]
    check(
        name,
        "sale date marked, 12-month range kept",
        any(m["date"] == "2026-01-22" for m in chart["spec"]["markers"])
        and chart["params"].get("last_months") == 12,
        str([m["date"] for m in chart["spec"]["markers"]]),
    )

    check(
        name,
        "a refresh shows exactly what streamed (replay == final)",
        *stream_matches_reports(c.events()),
    )

    after = c.feedback("Compare the average rating before and after the sale in a table.")
    evs = c.round_done(after)
    tables = [ch for ch in of(c.events(), "chart") if ch["tool"] == "table_rating_periods"]
    check(
        name,
        "before/after table",
        bool(tables),
        str([(r["period"], r["avg_rating"]) for r in tables[-1]["spec"]["rows"]]) if tables else "",
    )


def check_4(case: dict, hold_s: float) -> None:
    name = case["id"]
    c = Check(case["reviewer_input"])
    pause_mid_report(c, name, hold_s)
    evs = c.wait_status("completed", what="first report")
    if evs is None:
        return
    check(
        name, "long report continues after pause (visible == final)", *stream_matches_reports(evs)
    )

    after = c.feedback("Did you check news under the company's previous name?")
    if c.round_done(after) is None:
        return
    evs = c.events()
    text = latest_report(evs) + json.dumps(latest_findings(evs))
    check(
        name,
        "2023 data exposure under Leafline Apps is included",
        "Leafline" in text and ("exposure" in text.lower() or "breach" in text.lower()),
    )


def check_5(case: dict, hold_s: float) -> None:
    name = case["id"]
    c = Check(case["reviewer_input"])
    evs = c.wait(lambda evs: of(evs, "question"), what="first question")
    q = of(evs or [], "question")
    if not check(
        name,
        "multiple-choice question about which sources to trust",
        bool(q) and q[0]["kind"] == "multi",
        q[0]["question"][:80] if q else "",
    ):
        return
    picks = [
        o["id"]
        for o in q[0]["options"]
        if any(w in o["label"].lower() for w in ("review", "news"))
        and "record" not in o["label"].lower()
    ]
    c.answer(q[0]["question_id"], picks)
    evs = c.wait(lambda evs: len(of(evs, "question")) >= 2, what="free-text question")
    qs = of(evs or [], "question")
    if not check(
        name,
        "then a free-text question",
        len(qs) >= 2 and qs[1]["kind"] == "text",
        qs[1]["question"][:80] if len(qs) >= 2 else "",
    ):
        return
    c.answer(qs[1]["question_id"], "Whether the new business is a separate legal company.")
    evs = c.wait_status("completed", what="first report")
    if evs is None:
        return
    check(
        name,
        "findings address the separate legal company question",
        "legal" in (latest_report(evs) + json.dumps(latest_findings(evs))).lower(),
    )

    rounds = [
        "Make the summary two sentences.",
        "Put the recommendation in bold.",
        "Mention the relaunch date in the summary.",
        "Shorten the customer reviews section.",
        "Use 'relaunched business' instead of 'new business'.",
        "Add an open question about who owns the relaunched business.",
        "Make the concerns section more concise.",
        "Remove the word 'significant' everywhere.",
        "Keep the report under 350 words.",
        "Name the sources the reviewer chose to rely on in the summary.",
        "Make the recommendation reason one sentence.",
    ]
    after = c.last_seq()
    for text in rounds:
        c.feedback(text, {"kind": "report"})
    evs = c.round_done(after, n=len(rounds), what=f"{len(rounds)} feedback rounds")
    res = of(evs or [], "feedback_result")
    check(
        name,
        f"{len(rounds)} feedback rounds in a row all handled",
        len(res) == len(rounds),
        f"{sum(r['outcome'] == 'applied' for r in res)} applied",
    )
    report = latest_report(c.events())
    check(
        name,
        "earlier wording requests survive later rewrites (recommendation still bold)",
        "## Recommendation\n**" in report
        or "\n**Proceed" in report
        or "**Do not" in report
        or "**Needs" in report,
    )


CASES = {
    "check_1": check_1,
    "check_2": check_2,
    "check_3": check_3,
    "check_4": check_4,
    "check_5": check_5,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("only", nargs="*", help="check ids, e.g. check_3")
    ap.add_argument("--quick", action="store_true", help="hold pauses for 5 s instead of 60 s")
    a = ap.parse_args()
    samples = {c["id"]: c for c in json.loads(DATA.read_text())["sample_checks"]}
    hold = 5 if a.quick else 60
    for cid in a.only or list(CASES):
        print(f"\n{cid}: {samples[cid]['what_this_tests']}")
        t0 = time.time()
        try:
            CASES[cid](samples[cid], hold)
        except Exception as e:  # keep going with the other checks
            check(cid, "ran without crashing", False, repr(e))
        print(f"  ({time.time() - t0:.0f}s)")

    failed = [r for r in results if not r[2]]
    print(f"\n{len(results) - len(failed)}/{len(results)} expectations met")
    for case, what, _, detail in failed:
        print(f"  FAIL {case}: {what} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
