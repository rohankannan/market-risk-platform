"""RiskDesk API - FastAPI service over the results tables.

Day-one skeleton: /healthz is real; /api/v1/meta returns a static placeholder
until the batch writes its first risk_runs row (milestone 3 wires the DB).
Full 11-endpoint contract: RISKDESK_SPEC.md section 6.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(
    title="RiskDesk API",
    version="0.1.0",
    description="EOD market-risk platform for a mock three-desk book. VaR/ES reported as positive potential loss, USD.",
)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/v1/meta")
def meta() -> dict:
    # Placeholder until milestone 3 wires risk_runs; shape matches the final contract.
    return {
        "latest_as_of": None,
        "batch_status": "not_yet_run",
        "batch_completed_at": None,
        "available_dates": [],
        "desks": [
            {"desk_id": "equity", "name": "Cash Equities"},
            {"desk_id": "fx", "name": "FX Spot"},
            {"desk_id": "rates", "name": "US Rates"},
        ],
    }
