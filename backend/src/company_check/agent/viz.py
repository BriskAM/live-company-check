"""Chart and table builders. All numbers come from the data; the LLM only picks parameters.

Each builder takes the agent state plus tool parameters and returns a JSON spec the UI
renders. Specs are rebuilt from state whenever parameters or excluded evidence change, so a
chart never shows evidence the reviewer ruled out.
"""

from statistics import mean
from typing import Any

from company_check.agent.stats import monthly_ratings


def _shift_month(month: str, delta: int) -> str:
    y, m = map(int, month.split("-"))
    total = y * 12 + (m - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def resolve_range(
    as_of: str, start: str | None, end: str | None, last_months: int | None
) -> tuple[str | None, str | None]:
    """Turn tool parameters into an inclusive (start_month, end_month) range.

    `last_months` counts back from the month before `as_of` (the last complete month)."""
    if last_months:
        last_full = _shift_month(as_of[:7], -1)
        return _shift_month(last_full, -(last_months - 1)), last_full
    return (start[:7] if start else None), (end[:7] if end else None)


def _active(items: list[dict[str, Any]], excluded: set[str]) -> list[dict[str, Any]]:
    return [i for i in items if i["id"] not in excluded]


def rating_trend(state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    excluded = set(state.get("excluded_ids", []))
    start, end = resolve_range(
        state["as_of"], params.get("start"), params.get("end"), params.get("last_months")
    )
    reviews = _active(state.get("reviews", []), excluded)
    data = monthly_ratings(reviews, start=start, end=end)
    markers = sorted(
        (
            {"date": m["date"], "month": m["date"][:7], "label": m["label"]}
            for m in params.get("markers", [])
            if (not start or m["date"][:7] >= start) and (not end or m["date"][:7] <= end)
        ),
        key=lambda m: m["date"],
    )
    return {
        "type": "rating_trend",
        "range": {
            "start": start or (data[0]["month"] if data else None),
            "end": end or (data[-1]["month"] if data else None),
        },
        "data": data,
        "markers": markers,
        "review_count": sum(d["count"] for d in data),
    }


def news_timeline(state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    excluded = set(state.get("excluded_ids", []))
    start, end = resolve_range(
        state["as_of"], params.get("start"), params.get("end"), params.get("last_months")
    )
    items = {n["id"]: n for n in [*state.get("news", []), *state.get("extra_news", [])]}
    events = [
        {"id": n["id"], "date": n["date"], "headline": n["headline"], "text": n["text"]}
        for n in sorted(_active(list(items.values()), excluded), key=lambda n: (n["date"], n["id"]))
        if (not start or n["date"][:7] >= start) and (not end or n["date"][:7] <= end)
    ]
    return {"type": "news_timeline", "events": events, "excluded": sorted(excluded & set(items))}


class PeriodError(ValueError):
    pass


def _day(value: str | None, *, end: bool) -> str | None:
    """Normalise YYYY or YYYY-MM to the first/last day so string comparison is inclusive."""
    if not value:
        return None
    if len(value) == 4:
        return f"{value}-12-31" if end else f"{value}-01-01"
    if len(value) == 7:
        return f"{value}-31" if end else f"{value}-01"
    return value


def normalise_periods(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate periods: each bounded on at least one side, and no two overlapping."""
    out = []
    for p in periods:
        start, end = _day(p.get("start"), end=False), _day(p.get("end"), end=True)
        if not start and not end:
            raise PeriodError(f"period {p['label']!r} needs a start or an end date")
        if start and end and start > end:
            raise PeriodError(f"period {p['label']!r} starts after it ends")
        out.append({"label": p["label"], "start": start, "end": end})
    spans = sorted(out, key=lambda p: p["start"] or "0000")
    for a, b in zip(spans, spans[1:], strict=False):
        if (a["end"] or "9999") >= (b["start"] or "0000"):
            raise PeriodError(
                f"periods {a['label']!r} and {b['label']!r} overlap; give each a start/end "
                "so they don't (e.g. before: end 2026-01-21, after: start 2026-01-22)"
            )
    return out


def rating_periods(state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Average rating per labelled period, e.g. before / after an ownership change."""
    excluded = set(state.get("excluded_ids", []))
    reviews = _active(state.get("reviews", []), excluded)
    rows = []
    for p in normalise_periods(params["periods"]):
        start, end = p["start"] or "0000-01-01", p["end"] or "9999-12-31"
        inside = [r for r in reviews if start <= r["date"] <= end]
        rows.append(
            {
                "period": p["label"],
                "from": p.get("start"),
                "to": p.get("end"),
                "reviews": len(inside),
                "avg_rating": round(mean(r["rating"] for r in inside), 2) if inside else None,
                "low_ratings": sum(1 for r in inside if r["rating"] <= 2),
            }
        )
    return {
        "type": "table",
        "columns": [
            {"key": "period", "label": "Period"},
            {"key": "from", "label": "From"},
            {"key": "to", "label": "To"},
            {"key": "reviews", "label": "Reviews", "align": "right"},
            {"key": "avg_rating", "label": "Avg rating", "align": "right"},
            {"key": "low_ratings", "label": "1–2★ reviews", "align": "right"},
        ],
        "rows": rows,
    }


def company_comparison(companies: list[dict[str, Any]]) -> dict[str, Any]:
    """Side-by-side table of similarly named companies (rows prepared by the caller)."""
    return {
        "type": "table",
        "columns": [
            {"key": "name", "label": "Company"},
            {"key": "status", "label": "Status"},
            {"key": "founded", "label": "Founded"},
            {"key": "closed_on", "label": "Closed"},
            {"key": "address", "label": "Address"},
            {"key": "reviews", "label": "Reviews", "align": "right"},
            {"key": "avg_rating", "label": "Avg rating", "align": "right"},
            {"key": "news", "label": "News", "align": "right"},
        ],
        "rows": companies,
    }


def summarize(spec: dict[str, Any]) -> str:
    """Short text the LLM gets back after creating a chart (it never sees the full data)."""
    if spec["type"] == "rating_trend":
        d = spec["data"]
        if not d:
            return "No reviews in that range."
        lo = min(d, key=lambda x: x["avg"])
        hi = max(d, key=lambda x: x["avg"])
        return (
            f"{len(d)} months {spec['range']['start']}..{spec['range']['end']}, "
            f"{spec['review_count']} reviews; highest {hi['avg']} ({hi['month']}), "
            f"lowest {lo['avg']} ({lo['month']}); markers: {len(spec['markers'])}"
        )
    if spec["type"] == "news_timeline":
        return f"{len(spec['events'])} news events" + (
            f"; excluded {spec['excluded']}" if spec["excluded"] else ""
        )
    if spec["type"] == "table":
        return f"table with {len(spec['rows'])} rows: {spec['rows']}"
    return "ok"
