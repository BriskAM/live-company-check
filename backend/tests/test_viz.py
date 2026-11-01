from company_check.agent.viz import news_timeline, rating_periods, rating_trend, resolve_range


def _r(i: int, d: str, rating: int) -> dict:
    return {"id": f"r{i}", "date": d, "rating": rating, "text": ""}


STATE = {
    "as_of": "2026-11-01",
    "reviews": [
        _r(1, "2025-10-05", 5),
        _r(2, "2025-11-10", 5),
        _r(3, "2026-01-15", 4),
        _r(4, "2026-03-02", 2),
        _r(5, "2026-10-20", 2),
    ],
    "news": [
        {"id": "n1", "date": "2026-01-22", "headline": "Sold", "text": ""},
        {"id": "n2", "date": "2025-04-10", "headline": "Anniversary", "text": ""},
        {"id": "n3", "date": "2026-05-08", "headline": "Other firm", "text": ""},
    ],
    "excluded_ids": ["n3"],
}


def test_last_12_months_counts_back_from_last_full_month():
    assert resolve_range("2026-11-01", None, None, 12) == ("2025-11", "2026-10")
    assert resolve_range("2026-11-01", "2025-06-01", None, None) == ("2025-06", None)


def test_rating_trend_filters_range_and_markers():
    spec = rating_trend(
        STATE,
        {
            "last_months": 12,
            "markers": [
                {"date": "2026-01-22", "label": "Sold"},
                {"date": "2024-01-01", "label": "Too early"},
            ],
        },
    )
    assert [d["month"] for d in spec["data"]] == ["2025-11", "2026-01", "2026-03", "2026-10"]
    assert spec["markers"] == [{"date": "2026-01-22", "month": "2026-01", "label": "Sold"}]
    assert spec["range"] == {"start": "2025-11", "end": "2026-10"}


def test_news_timeline_is_sorted_and_skips_excluded():
    spec = news_timeline(STATE, {})
    assert [e["id"] for e in spec["events"]] == ["n2", "n1"]
    assert spec["excluded"] == ["n3"]


def test_rating_periods_before_and_after():
    spec = rating_periods(
        STATE,
        {
            "periods": [
                {"label": "Before sale", "end": "2026-01-21"},
                {"label": "After sale", "start": "2026-01-22"},
            ]
        },
    )
    before, after = spec["rows"]
    assert (before["reviews"], before["avg_rating"]) == (3, 4.67)
    assert (after["reviews"], after["avg_rating"], after["low_ratings"]) == (2, 2, 2)


def test_rating_periods_rejects_unbounded_or_overlapping_periods():
    import pytest

    from company_check.agent.viz import PeriodError

    with pytest.raises(PeriodError, match="needs a start or an end"):
        rating_periods(
            STATE, {"periods": [{"label": "Before", "end": "2026-01"}, {"label": "After"}]}
        )
    with pytest.raises(PeriodError, match="overlap"):
        rating_periods(
            STATE,
            {"periods": [{"label": "A", "end": "2026-02"}, {"label": "B", "start": "2026-02-10"}]},
        )


def test_rating_periods_month_bounds_are_inclusive():
    spec = rating_periods(
        STATE, {"periods": [{"label": "Mar", "start": "2026-03", "end": "2026-03"}]}
    )
    assert spec["rows"][0]["reviews"] == 1
