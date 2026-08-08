"""Per-run factor exposures (key-rate DV01s now, vega when options land).

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-08
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
CREATE TABLE risk_exposures (
    run_id    bigint   NOT NULL REFERENCES risk_runs ON DELETE CASCADE,
    desk_id   smallint NOT NULL REFERENCES desks,
    factor_id integer  NOT NULL REFERENCES risk_factors,
    measure   text NOT NULL CHECK (measure IN ('KRD_DV01')),
    value     numeric(18,2) NOT NULL,
    PRIMARY KEY (run_id, desk_id, factor_id, measure)
)
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS risk_exposures")
