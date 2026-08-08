"""Per-position risk decomposition table plus DELTA_USD exposure rows.

position_components carries the desk drill-down: standalone VaR, per-desk
Euler component ES, and exact marginal VaR per position. DELTA_USD joins the
per-run exposure measures (dollar delta per position class; bonds keep
KRD_DV01 as their exposure view).

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-08
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
CREATE TABLE position_components (
    run_id          bigint   NOT NULL REFERENCES risk_runs ON DELETE CASCADE,
    desk_id         smallint NOT NULL REFERENCES desks,
    ticker          text     NOT NULL,
    factor_class    text     NOT NULL CHECK (factor_class IN ('EQ','FX','IR','VOL')),
    quantity        numeric(18,4) NOT NULL,
    instrument_type text     NOT NULL,
    standalone_var  numeric(18,2) NOT NULL,
    component_es    numeric(18,2) NOT NULL,
    marginal_var    numeric(18,2) NOT NULL,
    PRIMARY KEY (run_id, desk_id, ticker)
)
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)
    op.execute("ALTER TABLE risk_exposures DROP CONSTRAINT risk_exposures_measure_check")
    op.execute("ALTER TABLE risk_exposures ADD CONSTRAINT risk_exposures_measure_check "
               "CHECK (measure IN ('KRD_DV01','VEGA','DELTA_USD'))")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS position_components")
    op.execute("DELETE FROM risk_exposures WHERE measure = 'DELTA_USD'")
    op.execute("ALTER TABLE risk_exposures DROP CONSTRAINT risk_exposures_measure_check")
    op.execute("ALTER TABLE risk_exposures ADD CONSTRAINT risk_exposures_measure_check "
               "CHECK (measure IN ('KRD_DV01','VEGA'))")
