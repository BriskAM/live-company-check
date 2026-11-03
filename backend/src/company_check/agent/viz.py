"""Chart and table builders. All numbers come from the data; the LLM only picks parameters.

Each builder takes the agent state plus tool parameters and returns a JSON spec the UI
renders. Specs are rebuilt from state whenever parameters or excluded evidence change, so a
chart never shows evidence the reviewer ruled out.
"""

import datetime
from statistics import mean
from typing import Any


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


class ChartError(ValueError):
    pass


UNITS = {"avg_rating": "rating", "count": "count", "low_rating_share": "percent"}
Y_LABELS = {"rating": "Average rating", "count": "Items", "percent": "% of reviews rated 1–2"}


def bucket_of(date: str, interval: str) -> str:
    """The x-axis bucket a YYYY-MM-DD date falls in; buckets sort as strings."""
    if interval == "year":
        return date[:4]
    if interval == "quarter":
        return f"{date[:4]}-Q{(int(date[5:7]) - 1) // 3 + 1}"
    if interval == "week":
        d = datetime.date.fromisoformat(date)
        return (d - datetime.timedelta(days=d.weekday())).isoformat()
    return date[:7]


def _metric(metric: str, items: list[dict[str, Any]]) -> float | None:
    if metric == "count":
        return len(items)
    if not items:
        return None
    if metric == "avg_rating":
        return round(mean(i["rating"] for i in items), 2)
    return round(100 * sum(1 for i in items if i["rating"] <= 2) / len(items), 1)


def _check_series(series: list[dict[str, Any]]) -> str:
    if not 1 <= len(series) <= 4:
        raise ChartError("give one to four series")
    for sq in series:
        if sq["source"] == "news" and sq["metric"] != "count":
            raise ChartError("news can only be counted: use metric 'count' for source 'news'")
        if sq["source"] == "news" and (
            sq.get("rating_min") is not None or sq.get("rating_max") is not None
        ):
            raise ChartError("rating_min/rating_max only apply to reviews")
        if sq["metric"] == "low_rating_share" and (
            sq.get("rating_min") is not None or sq.get("rating_max") is not None
        ):
            raise ChartError(
                "low_rating_share is already the % of reviews rated 1-2 out of all reviews; "
                "remove rating_min/rating_max (they would change what it is measured against)"
            )
    units = {UNITS[sq["metric"]] for sq in series}
    if len(units) > 1:
        raise ChartError(
            f"series mix {sorted(units)}; one chart has one y axis, so draw them as separate charts"
        )
    return units.pop()


def _select(items: list[dict[str, Any]], sq: dict[str, Any], excluded: set[str], span) -> list:
    start, end = span
    needle = (sq.get("contains") or "").lower()
    lo, hi = sq.get("rating_min"), sq.get("rating_max")
    out = []
    for i in items:
        if i["id"] in excluded:
            continue
        if (start and i["date"][:7] < start) or (end and i["date"][:7] > end):
            continue
        if lo is not None and i["rating"] < lo:
            continue
        if hi is not None and i["rating"] > hi:
            continue
        if needle and needle not in f"{i.get('headline', '')} {i['text']}".lower():
            continue
        out.append(i)
    return out


def chart(
    state: dict[str, Any], params: dict[str, Any], companies: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Line, bar or area chart of one to four series, each a metric over reviews or news.

    `companies` maps company id -> {name, reviews, news}. The agent only describes the series;
    every number is computed here from the data, minus excluded evidence."""
    series = params.get("series") or []
    unit = _check_series(series)
    interval = params.get("interval") or "month"
    excluded = set(state.get("excluded_ids", []))
    span = resolve_range(
        state["as_of"], params.get("start"), params.get("end"), params.get("last_months")
    )

    meta, by_bucket = [], {}
    for n, sq in enumerate(series):
        key = f"s{n}"
        company = companies[sq["company_id"]]
        items = _select(company[sq["source"]], sq, excluded, span)
        groups: dict[str, list] = {}
        for i in items:
            groups.setdefault(bucket_of(i["date"], interval), []).append(i)
        for b, group in groups.items():
            row = by_bucket.setdefault(b, {"bucket": b})
            row[key] = _metric(sq["metric"], group)
            row[f"{key}_n"] = len(group)
        label = sq.get("label") or f"{company['name']} {sq['metric'].replace('_', ' ')}"
        meta.append(
            {
                "key": key,
                "label": label,
                "company_id": sq["company_id"],
                "source": sq["source"],
                "metric": sq["metric"],
                "total": len(items),
            }
        )
    rows = [by_bucket[b] for b in sorted(by_bucket)]
    for row in rows:
        for m in meta:  # a bucket with nothing counted is 0; an average of nothing is a gap
            if m["metric"] == "count":
                row.setdefault(m["key"], 0)
                row.setdefault(f"{m['key']}_n", 0)
    markers = sorted(
        (
            {"date": m["date"], "bucket": bucket_of(m["date"], interval), "label": m["label"]}
            for m in params.get("markers") or []
            if bucket_of(m["date"], interval) in by_bucket
        ),
        key=lambda m: m["date"],
    )
    return {
        "type": "chart",
        "kind": params.get("kind") or "line",
        "interval": interval,
        "y": {
            "unit": unit,
            "label": Y_LABELS[unit],
            "domain": [1, 5] if unit == "rating" else None,
        },
        "range": {
            "start": span[0] or (rows[0]["bucket"] if rows else None),
            "end": span[1] or (rows[-1]["bucket"] if rows else None),
        },
        "series": meta,
        "data": rows,
        "markers": markers,
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
    if spec["type"] == "chart":
        if not spec["data"]:
            return "Nothing matched: no data points."
        parts = []
        for m in spec["series"]:
            pts = [(r["bucket"], r[m["key"]]) for r in spec["data"] if r.get(m["key"]) is not None]
            if not pts:
                parts.append(f"{m['label']}: nothing matched")
                continue
            lo, hi = min(pts, key=lambda p: p[1]), max(pts, key=lambda p: p[1])
            parts.append(
                f"{m['label']}: {m['total']} items, highest {hi[1]} ({hi[0]}), "
                f"lowest {lo[1]} ({lo[0]})"
            )
        return (
            f"{len(spec['data'])} {spec['interval']} points "
            f"{spec['range']['start']}..{spec['range']['end']}; "
            + "; ".join(parts)
            + f"; markers: {len(spec['markers'])}"
        )
    if spec["type"] == "news_timeline":
        return f"{len(spec['events'])} news events" + (
            f"; excluded {spec['excluded']}" if spec["excluded"] else ""
        )
    if spec["type"] == "table":
        return f"table with {len(spec['rows'])} rows: {spec['rows']}"
    return "ok"
