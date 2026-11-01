"""Database tables.

Two groups:
- Domain data loaded from dummy_data.json (companies, previous names, reviews, news).
  The agent reads these only through its tools.
- Check state: one row per check, an append-only event log that drives the UI stream,
  and a command log that makes reviewer actions idempotent.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# --- Domain data -------------------------------------------------------------


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # active | closed
    founded: Mapped[date | None] = mapped_column(Date)
    closed_on: Mapped[date | None] = mapped_column(Date)
    address: Mapped[str | None] = mapped_column(String)

    __table_args__ = (
        # Fuzzy name search for lookalikes ("Brightline" vs "Brightlane").
        Index(
            "ix_companies_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class CompanyPreviousName(Base):
    __tablename__ = "company_previous_names"

    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String, primary_key=True)

    __table_args__ = (
        Index(
            "ix_company_previous_names_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_reviews_company_date", "company_id", "date"),)


class NewsItem(Base):
    __tablename__ = "news"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_news_company_date", "company_id", "date"),)


# --- Check state -------------------------------------------------------------


class Check(Base):
    __tablename__ = "checks"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    # running | waiting_for_input | paused | idle | error | ... (mirrors the workflow)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    # Last event seq handed out for this check. Incremented under a row lock, so events are
    # numbered gap-free and in commit order even with concurrent writers.
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CheckEvent(Base):
    """Append-only log of everything the reviewer sees, ordered by a per-check seq.

    The UI is a pure fold over this log, so a refreshed browser replays from
    Last-Event-ID and then follows live events — no gaps and no duplicates.
    """

    __tablename__ = "check_events"

    check_id: Mapped[str] = mapped_column(
        ForeignKey("checks.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Optional idempotency key: an event re-emitted by a retried step is stored only once.
    key: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ux_check_events_key",
            "check_id",
            "key",
            unique=True,
            postgresql_where=text("key IS NOT NULL"),
        ),
    )


class Command(Base):
    """Reviewer actions (answer, feedback, pause, resume, ...), keyed by a client-made id.

    The primary key makes a double-clicked or retried request a no-op.
    """

    __tablename__ = "commands"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    check_id: Mapped[str] = mapped_column(
        ForeignKey("checks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
