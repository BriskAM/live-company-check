"""Load companies, reviews and news from dummy_data.json into Postgres.

Only the `companies` part is loaded. `sample_checks` describe expected results and must
never reach the database, the agent's tools or its prompts.

Idempotent: re-running upserts the same rows.

    uv run python -m company_check.seed [path/to/dummy_data.json]
"""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from company_check.db import SessionLocal, engine
from company_check.models import Company, CompanyPreviousName, NewsItem, Review

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "dummy_data.json"


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _upsert(model, rows: list[dict], key: list[str]):
    stmt = insert(model).values(rows)
    update_cols = {c: stmt.excluded[c] for c in rows[0] if c not in key}
    if update_cols:
        return stmt.on_conflict_do_update(index_elements=key, set_=update_cols)
    return stmt.on_conflict_do_nothing(index_elements=key)


async def seed(path: Path) -> dict[str, int]:
    companies = json.loads(path.read_text())["companies"]

    company_rows, prev_rows, review_rows, news_rows = [], [], [], []
    for c in companies:
        company_rows.append(
            {
                "id": c["id"],
                "name": c["name"],
                "status": c["status"],
                "founded": _d(c.get("founded")),
                "closed_on": _d(c.get("closed_on")),
                "address": c.get("address"),
            }
        )
        prev_rows += [{"company_id": c["id"], "name": n} for n in c.get("previous_names", [])]
        review_rows += [
            {
                "id": r["id"],
                "company_id": c["id"],
                "date": _d(r["date"]),
                "rating": r["rating"],
                "text": r["text"],
            }
            for r in c.get("reviews", [])
        ]
        news_rows += [
            {
                "id": n["id"],
                "company_id": c["id"],
                "date": _d(n["date"]),
                "headline": n["headline"],
                "text": n["text"],
            }
            for n in c.get("news", [])
        ]

    async with SessionLocal() as session, session.begin():
        await session.execute(_upsert(Company, company_rows, ["id"]))
        if prev_rows:
            await session.execute(_upsert(CompanyPreviousName, prev_rows, ["company_id", "name"]))
        if review_rows:
            await session.execute(_upsert(Review, review_rows, ["id"]))
        if news_rows:
            await session.execute(_upsert(NewsItem, news_rows, ["id"]))

    return {
        "companies": len(company_rows),
        "previous_names": len(prev_rows),
        "reviews": len(review_rows),
        "news": len(news_rows),
    }


async def _main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    counts = await seed(path)
    await engine.dispose()
    print("Seeded:", ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    asyncio.run(_main())
