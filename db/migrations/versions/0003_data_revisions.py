"""Vendor-revision log - history that changed after we first stored it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-08
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
CREATE TABLE data_revisions (
    revision_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id        bigint REFERENCES risk_runs ON DELETE SET NULL,
    factor_id     integer NOT NULL REFERENCES risk_factors,
    obs_date      date NOT NULL,
    old_value     numeric(18,8) NOT NULL,
    new_value     numeric(18,8) NOT NULL,
    revision_type text NOT NULL CHECK (revision_type IN ('VENDOR_REVISION','FFILL_REPLACED')),
    source        text NOT NULL,
    detected_at   timestamptz NOT NULL DEFAULT now()
)
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)
    op.execute("CREATE INDEX dr_by_factor_date ON data_revisions (factor_id, obs_date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_revisions")
