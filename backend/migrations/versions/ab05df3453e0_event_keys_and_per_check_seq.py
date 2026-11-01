"""event keys and per-check seq

Revision ID: ab05df3453e0
Revises: 04dc3b0396f9
Create Date: 2026-11-01 10:38:50.166609

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ab05df3453e0'
down_revision: Union[str, Sequence[str], None] = '04dc3b0396f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("check_events", sa.Column("key", sa.String(), nullable=True))
    op.create_index(
        "ux_check_events_key",
        "check_events",
        ["check_id", "key"],
        unique=True,
        postgresql_where=sa.text("key IS NOT NULL"),
    )
    op.add_column(
        "checks", sa.Column("last_seq", sa.BigInteger(), server_default="0", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("checks", "last_seq")
    op.drop_index("ux_check_events_key", table_name="check_events")
    op.drop_column("check_events", "key")
