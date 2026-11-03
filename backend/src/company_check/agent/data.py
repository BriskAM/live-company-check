"""Data tools: the only way the agent sees company data.

Plain async functions over Postgres that return JSON-friendly dicts. Graph nodes call them
directly (deterministic steps) and wrap some as LLM tools.
"""

from datetime import date
from typing import Any

from sqlalchemy import func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from company_check.models import Company, CompanyPreviousName, NewsItem, Review

# Trigram similarity below this is treated as "not the same name".
MATCH_THRESHOLD = 0.3


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _contains(text: str) -> str:
    """ILIKE pattern for a literal substring: user input must not act as a wildcard."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search_companies(
    session: AsyncSession, query: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Fuzzy search on current and previous names. Best match first."""
    q = query.strip()
    if not q:
        return []
    prev_sim = (
        select(func.max(func.similarity(CompanyPreviousName.name, q)))
        .where(CompanyPreviousName.company_id == Company.id)
        .scalar_subquery()
    )
    name_sim = func.similarity(Company.name, q)
    score = func.greatest(name_sim, func.coalesce(prev_sim, literal(0.0)))
    stmt = (
        select(Company, name_sim.label("name_sim"), prev_sim.label("prev_sim"))
        .where(
            or_(
                score >= MATCH_THRESHOLD,
                Company.name.ilike(_contains(q), escape="\\"),
            )
        )
        .order_by(score.desc(), Company.name)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "status": c.status,
            "founded": _iso(c.founded),
            "closed_on": _iso(c.closed_on),
            "address": c.address,
            "score": round(max(name_sim, prev_sim or 0.0), 3),
            "matched_on": "previous_name" if (prev_sim or 0.0) > name_sim else "name",
        }
        for c, name_sim, prev_sim in rows
    ]


async def get_company(session: AsyncSession, company_id: str) -> dict[str, Any] | None:
    c = await session.get(Company, company_id)
    if c is None:
        return None
    prev = (
        await session.scalars(
            select(CompanyPreviousName.name).where(CompanyPreviousName.company_id == company_id)
        )
    ).all()
    return {
        "id": c.id,
        "name": c.name,
        "status": c.status,
        "founded": _iso(c.founded),
        "closed_on": _iso(c.closed_on),
        "address": c.address,
        "previous_names": list(prev),
    }


async def get_reviews(session: AsyncSession, company_id: str) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(Review).where(Review.company_id == company_id).order_by(Review.date, Review.id)
    )
    return [
        {"id": r.id, "date": r.date.isoformat(), "rating": r.rating, "text": r.text} for r in rows
    ]


async def get_news(session: AsyncSession, company_id: str) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(NewsItem)
        .where(NewsItem.company_id == company_id)
        .order_by(NewsItem.date, NewsItem.id)
    )
    return [
        {"id": n.id, "date": n.date.isoformat(), "headline": n.headline, "text": n.text}
        for n in rows
    ]


async def search_news(session: AsyncSession, text: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search all news (any company) whose headline or body mentions `text`.

    Used to look for coverage under previous names or of lookalike companies.
    """
    if not text.strip():
        return []
    pattern = _contains(text.strip())
    rows = await session.scalars(
        select(NewsItem)
        .where(
            or_(
                NewsItem.headline.ilike(pattern, escape="\\"),
                NewsItem.text.ilike(pattern, escape="\\"),
            )
        )
        .order_by(NewsItem.date, NewsItem.id)
        .limit(limit)
    )
    return [
        {
            "id": n.id,
            "company_id": n.company_id,
            "date": n.date.isoformat(),
            "headline": n.headline,
            "text": n.text,
        }
        for n in rows
    ]
