"""Initial schema: 13 tables per RISKDESK_SPEC section 5.

Revision ID: 0001
Revises:
Create Date: 2026-08-03
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_SQL = """
CREATE TABLE desks (
    desk_id      smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    desk_code    text NOT NULL UNIQUE,                -- 'RATES','FX','EQUITY','FIRM'
    desk_name    text NOT NULL,
    base_ccy     char(3) NOT NULL DEFAULT 'USD',
    is_aggregate boolean NOT NULL DEFAULT false      -- true only for FIRM (no NULL keys)
);

CREATE TABLE risk_factors (
    factor_id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    factor_code      text NOT NULL UNIQUE,           -- 'EQ.SPY','IR.UST.10Y','FX.EURUSD','VOL.SPX.IV30'
    factor_type      text NOT NULL CHECK (factor_type IN ('PRICE','YIELD','FX_RATE','VOL_INDEX')),
    return_conv      text NOT NULL CHECK (return_conv IN ('LOG','ABS_BP','ABS')),
    source           text NOT NULL CHECK (source IN ('YFINANCE','FRED','STOOQ','DERIBIT')),
    source_symbol    text NOT NULL,                  -- 'SPY','DGS10','DEXUSEU'
    fallback_source  text,
    fallback_symbol  text,
    invert_on_ingest boolean NOT NULL DEFAULT false, -- DEXJPUS/DEXMXUS -> USD per 1 unit foreign
    ffill_limit_days smallint NOT NULL DEFAULT 3,    -- 7 for FRED FX (H.10 weekly publication lag)
    liquidity_horizon smallint NOT NULL DEFAULT 10,  -- FRTB MAR33.12 mapping (documented, not used in MVP)
    is_active        boolean NOT NULL DEFAULT true
);

CREATE TABLE instruments (
    instrument_id   integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker          text NOT NULL UNIQUE,            -- 'SPY','UST_10Y','EURUSD_SPOT'
    name            text NOT NULL,
    asset_class     text NOT NULL CHECK (asset_class IN ('EQUITY','RATES','FX','CRYPTO')),
    instrument_type text NOT NULL CHECK (instrument_type IN ('STOCK','ETF','GOVT_BOND','FX_SPOT','OPTION')),
    currency        char(3) NOT NULL DEFAULT 'USD',
    multiplier      numeric(18,6) NOT NULL DEFAULT 1,
    meta            jsonb NOT NULL DEFAULT '{}'      -- GOVT_BOND rows carry coupon + maturity_years
);

-- normalized instrument->factor sensitivities (DELTA for linear, DV01 for bonds, VEGA later)
CREATE TABLE instrument_factors (
    instrument_id    integer NOT NULL REFERENCES instruments,
    factor_id        integer NOT NULL REFERENCES risk_factors,
    sensitivity_type text NOT NULL CHECK (sensitivity_type IN ('DELTA','DV01','VEGA')),
    sensitivity      numeric(18,6) NOT NULL,
    PRIMARY KEY (instrument_id, factor_id)
);

CREATE TABLE positions (
    position_id   integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    desk_id       smallint NOT NULL REFERENCES desks,
    instrument_id integer  NOT NULL REFERENCES instruments,
    quantity      numeric(18,4) NOT NULL,            -- signed; shorts negative
    entry_date    date NOT NULL,
    entry_price   numeric(18,6),
    UNIQUE (desk_id, instrument_id)                  -- static mock book
);

CREATE TABLE market_data (
    factor_id   integer NOT NULL REFERENCES risk_factors,
    obs_date    date    NOT NULL,
    value       numeric(18,8) NOT NULL,
    source      text    NOT NULL,                    -- actual source used for this row
    is_ffilled  boolean NOT NULL DEFAULT false,
    ffill_age   smallint NOT NULL DEFAULT 0,
    inserted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (factor_id, obs_date)
);
CREATE INDEX md_by_date ON market_data (obs_date);   -- cross-sectional reads

CREATE TABLE risk_runs (                             -- doubles as the batch-status table
    run_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_date     date NOT NULL,
    run_type     text NOT NULL CHECK (run_type IN ('EOD','BACKFILL','ADHOC')),
    status       text NOT NULL DEFAULT 'PENDING'
                 CHECK (status IN ('PENDING','RUNNING','SUCCESS','PARTIAL','FAILED')),
    started_at   timestamptz,
    finished_at  timestamptz,
    code_version text,                               -- git SHA baked into the image
    config_hash  text,                               -- reproducibility story
    error_msg    text,
    UNIQUE (run_date, run_type)                      -- idempotency anchor
);

CREATE TABLE risk_results (
    run_id       bigint   NOT NULL REFERENCES risk_runs ON DELETE CASCADE,
    desk_id      smallint NOT NULL REFERENCES desks,   -- FIRM row for firm-wide
    measure      text NOT NULL CHECK (measure IN ('VAR_HS','VAR_FHS','VAR_PARAM','ES_975','ES_STRESSED')),
    confidence   numeric(5,4) NOT NULL,               -- 0.9900 / 0.9750
    horizon_days smallint NOT NULL DEFAULT 1,
    value        numeric(18,2) NOT NULL,              -- positive = potential loss, USD
    PRIMARY KEY (run_id, desk_id, measure, confidence, horizon_days)
);

CREATE TABLE pnl (                                    -- feeds backtesting + PLA
    desk_id  smallint NOT NULL REFERENCES desks,
    pnl_date date NOT NULL,
    pnl_type text NOT NULL CHECK (pnl_type IN ('HYPOTHETICAL','RISK_THEORETICAL')),
    amount   numeric(18,2) NOT NULL,
    PRIMARY KEY (desk_id, pnl_date, pnl_type)
);

CREATE TABLE backtest_exceptions (
    desk_id   smallint NOT NULL REFERENCES desks,
    obs_date  date NOT NULL,
    measure   text NOT NULL,
    var_value numeric(18,2) NOT NULL,
    pnl_value numeric(18,2) NOT NULL,
    run_id    bigint REFERENCES risk_runs,
    PRIMARY KEY (desk_id, obs_date, measure)
);

CREATE TABLE scenarios (
    scenario_id   smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scenario_code text NOT NULL UNIQUE,               -- 'GFC_2008','COVID_2020','RATES_UP_100'
    scenario_name text NOT NULL,
    scenario_type text NOT NULL CHECK (scenario_type IN ('HISTORICAL_REPLAY','REGULATORY','HYPOTHETICAL')),
    window_start  date,
    window_end    date,                               -- replays only
    description   text
);

CREATE TABLE scenario_shocks (
    scenario_id smallint NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    factor_id   integer  NOT NULL REFERENCES risk_factors,
    shock_type  text NOT NULL CHECK (shock_type IN ('RELATIVE','ABSOLUTE_BP','ABSOLUTE')),
    shock_value numeric(12,6) NOT NULL,
    PRIMARY KEY (scenario_id, factor_id)
);

CREATE TABLE scenario_results (
    run_id      bigint   NOT NULL REFERENCES risk_runs ON DELETE CASCADE,
    scenario_id smallint NOT NULL REFERENCES scenarios,
    desk_id     smallint NOT NULL REFERENCES desks,
    pnl_impact  numeric(18,2) NOT NULL,
    PRIMARY KEY (run_id, scenario_id, desk_id)
);

CREATE TABLE limits (
    limit_id       smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    desk_id        smallint NOT NULL REFERENCES desks,
    measure        text NOT NULL,
    limit_value    numeric(18,2) NOT NULL,
    warn_threshold numeric(5,4) NOT NULL DEFAULT 0.8000,
    effective_from date NOT NULL,
    effective_to   date,                              -- NULL = open-ended
    UNIQUE (desk_id, measure, effective_from)
);

CREATE TABLE dq_issues (
    issue_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id     bigint REFERENCES risk_runs,
    factor_id  integer REFERENCES risk_factors,
    obs_date   date,
    check_name text NOT NULL,                         -- 'GAP','FFILL_LIMIT','OUTLIER_RETURN','STALE','UNIT_BOUND','SOURCE_DIVERGENCE'
    severity   text NOT NULL CHECK (severity IN ('INFO','WARN','BLOCK')),
    detail     jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dq_open ON dq_issues (severity, created_at) WHERE severity IN ('WARN','BLOCK');
"""

DROP_SQL = """
DROP TABLE IF EXISTS dq_issues, limits, scenario_results, scenario_shocks, scenarios,
    backtest_exceptions, pnl, risk_results, risk_runs, market_data, positions,
    instrument_factors, instruments, risk_factors, desks CASCADE;
"""


def _execute_statements(sql: str) -> None:
    # psycopg3 rejects multiple commands in one parameterized execute, so run each
    # top-level statement separately. Line comments are stripped first: they may
    # contain ';' or ':'-tokens that would confuse the split / bind-param parsing.
    # (Safe here: no string literal in this DDL contains '--'.)
    stripped = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    for stmt in stripped.split(";"):
        if stmt.strip():
            op.execute(stmt)


def upgrade() -> None:
    _execute_statements(SCHEMA_SQL)


def downgrade() -> None:
    _execute_statements(DROP_SQL)
