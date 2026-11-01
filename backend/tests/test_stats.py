from company_check.agent.graph import _pick
from company_check.agent.stats import monthly_ratings, rating_shift


def _r(d: str, rating: int) -> dict:
    return {"id": d, "date": d, "rating": rating, "text": ""}


def test_monthly_ratings_groups_sorts_and_filters():
    reviews = [_r("2026-02-10", 2), _r("2026-01-05", 5), _r("2026-01-20", 4), _r("2025-12-01", 3)]
    assert monthly_ratings(reviews) == [
        {"month": "2025-12", "avg": 3, "count": 1},
        {"month": "2026-01", "avg": 4.5, "count": 2},
        {"month": "2026-02", "avg": 2, "count": 1},
    ]
    assert [m["month"] for m in monthly_ratings(reviews, start="2026-01-01")] == [
        "2026-01",
        "2026-02",
    ]
    assert [m["month"] for m in monthly_ratings(reviews, end="2026-01")] == ["2025-12", "2026-01"]


def test_rating_shift_finds_the_drop():
    reviews = [_r(f"2025-{m:02d}-01", 5) for m in range(1, 9)] + [
        _r(f"2026-{m:02d}-01", 2) for m in range(1, 9)
    ]
    shift = rating_shift(reviews)
    assert shift["from_month"] == "2026-01"
    assert shift["avg_before"] == 5 and shift["avg_after"] == 2


def test_rating_shift_ignores_noise():
    reviews = [_r(f"2025-{m:02d}-01", 4 if m % 2 else 3) for m in range(1, 13)]
    assert rating_shift(reviews) is None


def _c(cid: str, name: str, score: float) -> dict:
    return {"id": cid, "name": name, "score": score}


def test_pick_exact_name_wins():
    cands = [_c("a", "Northwind Dental Supplies", 1.0), _c("b", "Northwind Dental Clinic", 0.5)]
    assert _pick("northwind dental supplies", cands)["id"] == "a"


def test_pick_ambiguous_returns_none():
    cands = [
        _c("a", "Brightline Logistics Ltd", 0.875),
        _c("b", "Brightline Logistics Inc", 0.84),
        _c("c", "Brightlane Logistics", 0.75),
    ]
    assert _pick("Brightline Logistics", cands) is None


def test_pick_single_or_clear_winner():
    assert _pick("Harborview Catering", [_c("a", "Harborview Catering Co", 0.9)])["id"] == "a"
    assert _pick("x", [_c("a", "A", 0.9), _c("b", "B", 0.4)])["id"] == "a"
