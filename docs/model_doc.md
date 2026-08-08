# RiskDesk — Model Documentation

*Structured after SR 11-7 (model development, use, validation, and limitations).
Every methodology section ends with "questions an interviewer would ask" — this
document doubles as the walkthrough script for anyone probing the model. The
figures quoted below are reproducible from the committed market snapshot:
`docker compose up` rebuilds the database and reprints the headline risk
numbers (CI asserts the firm VaR to the cent on every push); the 750-day
backtest table regenerates with `python -m risk.jobs.backfill`.*

## 1. Model purpose and scope

RiskDesk measures the daily market risk of a mock three-desk trading book —
cash equities, FX spot, and US Treasuries — the way a bank's risk function runs
its nightly cycle. Per desk and firm-wide, the nightly batch produces:

- **1-day 99% VaR** under two methods: equal-weight historical simulation (HS)
  and EWMA-filtered historical simulation (FHS), plus 10-day figures by
  sqrt-of-time scaling;
- **97.5% Expected Shortfall**, and a **stressed ES** calibrated on a fixed
  2008–09 window (FRTB-style; see the scope note below);
- **scenario P&L** for two historical replays (GFC 2008, COVID 2020) and three
  hypothetical shocks;
- **backtest inputs** against clean P&L — the daily hypothetical P&L and
  exceptions versus the prior run's VaR — from which the API computes Kupiec,
  Christoffersen, and the Basel traffic light with its multiplier add-ons on
  demand.

The book is sized so the standalone-VaR mix across desks is roughly
rates 45 / equity 30 / fx 25 — the risk-class mix large dealers disclose in
their FY2025 trading-VaR tables (measured on the snapshot: 44/30/26). On the
2026-08-06 snapshot the firm 1-day VaR99 is ~$1.17M, 58% of its $2M limit,
with a ~40% diversification benefit and net rates DV01 of ~$83k/bp.

**Out of scope, deliberately:** trade pricing, intraday risk, counterparty and
credit risk, and regulatory capital. The stressed-ES piece follows FRTB's
*idea* (ES 97.5, stressed-period calibration); it is not an FRTB
implementation — no liquidity-horizon cascade, no NMRF, no P&L attribution
test yet (§5).

## 2. Model inputs and data

### 2.1 Risk factors and sources

Seventeen factors drive the book: seven equity adjusted closes
(yfinance, Stooq fallback), four USD FX rates (FRED H.10, normalized at ingest
to USD per unit of foreign currency; the weekly publication lag is absorbed by
a 7-day forward-fill cap), five constant-
maturity Treasury par yields in percent (FRED: 3M, 2Y, 5Y, 10Y, 30Y), and
VIX (parametric-VaR input reserved for the options sleeve). History runs from
2007 — deep enough to contain both stress windows used in §3.4.

A frozen snapshot of all 17 series (~84k observations) is committed to the
repository. Tests, CI, and the demo never make a network call: every number in
this document is reproducible bit-for-bit from the snapshot. Live fetches
happen only in the nightly batch, with per-factor source fallbacks.

### 2.2 Return conventions

The return convention is a property of the factor, stored on the
`risk_factors` table — never an `if` statement in engine code:

| Convention | Applied to | Definition | Why |
|---|---|---|---|
| `LOG` | equities, FX | log price relative | multiplicative, additive over time |
| `ABS_BP` | yields | day-over-day change in basis points | log returns on yields explode as yields approach zero (2020: 3M bills under 10bp) |
| `ABS` | vol indices | plain difference in points | vol points are already the natural unit |

Scenario P&L is exact under each convention: `qty * S0 * (exp(r) - 1)` for LOG
factors, full bond revaluation for bp shocks (§3.5). Linearization exists only
in the delta-gamma path reserved for risk-theoretical P&L.

### 2.3 Alignment and forward-fill policy

Factors publish on different calendars (FRED H.10 FX has a weekly publication
lag; FRED yields miss bond-market holidays that equities trade through). Each
factor forward-fills up to its own cap — 3 business days by default, 7 for
H.10 FX. A filled day produces a zero return downstream: a mild, *measured*
vol damping, recorded per factor per run rather than hidden. Gaps beyond the
cap are never filled silently — they block (§2.4).

Equity levels are the **adjusted** close, so equity positions are total-return
positions; share counts were struck from the unadjusted close on the anchor
date (where adjusted equals unadjusted by construction).

### 2.4 Data-quality gate

Checks run nightly before the risk step; findings persist to `dq_issues` with
a severity that decides the run's fate. The policy is **flag, never delete**:
auto-scrubbing genuine crash days would destroy the very tail VaR feeds on.

| Check | Trigger | Severity |
|---|---|---|
| `OUTLIER_RETURN` | LOG: \|r\| > 6× trailing EWMA vol **and** \|r\| > 3%; yields: \|Δy\| > 75bp/day | WARN — investigate, keep |
| `STALE` | a PRICE/FX factor repeats the identical value 5 sessions | WARN — dead source suspected |
| `UNIT_BOUND` | yield outside [−2%, 25%]; price/vol ≤ 0; FX > 50% from its 1-year median | BLOCK — structurally impossible, unit error |
| `GAP` / `FFILL_LIMIT` | missing beyond the factor's forward-fill cap | BLOCK / INFO within cap |

Any BLOCK downgrades the run to `PARTIAL` — results write, but the run is
visibly impaired; there is no silent green.

*Questions an interviewer would ask:* Why absolute bp shocks for rates — what
breaks with log returns on 2020 yields? Why flag rather than delete an outlier
return? What does a forward-filled day do to EWMA vol, and where would you see
that effect? Why is the FX bound relative to a median but the yield bound
absolute?

## 3. Methodology

### 3.1 Historical-simulation VaR

The scenario set is the trailing **500** daily joint return vectors. The full
book is revalued under each vector (§3.5), scenario P&Ls are aggregated per
desk, and

    VaR_a = -Quantile_{1-a}(P&L),   linear interpolation between order statistics.

At n=500 and a=0.99 the estimator interpolates between the 5th and 6th worst
losses, with almost all weight on the 6th. 500 days rather than the Basel
minimum 250 is a variance-versus-staleness trade: at 250 the same quantile
sits between the 3rd and 4th worst losses — an estimator that jumps when a
single scenario ages out of the window.

The known weakness of equal weights is regime lag, and it is quantified rather
than asserted: over the 750-day out-of-sample backfill, the HS VaR line lags
the April-2025 vol spike on the way in — every desk takes exceptions in the
first days of April — and then, once the spike's scenarios enter the window,
stays conservatively wide through the calm regime that follows (§4).

*Questions an interviewer would ask:* Why 500 days and not 250? Which order
statistics does the 99% estimate sit between at each window length? What
happens to HS VaR in the weeks after a vol-regime shift, and in the months
after the spike leaves the window ("ghosting")?

### 3.2 Filtered historical simulation (EWMA)

Per-factor conditional variance follows the RiskMetrics recursion

    sigma^2_t = lambda * sigma^2_{t-1} + (1 - lambda) * r^2_{t-1},   lambda = 0.94,

seeded with the population variance of the first 30 returns; the half-life is
ln 0.5 / ln 0.94 ≈ 11 days. lambda = 0.94 is a convention, not an estimate —
defended as such, with a GARCH(1,1) comparison on the roadmap rather than
implied.

Scenario construction devolatilizes and revolatilizes:

    z_s = r_s / sigma_s          (standardized joint historical vector, per factor)
    r~_s = z_s * sigma_{T+1}     (rescaled by the next-day vol forecast)

Cross-factor correlation survives because the scenarios remain *joint*
historical z-vectors — only the per-factor vol scale updates. The same 500
scenarios then price the same book through the same revaluation path as HS;
the only difference between the methods is the scenario matrix.

Failure modes, named: standardized residuals are assumed stationary (a
correlation-regime break violates this); a one-day vol overshoot inflates the
next day's entire scenario set (revol overshoot); forward-filled zero returns
damp sigma. The DQ gate bounds the third; the backfill chart shows the net
effect of the first two is still a large improvement in regime tracking.

*Questions an interviewer would ask:* Derive the recursion from exponential
weights. Why does FHS preserve cross-correlations when scaling per-factor?
What breaks if sigma_s is near zero for some historical date? Why EWMA and not
GARCH(1,1) — what would GARCH add and what does it cost?

### 3.3 Expected Shortfall 97.5% and stressed calibration

    ES_a = -mean( P&L | P&L <= Quantile_{1-a}(P&L) )

— the empirical mean of the tail at or beyond the 2.5% quantile; at n=500 that
averages roughly the 13 worst scenario losses. ES is reported at 97.5% beside
VaR 99% because that is Basel's calibration-neutral pair: under normality
VaR99 = 2.326 sigma and ES97.5 = 2.338 sigma — numerically almost identical,
but ES is coherent (subadditive) and sees tail *shape*, not just one quantile.
Both reference constants are encoded as unit tests.

**Stressed ES** revalues *today's* book on the daily shocks of a fixed
2008-09-12 → 2009-09-11 window and takes the same ES functional. On the
snapshot date it is ~2.3× the current-window ES ($2.74M vs $1.18M firm) — the
gap being exactly the point: current-regime ES understates what this book does
in a crisis regime. The window is fixed in the MVP; the programmatic
worst-window search over the full 2007+ history (FRTB's "period of significant
stress *for this portfolio*") is scheduled, with an integration test asserting
it lands in 2008–09 or 2020.

Firm ES can be allocated to desks by Euler allocation — each desk's mean P&L
over the firm's tail scenarios. Components sum exactly to firm ES and go
*negative* for hedging desks, which is the feature: it answers "which desk to
cut". The allocation is exercised per position in the RNIV concentration
measurement (R5, [docs/rniv.md](rniv.md)); a per-desk API surface arrives
with the desk-decomposition endpoint on the roadmap.

*Questions an interviewer would ask:* Prove ES is subadditive and give the
two-position example where VaR is not. Why did Basel pick 97.5% ES to replace
99% VaR? What makes a stressed window "right" for this portfolio, and why can
a fixed window rot? Show the Euler allocation sums to firm ES.

### 3.4 Stress testing

Historical replays are **data-driven**: only the window dates are pinned
(GFC: 2008-09-12 → 2008-11-20; COVID: 2020-02-19 → 2020-03-23). The shock
vector is the sum of stored daily returns over the window — additive for both
log returns and bp changes — applied instantaneously to today's book with full
revaluation. Sanity anchors are pinned by tests against the committed snapshot
(GFC window: SPX log return ≈ −0.50, 2Y yield ≈ −118bp, JPY ≈ +12%
safe-haven move). Three
hypothetical shocks (+100bp parallel rates, −20% equities, +10% USD) come from
a YAML catalog; factors absent from a scenario are shocked by zero — the
documented fill rule, until beta-fill rules arrive with the CCAR-style
scenarios. Post-shock yields floor at 1bp.

On the snapshot book the GFC replay is the worst scenario at ~−$13.1M firm
P&L. The FX desk drives it (−$11.1M): all four positions lose at once as risk
currencies fall against USD and the yen rallies into the short — the long-MXN
position is the single largest loss (~−$3.9M) — while the rates desk *gains*
~+$4.9M on the yield collapse. A concrete illustration that today's book, not
2008's, is what gets stressed.

*Questions an interviewer would ask:* Why is summing daily log returns over
the window legitimate but summing simple returns is not? An instantaneous
replay ignores path — when does that matter? Why does the *rates* desk make
money in the GFC replay while the firm loses?

### 3.5 Revaluation

The engine prices from a tidy positions frame (one row per position: ticker,
desk, factor, quantity, type, convention, and coupon/maturity for bonds); it
never touches the database.

**Equities / FX (exact):** `P&L = qty * S0 * (exp(r) - 1)`.

**Rates:** each position is a constant-maturity UST par-bond proxy priced with
the closed-form semiannual fixed-coupon formula

    P(y) = face * (c/y) * (1 - (1 + y/2)^(-2T)) + face * (1 + y/2)^(-2T),

with the y→0 limit `face * (1 + cT)` substituted below |y| < 1e-9. Par coupons
are struck at the anchor date's yields (so each bond prices at par on that
date — unit-tested to 1e-9). Under a scenario the bond is *fully repriced* at
the shocked yield; DV01 and dollar convexity exist only for the delta-gamma
path, computed by central-difference bump-and-reprice (±0.5bp; reference:
10Y 4% par bond ≈ $817.5 per bp per $1M face). The delta-gamma approximation
`P&L ≈ -DV01_bp * dy_bp + 0.5 * convexity * dy^2` (with `dy_bp` in basis
points and `dy` in decimal, matching the mixed units the two Greeks are quoted
in) is reserved for risk-theoretical P&L, so the future PLA test's HPL-RTPL
gap is real and internally generated, not simulated.

Each instrument maps to exactly one factor for VaR (SPY to its own price
series, the 10Y proxy to the 10Y constant-maturity yield). The **curve view**
exists alongside the proxy: a semiannual zero curve bootstrapped from the five
par CMT nodes (brentq per node, linear interpolation in zero rates, flat
extrapolation), bonds priced as cashflow strips off it, and par key-rate
DV01s measured by bumping one input node at a time and re-bootstrapping. The
nightly batch writes the per-desk KRD table (`risk_exposures`), served by the
API and dashboard. One subtlety worth knowing cold: at the anchor date the
book's par coupons make each bond the bootstrap's own calibration instrument,
so the KRD matrix is exactly diagonal *by construction* — cross-tenor risk
appears (and grows) as coupons drift off par, which is also why the
curve-vs-proxy VaR basis is nonzero inside shocked scenarios (§5.1 R7). VaR
pricing keeps the proxy, with the basis measured rather than assumed.

*Questions an interviewer would ask (curve):* Why is a node par bond's KRD
diagonal by construction? Derive the bootstrap fixed point at the 2Y node —
what makes it one-dimensional? What breaks with linear-in-zero interpolation
(forward-rate kinks), and what would you use instead?

*Questions an interviewer would ask:* Derive the closed form from the annuity
sum. Why bump-and-reprice instead of the analytic duration formula? What does
the constant-maturity proxy ignore that a real bond position has (roll-down,
carry, financing)? Where would linearization bite hardest in this book — which
desk, which scenario?

## 4. Backtesting and ongoing monitoring

**Clean P&L.** Each day's hypothetical P&L freezes the prior day's book and
levels and applies the realized factor returns — no fees, no intraday, no new
trades. An exception on day t+1 is `HPL_{t+1} < -VaR_t`: the comparison is
strictly out-of-sample (the VaR quoted on t never saw t+1's return).

**Kupiec proportion-of-failures** tests coverage: with x exceptions in n days
and target p = 1%,

    LR_pof = -2 [ ln L(p) - ln L(x/n) ] ~ chi2(1),

with the 0·ln 0 := 0 convention guarding the zero-exception case. Published
worked example, encoded as a known-answer test: n=250, x=5 gives LR ≈ 1.96,
p ≈ 0.16 — five exceptions against 2.5 expected and the test *cannot* reject.

**Christoffersen independence** fits a first-order Markov chain to the
exception indicator sequence and likelihood-ratio-tests whether an exception
today changes tomorrow's exception probability. This is the test that sees
*clustering*: ten consecutive exceptions in 500 days reject overwhelmingly
while the same ten spread evenly do not — Kupiec, by construction, scores both
identically (unit-tested pair). Conditional coverage adds the two statistics:
LR_cc = LR_pof + LR_ind ~ chi2(2). Clustered exceptions are precisely the
vol-regime failure mode of equal-weight HS, which is why both tests run
nightly.

**Basel traffic light**, 250-day window: green at 0–4 exceptions, amber 5–9
with multiplier add-ons rising 0.40 → 0.85, red at 10+ (add-on 1.00, model
presumed flawed). The regulatory response is deliberately *not* hypothesis
testing: at x=5 Kupiec's p-value is 0.16, yet the capital multiplier already
penalizes — supervisors price the asymmetry of accepting a bad model rather
than the Type-I error rate.

**Out-of-sample results** (750 trading days ending 2026-08-06, firm book):

| Method | Exceptions | Expected | Kupiec p | CC p | 250d zone |
|---|---|---|---|---|---|
| HS | 5 | 7.5 | 0.33 | 0.60 | GREEN |
| FHS | 10 | 7.5 | 0.38 | 0.60 | GREEN |

Both models pass everything — the difference is *where* the risk shows. Going
into the April-2025 vol spike the lagging HS window under-covers at desk
level: every desk takes exceptions in the first days of April, and only the
~40% diversification benefit keeps the firm aggregate clean (its five HS
exceptions sit elsewhere — two in late 2023, one in mid-2024, two in
mid-2026). After the spike's scenarios enter the window, firm HS VaR runs
conservatively wide for months of calm. FHS spends its near-nominal-rate
exceptions across regimes and re-tightens where HS cannot. Coverage alone
does not separate the two; the exception *timing* chart in the README and
dashboard does. That comparison — champion/challenger on identical scenarios,
differing only in the vol filter — is the model-selection argument in one
picture.

**Ongoing monitoring** is the nightly cycle itself: every batch writes
exceptions against the prior run's VaR, the dashboard recomputes rolling
zones, DQ issues persist with severities, and CI reruns the known-answer suite
(engine math, Kupiec/Christoffersen worked examples, bond-pricing identities)
on every commit. Scheduled next: the Acerbi–Székely ES backtest with simulated
critical values, and P&L attribution (Spearman + KS with MAR32 zones) once the
options sleeve makes RTPL diverge from HPL.

*Questions an interviewer would ask:* Write the Kupiec statistic from memory
and evaluate the n=250, x=5 case. Why does Christoffersen catch what Kupiec
cannot, and what does its n11 count mean? Why does the Basel multiplier start
at five exceptions when the hypothesis test can't reject there? Why must the
VaR in an exception check come from the *prior* run?

## 5. Assumptions and limitations

*The load-bearing section: each entry names the assumption, its consequence,
and the mitigation or roadmap item. Where a limitation is measurable it is
**measured**: [docs/rniv.md](rniv.md) is a risks-not-in-VaR inventory
regenerated from the snapshot by `python -m risk.jobs.rniv`, and §5.1 below
summarizes it.*

1. **sqrt(10) horizon scaling assumes iid daily P&L.** Under volatility
   clustering it under-scales in stressed regimes; on trending or
   mean-reverting windows it over-scales. Measured (R1): on the current
   500-day window, overlapping 10-day revaluation gives $2.97M firm VaR
   against $3.69M scaled — the scaled figure currently *overstates* by ~20%.
   The 10-day figures are reported as scaled, never as modeled, with the
   overlapping estimator kept as a standing check.
2. **One factor per instrument in the VaR path.** Each bond proxy loads on a
   single constant-maturity yield. Measured (R7): repricing the bond book on
   the bootstrapped curve under the same scenarios moves rates-desk VaR by
   +1.9% ($885k vs $868k) — the pricing-model basis; the key-rate DV01 table
   is written nightly and is diagonal at the anchor by construction, with
   off-diagonals growing as coupons drift off par. Adequate for a five-point
   node book; wrong for real curve trading.
3. **Constant-maturity par proxies ignore aging, roll-down, carry, and
   financing.** Positions are re-struck at par conceptually every day; clean
   P&L is price P&L only.
4. **Non-synchronous closes bias cross-asset correlation.** FRED H.10 FX is a
   noon-ET fixing; equities close 16:00 ET; yields are afternoon CMT reads.
   Same-day cross-asset correlations are attenuated, and the diversification
   benefit inherits that bias. Measured (R2): the benefit is 40.3% on 1-day
   returns and 39.3% on 2-day aggregation — about one percentage point of
   co-movement hides in the close-time gaps (SPY leads next-day GBP at +0.23
   vs +0.14 same-day). Named and measured, not fixed — production desks fight
   the same issue with synchronization overlays.
5. **Forward-filled days imprint zero returns** (bounded at 3 days, 7 for
   H.10 FX), damping vol for affected factors. Measured (R3): fills are 3.6%
   of factor-days in the window; correcting each factor's EWMA forecast to
   print-days-only raises firm FHS VaR by 1.9% (~$24k), concentrated in the
   H.10 FX factors (forecast ratios ~1.15). The fill count per factor per run
   is recorded; beyond-cap gaps block the run.
6. **The book is linear, so P&L attribution would be vacuous** — RTPL and HPL
   coincide by construction. PLA ships only with the options sleeve; shipping
   a trivially green PLA on a linear book would be validation theater.
7. **FRTB scope is the ES piece only.** Stressed calibration follows the FRTB
   idea; there is no liquidity-horizon cascade (a `liquidity_horizon` column
   exists on `risk_factors` as a placeholder for the MAR33.12 mapping, still
   uniform at its 10-day default), no NMRF/RFET, no IMA capital arithmetic.
   The phrase "FRTB-compliant" is deliberately banned from this repository.
8. **The stressed window is fixed (2008-09-12 → 2009-09-11).** A book can
   have its true worst regime elsewhere. Measured (R4): stressed ES on this
   book is $2.74M under the in-force window vs $2.10M for the 2022 hiking
   year and $1.91M for the COVID year — the fixed choice is currently the
   most conservative of the candidates, but that is checked, not assumed. The
   programmatic argmax search over 2007+ full-reval history is the scheduled
   replacement.
9. **Post-shock yields floor at 1bp**, which would truncate the rates desk's
   convexity gain in scenarios that drive short yields toward zero. No shipped
   scenario currently binds it — GFC-replay post-shock yields bottom out near
   2.3% — but a 2020-style front-end shock from today's levels would.
10. **Equal-weight HS dilutes new information by design**; the FHS comparison
    exists to quantify exactly that cost (§4). Equity risk is total-return
    (adjusted close), so dividend timing is smeared across history.
11. **No idiosyncratic-risk model, and tail risk is concentrated.** Single
    names carry only their own history; there is no factor model and no
    name-level shrinkage. Measured (R5): Euler allocation of firm ES puts
    33.9% on the top position and 79.5% on the top three (the 10Y/30Y
    duration proxies dominate; NVDA is the largest single-name component).
    Seven liquid mega-caps make the missing idio model tolerable; the
    concentration is monitored via the component table.

### 5.1 Quantified impacts (risks-not-in-VaR inventory)

Summary of [docs/rniv.md](rniv.md), regenerated by `python -m risk.jobs.rniv`
(as of 2026-08-06; Material ≥ 5% of the base measure, Monitor ≥ 1%):

| ID | Limitation | Measured impact | Class |
|---|---|---|---|
| R1 | sqrt(10) scaling vs overlapping 10-day reval | −$726k on the 10-day VaR (−19.7%) | Material |
| R2 | Asynchronous closes hide co-movement | diversification 40.3% (1d) → 39.3% (2d) | Immaterial |
| R3 | Forward-fill vol damping | +$24k on FHS VaR (+1.9%) | Monitor |
| R4 | Fixed stressed window | in-force $2.74M is the max of candidates ($1.91M–$2.10M) | Immaterial |
| R5 | Position concentration of firm ES | top position 33.9%, top 3 79.5% | Monitor |
| R6 | 1bp post-shock yield floor | unbinding; 230bp of headroom | Immaterial |
| R7 | One-factor ytm proxy vs bootstrapped curve | rates VaR +1.9% curve-priced; KRDs diagonal at anchor by construction | Monitor |

The point of the exercise is the discipline, not the add-ons: every §5 claim
that could be a number *is* a number, with the measurement code in the
repository and the classification rule stated up front.

## 6. Model governance

Every tunable lives in one frozen dataclass (`RiskConfig`) — lookback 500,
lambda 0.94, confidence pair (0.99, 0.975), forward-fill caps, the stressed
window. Each batch run records the git SHA and a hash of that config on its
`risk_runs` row, so any number in the results tables traces to exact code and
parameters. The committed snapshot makes the whole history a fixture: on every
push, CI rebuilds the database from scratch and asserts the firm 1-day VaR99
equals $1,168,055.33 to the cent, runs the known-answer suite (worked examples
from the literature encoded as tests), and executes the one-command demo path
end-to-end. Data
quality is enforced in-pipeline with persisted, severity-graded findings, and
batch runs are idempotent under an advisory-lock claim — a re-run can replace
a failed run but never silently duplicate a successful one.

In SR 11-7 terms: this document is the development evidence; the known-answer
tests and the 750-day out-of-sample backtest are validation; the nightly
exception writes, traffic-light zones, and DQ gate are ongoing monitoring; §5
is the limitations inventory, maintained with the same seriousness as the
code.
