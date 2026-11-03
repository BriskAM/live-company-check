import pytest

from company_check.agent.viz import (
    ChartError,
    bucket_of,
    chart,
    news_timeline,
    rating_periods,
    resolve_range,
)


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


COMPANIES = {
    "a": {"name": "A", "reviews": STATE["reviews"], "news": STATE["news"]},
    "b": {
        "name": "B",
        "reviews": [_r(10, "2026-01-03", 1), _r(11, "2026-02-04", 2), _r(12, "2026-02-09", 4)],
        "news": [],
    },
}


def _series(**kw):
    return {"company_id": "a", "source": "reviews", "metric": "avg_rating", **kw}


def test_chart_filters_range_and_markers():
    spec = chart(
        STATE,
        {
            "last_months": 12,
            "series": [_series()],
            "markers": [
                {"date": "2026-01-22", "label": "Sold"},
                {"date": "2024-01-01", "label": "Too early"},
            ],
        },
        COMPANIES,
    )
    assert [d["bucket"] for d in spec["data"]] == ["2025-11", "2026-01", "2026-03", "2026-10"]
    assert spec["markers"] == [{"date": "2026-01-22", "bucket": "2026-01", "label": "Sold"}]
    assert spec["range"] == {"start": "2025-11", "end": "2026-10"}
    assert spec["y"]["unit"] == "rating" and spec["kind"] == "line"


def test_chart_compares_companies_with_gaps_and_exclusions():
    spec = chart(
        {**STATE, "excluded_ids": ["r3", "r12"]},
        {"start": "2026-01", "series": [_series(), _series(company_id="b")]},
        COMPANIES,
    )
    assert [s["label"] for s in spec["series"]] == ["A avg rating", "B avg rating"]
    rows = {r["bucket"]: r for r in spec["data"]}
    assert "s0" not in rows["2026-01"]  # r3 excluded, so A has a gap, not a zero
    assert rows["2026-01"]["s1"] == 1
    assert rows["2026-02"]["s1"] == 2 and rows["2026-02"]["s1_n"] == 1  # r12 excluded
    assert rows["2026-03"]["s0"] == 2


def test_chart_metrics_filters_and_intervals():
    spec = chart(
        STATE,
        {
            "kind": "bar",
            "interval": "quarter",
            "series": [
                {"company_id": "a", "source": "reviews", "metric": "count"},
                {"company_id": "a", "source": "reviews", "metric": "count", "rating_max": 2},
            ],
        },
        COMPANIES,
    )
    rows = {r["bucket"]: r for r in spec["data"]}
    assert rows["2025-Q4"]["s0"] == 2 and rows["2025-Q4"]["s1"] == 0  # counts fill with 0
    assert rows["2026-Q1"]["s0"] == 2 and rows["2026-Q1"]["s1"] == 1
    share = chart(
        STATE,
        {"series": [_series(metric="low_rating_share")], "interval": "year"},
        COMPANIES,
    )
    assert {r["bucket"]: r["s0"] for r in share["data"]} == {"2025": 0.0, "2026": 66.7}
    news = chart(
        STATE,
        {"series": [{"company_id": "a", "source": "news", "metric": "count", "contains": "sold"}]},
        COMPANIES,
    )
    assert [r["bucket"] for r in news["data"]] == ["2026-01"]  # n3 is excluded anyway


def test_chart_week_buckets_start_on_monday():
    assert bucket_of("2026-01-22", "week") == "2026-01-19"
    assert bucket_of("2026-05-08", "quarter") == "2026-Q2"


def test_chart_rejects_mixed_units_and_news_ratings():
    with pytest.raises(ChartError, match="separate charts"):
        chart(STATE, {"series": [_series(), _series(metric="count")]}, COMPANIES)
    with pytest.raises(ChartError, match="counted"):
        chart(
            STATE,
            {"series": [{"company_id": "a", "source": "news", "metric": "avg_rating"}]},
            COMPANIES,
        )


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


def test_chart_rejects_rating_filter_on_low_rating_share():
    series = [_series(metric="low_rating_share", rating_max=2)]
    with pytest.raises(ChartError, match="remove rating_min/rating_max"):
        chart(STATE, {"series": series}, COMPANIES)
