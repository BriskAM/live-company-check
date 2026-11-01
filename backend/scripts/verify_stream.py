"""Fold a check's event log the way the UI does and compare the visible report with the final one.

    uv run python scripts/verify_stream.py <check_id>

Prints how many times each message was (re)started and whether the text a viewer would
have on screen equals the authoritative final report — i.e. no gaps and no repeats.
"""

import json
import sys

import httpx

API = "http://localhost:8000"


def main(check_id: str) -> int:
    texts: dict[str, str] = {}
    starts: dict[str, int] = {}
    reports: dict[str, str] = {}
    statuses: list[str] = []
    with httpx.stream("GET", f"{API}/checks/{check_id}/events", timeout=3) as r:
        try:
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                e = json.loads(line[6:])
                p = e["payload"]
                if e["type"] == "message_start":
                    texts[p["message_id"]] = ""
                    starts[p["message_id"]] = starts.get(p["message_id"], 0) + 1
                elif e["type"] == "message_delta":
                    texts[p["message_id"]] = texts.get(p["message_id"], "") + p["text"]
                elif e["type"] == "report":
                    reports[p["message_id"]] = p["markdown"]
                elif e["type"] == "status":
                    statuses.append(p["status"])
        except httpx.ReadTimeout:
            pass  # the stream stays open; we have everything committed so far

    print("statuses:", " → ".join(statuses))
    ok = True
    for mid, final in reports.items():
        same = texts.get(mid) == final
        ok &= same
        print(
            f"{mid}: starts={starts.get(mid)} visible={len(texts.get(mid, ''))} "
            f"final={len(final)} {'MATCH' if same else 'MISMATCH'}"
        )
    return 0 if ok and reports else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
