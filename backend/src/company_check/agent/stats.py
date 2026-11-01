"""Deterministic review statistics. The LLM gets these numbers; it never computes them."""

from collections import defaultdict
from statistics import mean
from typing import Any

# A rating shift is only reported when both sides have enough reviews and the gap is real.
MIN_REVIEWS_PER_SIDE = 5
MIN_SHIFT = 0.75


def monthly_ratings(
    reviews: list[dict[str, Any]], start: str | None = None, end: str | None = None
) -> list[dict[str, Any]]:
    """Average rating and count per calendar month (YYYY-MM), sorted.

    `start` / `end` are inclusive ISO dates or months; reviews outside are ignored.
    """
    buckets: dict[str, list[int]] = defaultdict(list)
    for r in reviews:
        if start and r["date"] < start:
            continue
        if end and r["date"][: len(end)] > end:
            continue
        buckets[r["date"][:7]].append(r["rating"])
    return [
        {"month": m, "avg": round(mean(v), 2), "count": len(v)} for m, v in sorted(buckets.items())
    ]


def rating_shift(reviews: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the month where the average rating changes most (single change point).

    Returns None when there is no meaningful shift.
    """
    ordered = sorted(reviews, key=lambda r: r["date"])
    months = sorted({r["date"][:7] for r in ordered})
    best = None
    for m in months[1:]:
        before = [r["rating"] for r in ordered if r["date"][:7] < m]
        after = [r["rating"] for r in ordered if r["date"][:7] >= m]
        if len(before) < MIN_REVIEWS_PER_SIDE or len(after) < MIN_REVIEWS_PER_SIDE:
            continue
        delta = mean(after) - mean(before)
        if best is None or abs(delta) > abs(best["delta"]):
            best = {
                "from_month": m,
                "avg_before": round(mean(before), 2),
                "avg_after": round(mean(after), 2),
                "delta": round(delta, 2),
                "reviews_before": len(before),
                "reviews_after": len(after),
            }
    if best is None or abs(best["delta"]) < MIN_SHIFT:
        return None
    return best


def review_summary(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    if not reviews:
        return {"count": 0}
    return {
        "count": len(reviews),
        "avg": round(mean(r["rating"] for r in reviews), 2),
        "first": min(r["date"] for r in reviews),
        "last": max(r["date"] for r in reviews),
        "shift": rating_shift(reviews),
    }
