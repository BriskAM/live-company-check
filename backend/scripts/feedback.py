"""Send reviewer feedback to a check and print what the round did.

    uv run python scripts/feedback.py <check_id> "Leave out ev_news_004_02" [--target chart:chart-1]
    uv run python scripts/feedback.py <check_id> "same text" --times 2   # repeat test
    uv run python scripts/feedback.py <check_id> --show                  # just print the state

Waits until the check is completed again, then prints the events after the feedback:
agent replies, chart versions, findings changes, report revisions and the round's result.
"""

import argparse
import json
import time
import uuid

import httpx

API = "http://localhost:8000"


def events(check_id: str, after: int = 0) -> list[dict]:
    out = []
    with httpx.stream("GET", f"{API}/checks/{check_id}/events?after={after}", timeout=2) as r:
        try:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    out.append(json.loads(line[6:]))
        except httpx.ReadTimeout:
            pass
    return out


def wait_idle(check_id: str, timeout: float = 600) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = httpx.get(f"{API}/checks/{check_id}").json()["status"]
        if status in ("completed", "error", "waiting_for_input"):
            return status
        time.sleep(2)
    return "timeout"


def show(evs: list[dict]) -> None:
    texts: dict[str, str] = {}
    for e in evs:
        p, t = e["payload"], e["type"]
        if t == "message_start":
            texts[p["message_id"]] = ""
        elif t == "message_delta":
            texts[p["message_id"]] = texts.get(p["message_id"], "") + p["text"]
        elif t == "message_end" and p["message_id"].startswith("analyst"):
            print(f"  agent: {texts.get(p['message_id'], '').strip()}")
        elif t == "tool":
            if p.get("status") == "started":
                print(f"  tool {p['name']} {json.dumps(p.get('args', {}))}")
        elif t == "chart":
            print(f"  chart {p['chart_id']} v{p['version']} {p['title']} params={p['params']}")
        elif t == "findings":
            ids = [c["id"] for c in p["concerns"]]
            print(f"  findings {ids} changes={p.get('changes')}")
        elif t == "evidence_excluded":
            print(f"  excluded {p['ids']} ({p['reason']})")
        elif t == "report":
            revised = bool(p.get("previous"))
            print(f"  report {p['message_id']} ({len(p['markdown'])} chars, revised={revised})")
        elif t == "question":
            print(f"  question [{p['kind']}] {p['question']} {[o['label'] for o in p['options']]}")
        elif t == "feedback":
            print(f"  feedback {p['id'][:8]} queued={p['queued']}: {p['text']}")
        elif t == "feedback_result":
            detail = p.get("changes") or p.get("message", "")
            print(f"  result {p['feedback_id'][:8]}: {p['outcome']} {detail}")
        elif t == "status" and p["status"] in ("error", "waiting_for_input", "completed"):
            print(f"  status {p['status']} {p.get('message', '')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("check_id")
    ap.add_argument("text", nargs="?")
    ap.add_argument("--target", help="kind:id, e.g. chart:chart-1 or report")
    ap.add_argument("--times", type=int, default=1)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    before = events(a.check_id)
    if a.show or not a.text:
        show(before)
        return
    last = before[-1]["seq"] if before else 0
    target = None
    if a.target:
        kind, _, tid = a.target.partition(":")
        target = {"kind": kind, "id": tid or None}
    for _ in range(a.times):
        r = httpx.post(
            f"{API}/checks/{a.check_id}/feedback",
            json={"command_id": str(uuid.uuid4()), "text": a.text, "target": target},
        )
        r.raise_for_status()
    t0 = time.time()
    time.sleep(3)
    status = wait_idle(a.check_id)
    # Repeats are queued; wait until every feedback has a result.
    while a.times > 1 and status == "completed":
        evs = events(a.check_id, last)
        if sum(e["type"] == "feedback_result" for e in evs) >= a.times:
            break
        time.sleep(2)
        status = wait_idle(a.check_id)
    print(f"{status} after {time.time() - t0:.0f}s")
    show(events(a.check_id, last))


if __name__ == "__main__":
    main()
