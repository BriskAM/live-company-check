"""Scenario: pause while the report is streaming, resume, and verify nothing is lost or repeated.

    uv run python scripts/pause_mid_report.py "Copperleaf Software" [--chars 40] [--wait 5]

Talks to the running API like a reviewer would. Exits non-zero if the final report does not
start with the text that was visible at the moment of the pause.
"""

import argparse
import asyncio
import json
import sys
import uuid

import httpx

API = "http://localhost:8000"


async def stream_events(client: httpx.AsyncClient, check_id: str, after: int = 0):
    async with client.stream(
        "GET", f"{API}/checks/{check_id}/events", params={"after": after}, timeout=None
    ) as r:
        async for line in r.aiter_lines():
            if line.startswith("data: "):
                yield json.loads(line[6:])


async def main(query: str, chars: int, wait: float) -> int:
    async with httpx.AsyncClient() as client:
        check = (await client.post(f"{API}/checks", json={"query": query})).json()
        check_id = check["id"]
        print(f"check {check_id}")

        report_id, visible = None, ""
        async for e in stream_events(client, check_id):
            p = e["payload"]
            if e["type"] == "message_start" and p["message_id"].startswith("report:"):
                report_id, visible = p["message_id"], ""
            elif e["type"] == "message_delta" and p["message_id"] == report_id:
                visible += p["text"]
                if len(visible) >= chars:
                    break
            elif e["type"] == "status" and p["status"] in ("completed", "error"):
                print("check finished before the report could be paused")
                return 1

        await client.post(f"{API}/checks/{check_id}/pause", json={"command_id": str(uuid.uuid4())})
        print(f"paused after {len(visible)} visible chars of {report_id}")
        await asyncio.sleep(wait)

        # What the reviewer sees after the pause settles (includes text flushed on cancel).
        shown = ""
        async for e in stream_events(client, check_id):
            p = e["payload"]
            if e["type"] == "message_start" and p["message_id"] == report_id:
                shown = ""
            elif e["type"] == "message_delta" and p["message_id"] == report_id:
                shown += p["text"]
            elif e["type"] == "status" and p["status"] == "paused":
                break
        status = (await client.get(f"{API}/checks/{check_id}")).json()["status"]
        print(f"status={status}; report text shown while paused: {len(shown)} chars")
        print(f"   …{shown[-120:]!r}")

        await client.post(f"{API}/checks/{check_id}/resume", json={"command_id": str(uuid.uuid4())})
        final = None
        async for e in stream_events(client, check_id):
            if e["type"] == "report" and e["payload"]["message_id"] == report_id:
                final = e["payload"]["markdown"]
            if e["type"] == "status" and e["payload"]["status"] in ("completed", "error"):
                break

        if final is None:
            print("no final report")
            return 1
        seam = len(shown)
        print(f"final report: {len(final)} chars")
        print(f"   seam: {final[max(0, seam - 80) : seam]!r} ⟂ {final[seam : seam + 80]!r}")
        ok = final.startswith(shown) and final.count(shown[:60]) == 1
        print("OK: report continued from the paused text" if ok else "FAIL: text was rewritten")
        return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--chars", type=int, default=40)
    ap.add_argument("--wait", type=float, default=5)
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.query, a.chars, a.wait)))
