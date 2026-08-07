# RiskDesk — Model Documentation (SR 11-7 structure)

*Skeleton (day 1). Each section is written as its component ships; every method
section ends with "questions an interviewer would ask" — this document doubles
as interview prep. Target: 6–10 pages by Oct 15.*

## 1. Model purpose and scope
- What the model is for: daily desk-level and firm-level market-risk measurement (VaR 99%, ES 97.5%), limit monitoring, stress testing, and backtesting on a mock three-desk book.
- What it is NOT for: pricing, intraday risk, regulatory capital calculation.

## 2. Model inputs and data
- Sources, conventions (log vs bp returns), alignment policy (NYSE calendar, bounded forward-fill, FRED H.10 lag), corporate-action handling (adjusted vs unadjusted close), asynchronous-close caveat.
- Data-quality checks and their severities; what blocks a run.
- *Interviewer questions:* Why absolute bp shocks for rates? What breaks if you use log returns on 2020 yields? Why flag rather than delete outlier returns?

## 3. Methodology
### 3.1 Historical simulation VaR
- 500-day equal-weight window; quantile estimator and its order statistics at n=500.
- *Interviewer questions:* Why 500 and not 250? What happens to HS VaR when vol regime shifts?
### 3.2 Filtered historical simulation (EWMA)
- Devol/revol mechanics; λ=0.94 convention; half-life ≈ 11 days; why cross-correlations survive; failure modes (stationarity of standardized residuals, revol overshoot).
### 3.3 Expected Shortfall 97.5% and stressed calibration
- ES estimator; ES97.5 ≈ VaR99 under normality (Basel calibration); fixed stressed window (MVP) → programmatic worst-window search (winter).
### 3.4 Stress testing
- Data-driven replays (GFC 2008, COVID 2020) and hypothetical shocks; yield floor.
### 3.5 Revaluation
- Exact P&L for linear positions; closed-form par-bond pricing; DV01/convexity by central-difference bump-and-reprice; constant-maturity proxy-portfolio mapping.

## 4. Backtesting and ongoing monitoring
- Kupiec POF, Christoffersen independence/conditional coverage, Basel traffic light and multiplier add-ons; why the regulatory response is not hypothesis testing.
- (Winter) Acerbi–Székely Z₂ ES backtest with simulated critical values; PLA (Spearman + KS, MAR32 zones) once the book has options.

## 5. Assumptions and limitations (mandatory section)
- √10 horizon scaling assumes iid daily P&L.
- One factor per instrument; nearest-key-rate mapping; no tenor interpolation (MVP).
- Non-synchronous closes across asset classes bias cross-asset correlation (named, not fixed — real desks fight the same issue).
- Linear book in MVP ⇒ PLA deliberately deferred (vacuous on a linear book).
- FRTB scope: ES piece only — no liquidity-horizon cascade, no NMRF/RFET, no IMA capital. Liquidity horizons per MAR33.12 are recorded on `risk_factors` to document the conscious scope cut.
- Yields floored at 1bp under stress; equal-weight HS window dilutes new information (the FHS comparison quantifies this).

## 6. Model governance
- Config-as-code (`RiskConfig`), seeded reproducibility, `config_hash` + git SHA on every run, CI known-answer tests as ongoing monitoring.
