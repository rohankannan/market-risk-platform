"""Seed loader: fixtures (portfolio.yaml) + the committed market snapshot -> Postgres.

    python -m risk.jobs.seed [--snapshot data/seed/market_snapshot.parquet]
                             [--portfolio data/seed/portfolio.yaml] [--force]

Requires the schema (alembic upgrade head) and DATABASE_URL (or the local
docker-compose default). Idempotent by construction: fixtures upsert on natural
keys; market data COPYs into a temp table and upserts on (factor_id, obs_date).

Unit quantities are fixed once, at seed time, from each instrument's LAST
available close in the snapshot (the "anchor"): the book hits its target
notionals as of the seed date and is static thereafter (a documented model-doc
limitation). Equity conversion uses the UNADJUSTED close - that is the only
thing the unadjusted series is for. Par-bond coupons are struck at the anchor
yield, so bonds price at par on the anchor date.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from dataclasses import dataclass, field

import pandas as pd
import yaml
from sqlalchemy import create_engine, text

from risk_engine.pricing import dv01

DEFAULT_DB_URL = "postgresql+psycopg://riskdesk:riskdesk@localhost:5432/riskdesk"
LIMITS_EFFECTIVE_FROM = dt.date(2007, 1, 1)   # before all snapshot data: limits always in force

_FACTOR_TYPE_TO_ASSET_CLASS = {"PRICE": "EQUITY", "FX_RATE": "FX", "YIELD": "RATES",
                               "VOL_INDEX": "EQUITY"}


@dataclass
class SeedBundle:
    desks: list[dict] = field(default_factory=list)
    factors: list[dict] = field(default_factory=list)
    instruments: list[dict] = field(default_factory=list)
    instrument_factors: list[dict] = field(default_factory=list)   # keyed by ticker/factor_code
    positions: list[dict] = field(default_factory=list)            # keyed by desk_code/ticker
    limits: list[dict] = field(default_factory=list)
    anchor_date: dt.date | None = None


def latest_by_factor(snap: pd.DataFrame) -> pd.DataFrame:
    """Last observation per factor: columns obs_date, value, value_unadjusted."""
    idx = snap.groupby("factor_code")["obs_date"].idxmax()
    return snap.loc[idx].set_index("factor_code")


def build_seed_bundle(cfg: dict, snap: pd.DataFrame) -> SeedBundle:
    """Pure transform from config + snapshot to insertable rows (no DB access)."""
    b = SeedBundle()
    latest = latest_by_factor(snap)
    b.anchor_date = max(pd.Timestamp(d).date() for d in latest["obs_date"])

    for d in cfg["desks"]:
        b.desks.append({"desk_code": d["code"], "desk_name": d["name"],
                        "is_aggregate": bool(d.get("is_aggregate", False))})

    for f in cfg["factors"]:
        fb = f.get("fallback") or {}
        b.factors.append({
            "factor_code": f["code"], "factor_type": f["type"], "return_conv": f["conv"],
            "source": f["source"], "source_symbol": f["symbol"],
            "fallback_source": fb.get("source"), "fallback_symbol": fb.get("symbol"),
            "invert_on_ingest": bool(f.get("invert", False)),
            "ffill_limit_days": int(f.get("ffill_limit", 3)),
        })

    for desk_code, rows in cfg["positions"].items():
        for p in rows:
            factor = p["factor"]
            if factor not in latest.index:
                raise ValueError(f"position {p['ticker']}: factor {factor} not in snapshot")
            anchor_value = float(latest.loc[factor, "value"])
            anchor_unadj = float(latest.loc[factor, "value_unadjusted"])

            if desk_code == "EQUITY":
                target = float(p["target_notional_usd"])
                qty = round(target / anchor_unadj)                     # whole shares
                inst_type = "ETF" if p["ticker"] == "SPY" else "STOCK"
                meta, entry_price = {}, anchor_unadj
                sens = [{"factor_code": factor, "sensitivity_type": "DELTA", "sensitivity": 1.0}]
                realized = qty * anchor_unadj
            elif desk_code == "FX":
                target = float(p["target_notional_usd"])
                qty = round(target / anchor_value, 2)                  # units of foreign ccy
                inst_type, meta, entry_price = "FX_SPOT", {}, anchor_value
                sens = [{"factor_code": factor, "sensitivity_type": "DELTA", "sensitivity": 1.0}]
                realized = qty * anchor_value
            elif desk_code == "RATES":
                qty = float(p["face_usd"])                             # signed face
                maturity = float(p["maturity_years"])
                y = round(anchor_value / 100.0, 6)                     # DGS series are in percent
                coupon = y if p.get("coupon") == "par" else float(p["coupon"])
                inst_type = "GOVT_BOND"
                meta = {"coupon": coupon, "maturity_years": maturity, "anchor_yield_pct": anchor_value}
                entry_price = 100.0                                    # par at the anchor by construction
                sens = [{"factor_code": factor, "sensitivity_type": "DV01",
                         "sensitivity": dv01(coupon, maturity, y, face=1.0)}]  # $ per bp per $1 face
                realized = qty
            else:
                raise ValueError(f"unknown desk {desk_code!r} in positions")

            b.instruments.append({
                "ticker": p["ticker"], "name": p["ticker"].replace("_", " "),
                "asset_class": desk_code if desk_code != "EQUITY" else "EQUITY",
                "instrument_type": inst_type, "currency": "USD",
                "multiplier": 1, "meta": meta,
            })
            for s in sens:
                b.instrument_factors.append({"ticker": p["ticker"], **s})
            b.positions.append({
                "desk_code": desk_code, "ticker": p["ticker"], "quantity": qty,
                "entry_date": b.anchor_date, "entry_price": entry_price,
                "target_notional_usd": float(p.get("target_notional_usd", p.get("face_usd"))),
                "realized_notional_usd": realized,
            })

    for lim in cfg["limits"]:
        b.limits.append({"desk_code": lim["desk"], "measure": lim["measure"],
                         "limit_value": float(lim["limit_usd"]),
                         "effective_from": LIMITS_EFFECTIVE_FROM})
    return b


# ---------------------------------------------------------------- DB load

def load_bundle(engine, bundle: SeedBundle, snap: pd.DataFrame, force: bool = False) -> dict:
    """Upsert fixtures and bulk-load market data in one transaction. Returns row counts."""
    with engine.begin() as conn:
        n_positions = conn.scalar(text("SELECT count(*) FROM positions"))
        if n_positions and not force:
            raise SystemExit(f"positions table already has {n_positions} rows - rerun with --force "
                             "to reseed fixtures (market data upserts either way)")
        if n_positions and force:
            # FK-safe order; market_data and run history are preserved
            for t in ("limits", "positions", "instrument_factors"):
                conn.execute(text(f"DELETE FROM {t}"))

        for d in bundle.desks:
            conn.execute(text("""
                INSERT INTO desks (desk_code, desk_name, is_aggregate)
                VALUES (:desk_code, :desk_name, :is_aggregate)
                ON CONFLICT (desk_code) DO UPDATE
                SET desk_name = EXCLUDED.desk_name, is_aggregate = EXCLUDED.is_aggregate"""), d)

        for f in bundle.factors:
            conn.execute(text("""
                INSERT INTO risk_factors (factor_code, factor_type, return_conv, source,
                    source_symbol, fallback_source, fallback_symbol, invert_on_ingest,
                    ffill_limit_days)
                VALUES (:factor_code, :factor_type, :return_conv, :source, :source_symbol,
                        :fallback_source, :fallback_symbol, :invert_on_ingest, :ffill_limit_days)
                ON CONFLICT (factor_code) DO UPDATE
                SET source = EXCLUDED.source, source_symbol = EXCLUDED.source_symbol,
                    fallback_source = EXCLUDED.fallback_source,
                    fallback_symbol = EXCLUDED.fallback_symbol,
                    invert_on_ingest = EXCLUDED.invert_on_ingest,
                    ffill_limit_days = EXCLUDED.ffill_limit_days"""), f)

        for i in bundle.instruments:
            conn.execute(text("""
                INSERT INTO instruments (ticker, name, asset_class, instrument_type, currency,
                    multiplier, meta)
                VALUES (:ticker, :name, :asset_class, :instrument_type, :currency,
                        :multiplier, CAST(:meta AS jsonb))
                ON CONFLICT (ticker) DO UPDATE
                SET instrument_type = EXCLUDED.instrument_type, meta = EXCLUDED.meta"""),
                {**i, "meta": json.dumps(i["meta"])})

        desk_id = dict(conn.execute(text("SELECT desk_code, desk_id FROM desks")).all())
        factor_id = dict(conn.execute(text("SELECT factor_code, factor_id FROM risk_factors")).all())
        inst_id = dict(conn.execute(text("SELECT ticker, instrument_id FROM instruments")).all())

        for s in bundle.instrument_factors:
            conn.execute(text("""
                INSERT INTO instrument_factors (instrument_id, factor_id, sensitivity_type, sensitivity)
                VALUES (:iid, :fid, :stype, :sens)
                ON CONFLICT (instrument_id, factor_id) DO UPDATE
                SET sensitivity_type = EXCLUDED.sensitivity_type,
                    sensitivity = EXCLUDED.sensitivity"""),
                {"iid": inst_id[s["ticker"]], "fid": factor_id[s["factor_code"]],
                 "stype": s["sensitivity_type"], "sens": s["sensitivity"]})

        for pos in bundle.positions:
            conn.execute(text("""
                INSERT INTO positions (desk_id, instrument_id, quantity, entry_date, entry_price)
                VALUES (:did, :iid, :qty, :entry_date, :entry_price)
                ON CONFLICT (desk_id, instrument_id) DO UPDATE
                SET quantity = EXCLUDED.quantity, entry_date = EXCLUDED.entry_date,
                    entry_price = EXCLUDED.entry_price"""),
                {"did": desk_id[pos["desk_code"]], "iid": inst_id[pos["ticker"]],
                 "qty": pos["quantity"], "entry_date": pos["entry_date"],
                 "entry_price": pos["entry_price"]})

        for lim in bundle.limits:
            conn.execute(text("""
                INSERT INTO limits (desk_id, measure, limit_value, effective_from)
                VALUES (:did, :measure, :limit_value, :effective_from)
                ON CONFLICT (desk_id, measure, effective_from) DO UPDATE
                SET limit_value = EXCLUDED.limit_value"""),
                {"did": desk_id[lim["desk_code"]], "measure": lim["measure"],
                 "limit_value": lim["limit_value"], "effective_from": lim["effective_from"]})

        # market data: COPY into a temp table, then set-based upsert (idempotent)
        conn.exec_driver_sql(
            "CREATE TEMP TABLE tmp_md (factor_code text, obs_date date, value numeric, "
            "source text) ON COMMIT DROP")
        dbapi = conn.connection.driver_connection
        with dbapi.cursor() as cur, cur.copy(
                "COPY tmp_md (factor_code, obs_date, value, source) FROM STDIN") as cp:
            for row in snap.itertuples(index=False):
                cp.write_row((row.factor_code, pd.Timestamp(row.obs_date).date(),
                              float(row.value), row.source))
        conn.exec_driver_sql("""
            INSERT INTO market_data (factor_id, obs_date, value, source)
            SELECT rf.factor_id, t.obs_date, t.value, t.source
            FROM tmp_md t JOIN risk_factors rf USING (factor_code)
            ON CONFLICT (factor_id, obs_date) DO UPDATE
            SET value = EXCLUDED.value, source = EXCLUDED.source,
                is_ffilled = false, ffill_age = 0""")

        counts = {}
        for t in ("desks", "risk_factors", "instruments", "instrument_factors",
                  "positions", "limits", "market_data"):
            counts[t] = conn.scalar(text(f"SELECT count(*) FROM {t}"))
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="risk.jobs.seed")
    parser.add_argument("--snapshot", default="data/seed/market_snapshot.parquet")
    parser.add_argument("--portfolio", default="data/seed/portfolio.yaml")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", DEFAULT_DB_URL))
    parser.add_argument("--force", action="store_true", help="reseed fixtures even if present")
    args = parser.parse_args(argv)

    with open(args.portfolio) as f:
        cfg = yaml.safe_load(f)
    snap = pd.read_parquet(args.snapshot)
    bundle = build_seed_bundle(cfg, snap)

    print(f"[seed] anchor date {bundle.anchor_date}; book:")
    book = pd.DataFrame(bundle.positions)[
        ["desk_code", "ticker", "quantity", "entry_price", "realized_notional_usd",
         "target_notional_usd"]]
    with pd.option_context("display.width", 160, "display.float_format", "{:,.2f}".format):
        print(book.to_string(index=False))

    engine = create_engine(args.database_url)
    counts = load_bundle(engine, bundle, snap, force=args.force)
    print("[seed] row counts:", ", ".join(f"{k}={v:,}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
