"""Data tools against the seeded database (run `python -m company_check.seed` first)."""

import pytest

from company_check.agent import data
from company_check.db import SessionLocal, engine


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s
    await engine.dispose()


async def test_search_finds_all_lookalikes(session):
    names = [c["name"] for c in await data.search_companies(session, "Brightline Logistics")]
    assert names == ["Brightline Logistics Ltd", "Brightline Logistics Inc", "Brightlane Logistics"]


async def test_search_matches_previous_name(session):
    [hit] = await data.search_companies(session, "Leafline Apps")
    assert hit["id"] == "cmp_007" and hit["matched_on"] == "previous_name"


async def test_reviews_are_sorted_by_date(session):
    reviews = await data.get_reviews(session, "cmp_001")
    dates = [r["date"] for r in reviews]
    assert len(reviews) == 24 and dates == sorted(dates)


async def test_company_includes_previous_names(session):
    c = await data.get_company(session, "cmp_007")
    assert c["previous_names"] == ["Leafline Apps"]
    assert await data.get_company(session, "nope") is None


async def test_search_news_by_previous_name(session):
    ids = [n["id"] for n in await data.search_news(session, "Leafline")]
    assert "ev_news_007_02" in ids


@pytest.mark.parametrize("query", ["%", "_", "   ", "%%_%"])
async def test_search_treats_wildcards_literally(session, query):
    assert await data.search_companies(session, query) == []
    assert await data.search_news(session, query) == []
