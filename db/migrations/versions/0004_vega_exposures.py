"""Admit VEGA to the per-run exposure measures (options sleeve).

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-08
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE risk_exposures DROP CONSTRAINT risk_exposures_measure_check")
    op.execute("ALTER TABLE risk_exposures ADD CONSTRAINT risk_exposures_measure_check "
               "CHECK (measure IN ('KRD_DV01','VEGA'))")


def downgrade() -> None:
    op.execute("DELETE FROM risk_exposures WHERE measure = 'VEGA'")
    op.execute("ALTER TABLE risk_exposures DROP CONSTRAINT risk_exposures_measure_check")
    op.execute("ALTER TABLE risk_exposures ADD CONSTRAINT risk_exposures_measure_check "
               "CHECK (measure IN ('KRD_DV01'))")
