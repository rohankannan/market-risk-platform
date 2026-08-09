# RiskDesk — Model Documentation

*Structured along the April 2026 interagency Supervisory Guidance on Model Risk
Management — Fed SR 26-2, OCC Bulletin 2026-13, FDIC FIL-15-2026, all issued
17 April 2026, superseding SR 11-7 — so: model development and use, validation
and monitoring, governance and controls, and a standing limitations inventory.
That guidance is explicit that it "does not set forth enforceable standards or
prescriptive requirements." For a trading book the text that binds is the market
risk capital rule at 12 CFR 217 subpart F, which requires a validation process
independent of model development covering conceptual soundness, ongoing
monitoring, and outcomes analysis including backtesting. §4 is organized on
those three. Where the two differ this document follows the rule and says so.
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
- **scenario P&L** for three historical replays (GFC 2008, COVID 2020, the 2022
  rate selloff) and four hypothetical shocks whose magnitudes are measured
  against the book's own history (§3.4);
- **backtest inputs** against clean P&L — daily hypothetical AND
  risk-theoretical P&L plus exceptions versus the prior run's VaR — from which
  the API computes Kupiec, Christoffersen, the Basel traffic light, and the
  P&L-attribution test (Spearman + KS with MAR32-style zones) on demand.

The book is sized so the standalone-VaR mix across desks is roughly
rates 45 / equity 30 / fx 25 — the risk-class mix large dealers disclose in
their FY2025 trading-VaR tables (measured on the snapshot: 46/28/26). The
equity desk carries a 1-month SPY collar (long 95% put, short 103% call, 78
contracts against the ~7,800-share holding) — the options sleeve that gives
the book gamma and vega and makes the attribution test real. On the
2026-08-06 snapshot the firm 1-day VaR99 is ~$1.14M, 57% of its $2M limit,
with a ~40% diversification benefit, net rates DV01 of ~$83k/bp, and net
vega of about -$2.7k per vol point.

**Out of scope, deliberately:** trade pricing, official intraday risk
measurement, counterparty and credit risk, and regulatory capital. The
end-of-day batch is the record. A flash tier does re-mark the book against
delayed quotes between closes, and §3.6 states what it is and is not: indicative
only, never written to the official tables, and refused outright when a quote
implies an implausible move. The stressed-ES piece follows FRTB's
*idea* (ES 97.5, stressed-period calibration) and the attribution test follows
the PLA *metrics*; neither is an FRTB implementation — no liquidity-horizon
cascade, no NMRF, no IMA capital arithmetic (§5).

## 2. Model inputs and data

### 2.1 Risk factors and sources

Seventeen factors drive the book: seven equity adjusted closes
(yfinance, Stooq fallback), four USD FX rates (FRED H.10, normalized at ingest
to USD per unit of foreign currency; the weekly publication lag is absorbed by
a 7-day forward-fill cap), five constant-
maturity Treasury par yields in percent (FRED: 3M, 2Y, 5Y, 10Y, 30Y), and
VIX, which prices the options sleeve as its (flat) implied vol. History runs from
2007 — deep enough to contain every stress window used in §3.4.

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

Fills are **provisional**: ingest windows anchor at each factor's last *real*
print, not its last row, so a filled date stays in the request until the
vendor publishes it, and staleness ages are measured against real prints — a
dead source runs into its cap and blocks rather than forward-filling forever
behind self-resetting fills.

Two controls extend the gate beyond the inputs. A **flash check** compares
each firm VaR measure (HS and FHS) to the prior run before the number is read
as final: a move beyond ±25% writes a `FLASH_DOD` warning carrying its own
explanation — per-desk VaR deltas, plus the largest vol-forecast movers where
the vol filter is the actual driver (FHS; an HS move comes from scenario
turnover and level changes). A large move is not an error; an *unexplained*
large move is. And a **revision log** (`data_revisions`) records every
ingested value that differs from what is stored — compared at the database's
own precision — classified: a synthetic fill replaced by the vendor's late
print is routine (`FFILL_REPLACED`, the H.10 publication cycle produces
these); a changed real print is a vendor restatement (`VENDOR_REVISION`) and
the reason yesterday's VaR may no longer be reproducible from today's
database. The log preserves the before-image, and
`run --date <d> --force` is the restatement path — an EOD restatement
outranks a backfill run for the same date in every read.

*Questions an interviewer would ask:* Why absolute bp shocks for rates — what
breaks with log returns on 2020 yields? Why flag rather than delete an outlier
return? What does a forward-filled day do to EWMA vol, and where would you see
that effect? Why is the FX bound relative to a median but the yield bound
absolute?

## 3. Model development and methodology

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
defended as such, and tested against a fitted GARCH(1,1) rather than left
implied — see the champion/challenger run below.

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

**Champion/challenger.** The GARCH(1,1) question is answered with a parallel
run, not an opinion: a hand-rolled Gaussian-QMLE GARCH filter runs beside the
EWMA champion over the trailing 250 days on the same historical windows, book,
and clean P&L — the scenario sets differ only by each model's vol rescaling,
which is the treatment under test — judged
against promotion criteria pre-registered before any result is computed
([docs/challenger_garch.md](challenger_garch.md), regenerated by
`python -m risk.jobs.challenger`). Current verdict: **HOLD** — outcomes are
indistinguishable (the same four exceptions, +6.4% mean VaR delta), and four
factors (the short-rate complex and EURUSD) fit at the stationarity boundary,
where the unconditional level is meaningless — IGARCH by accident. EWMA *is*
the IGARCH boundary case (omega 0, alpha 1−lambda, beta lambda; unit-tested
equivalence), so the champion deliberately imposes the infinite persistence
those degenerate fits stumble into — a defensible convention until a
challenger clears the health gate.

*Questions an interviewer would ask:* Derive the recursion from exponential
weights. Why does FHS preserve cross-correlations when scaling per-factor?
What breaks if sigma_s is near zero for some historical date? Derive the
GARCH(1,1) likelihood; why does persistence at the boundary make the
unconditional variance meaningless, and why must promotion criteria be
registered before results exist?

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
snapshot date it is ~2.3× the current-window ES ($2.61M vs $1.15M firm) — the
gap being exactly the point: current-regime ES understates what this book does
in a crisis regime. The window is fixed in the MVP; the programmatic
worst-window search over the full 2007+ history (FRTB's "period of significant
stress *for this portfolio*") is scheduled, with an integration test asserting
it lands in 2008–09 or 2020.

Firm ES can be allocated to desks by Euler allocation — each desk's mean P&L
over the firm's tail scenarios. Components sum exactly to firm ES and go
*negative* for hedging desks, which is the feature: it answers "which desk to
cut". The allocation is exercised per position in the RNIV concentration
measurement (R5, [docs/rniv.md](rniv.md)) and served per desk at
`/api/v1/desks/{desk}/decomposition`, whose waterfall reconciles the standalone
bucket VaRs and the diversification term to the desk total.

*Questions an interviewer would ask:* Prove ES is subadditive and give the
two-position example where VaR is not. Why did Basel pick 97.5% ES to replace
99% VaR? What makes a stressed window "right" for this portfolio, and why can
a fixed window rot? Show the Euler allocation sums to firm ES.

### 3.4 Stress testing

Historical replays are **data-driven**: only the window dates are pinned
(GFC: 2008-09-12 → 2008-11-20; COVID: 2020-02-19 → 2020-03-23; 2022 rate
selloff: 2022-01-03 → 2022-10-31). The shock vector is the sum of stored daily
returns over the window — additive for both log returns and bp changes —
applied instantaneously to today's book with full revaluation. Sanity anchors
are pinned by tests against the committed snapshot (GFC window: SPX log return
≈ −0.50, 2Y yield ≈ −118bp, JPY ≈ +12% safe-haven move). Factors absent from a
scenario are shocked by zero — the documented fill rule, until beta-fill rules
arrive with the CCAR-style scenarios. Post-shock yields floor at 1bp.

**Where the hypothetical magnitudes come from.** The catalog separates two
kinds of row, and the distinction is the answer to "why those numbers":

- *Sensitivity ladders* (+100bp parallel, −20% equities with a +20pt vol bid,
  +10% broad USD) are round on purpose — they read the book's directional
  exposure the way a desk quotes it, and are not forecasts. Roundness is not
  the same as arbitrary: each one's severity is measured against this book's
  own history and cited in the catalog. On overlapping 20-business-day moves
  (4,966 windows, 2007–2026) the +100bp shift sits at the 99.64th percentile
  of 10Y moves and was reached in 18 windows; −20% equities at the 99.52nd
  percentile of SPY moves, 24 windows; +10% USD at the 99.78th, 11 windows.
  Because each ladder moves a class uniformly it understates a book that is
  long high-beta names (NVDA's own p99 is roughly twice SPY's) and short
  safe-haven currencies.
- *Correlated scenarios* are calibrated, not chosen. `RISK_OFF` takes the
  **conditional tail mean**: the average move of every factor over the 50
  windows where 20-day SPY sat at or below its 1st percentile. That is where
  the co-movement the ladders cannot express comes from — equities −23% with
  vol +29pt, a flight-to-quality rally steepening from the front end (3M
  −56bp, 30Y −29bp), the yen bid +2.3% and the peso sold −15.6%.

`python -m risk.jobs.calibrate_stress` regenerates every number above into
`docs/stress_calibration.md` from the committed snapshot, and unit tests
recompute the quantiles and assert the catalog's declared shock types match
each factor's return convention — a bp value mislabeled as a log return would
be a 100× error applied silently.

**What the catalog was missing.** Both original replays are flight-to-quality
episodes in which this long-duration book *gains* on the rates leg, so the
worst case on record was the GFC replay at ~−$11.0M — FX-driven (−$11.1M, the
long-MXN position the single largest loss at ~−$3.9M) with rates *up* ~+$4.9M
and the equity desk's protective put clawing back ~$2M. Adding 2022 — the
correlated stock-bond selloff, 10Y +258bp with SPY −17.7% over the window —
produces **−$21.8M, roughly twice the GFC replay and the worst scenario in the
catalog**, concentrated in rates. A stress program of only 2008 and 2020 was
systematically blind to this book's adverse regime, which is the practical
argument for choosing replay windows by what threatens the *current* book
rather than by which crises are famous.

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
in) is reserved for risk-theoretical P&L, so the PLA test's HPL-RTPL gap is
real and internally generated, not simulated.

**Options:** the collar legs are European SPY options priced with hand-rolled
Black-Scholes (d1/d2, scipy only for the normal distribution). Like the bond
proxies they are constant-maturity, constant-moneyness instruments — struck
daily at moneyness times that day's spot, one month to expiry — so daily P&L
is price P&L under the day's joint spot/vol/rate move, with no theta or roll
(the same documented re-strike convention as the par bonds). Implied vol is
the VOL.SPX.IV30 factor (VIX as the 30-day SPX proxy for SPY, in points,
flat across strikes), the discount rate is the 3M CMT, and q = 0 because the
stored equity series is total-return. Shocked vols floor at one point. Under
mode="full" options fully reprice; the delta-gamma-vega Taylor lives only in
the risk-theoretical path.

Each *linear* instrument maps to exactly one factor for VaR (SPY to its own
price series, the 10Y proxy to the 10Y constant-maturity yield); an option
adds its vol and rate factors through instrument meta. The **curve view**
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

*Questions an interviewer would ask (options):* Derive d1 and put-call
parity. Why does a flat smile understate risk for the 95% put specifically?
What does the constant-moneyness re-strike assume away (theta, pin risk,
early exercise), and why is that consistent with the bond proxies? Where does
the VIX-as-SPY-IV basis bite hardest?

*Questions an interviewer would ask (curve):* Why is a node par bond's KRD
diagonal by construction? Derive the bootstrap fixed point at the 2Y node —
what makes it one-dimensional? What breaks with linear-in-zero interpolation
(forward-rate kinks), and what would you use instead?

*Questions an interviewer would ask:* Derive the closed form from the annuity
sum. Why bump-and-reprice instead of the analytic duration formula? What does
the constant-maturity proxy ignore that a real bond position has (roll-down,
carry, financing)? Where would linearization bite hardest in this book — which
desk, which scenario?

### 3.6 Model use

A number's meaning depends on which path produced it, so the system runs three
tiers and never lets them blur.

**The official record** is the end-of-day batch. It alone writes `risk_results`,
`pnl`, `scenario_results`, `risk_exposures` and `backtest_exceptions`; the API is
read-only over what the batch wrote. Everything in §4 is measured on this tier,
and it is the only tier a limit is checked against.

**The flash tier** re-marks the same book against delayed intraday quotes
between closes. It is labelled indicative, it writes nothing to the official
tables, its quote and revaluation caches expire in minutes, and it *refuses*
rather than guesses: a quote implying a move past 150bp, 25% or 25 vol points is
rejected, the prior close is carried, and the refusal is surfaced instead of
being silently absorbed. That bound exists because a vendor changed the scale of
its yield indices and the unguarded path fabricated a 416bp rally — the control
is there because the failure happened, not in anticipation of it.

**The sandbox** (`POST /api/v1/whatif`) is a documented exception to
API-reads-only: it scales positions and applies factor shocks in one
revaluation, at full float precision, so a catalog preset posted back unedited
reproduces the batch's scenario P&L to the cent. It persists nothing. It also
declines to report one thing it could easily print — the VaR of the shocked
book — because re-marking levels while reusing the unshocked return vectors
makes equity VaR *fall* as the mark falls. Reporting it would be worse than
omitting it, and the omission is named in the response rather than left for the
reader to notice.

The guidance's point about using a model beyond its intended purpose is the live
risk here: the flash and sandbox tiers are exactly where a reader could mistake
an indicative number for the record. Both are labelled at the point of use, in
the API payload and on the page, not only in this document.

*Questions an interviewer would ask:* Which tier would you show a desk head at
11am, and what would you refuse to tell them from it? Why is the VaR of a
shocked book not reported — what goes wrong? If the flash tier and the batch
disagree at 4pm, which is wrong, and how would you tell?

## 4. Validation and monitoring

### 4.1 Conceptual soundness

Validation of conceptual soundness is design evidence rather than performance
evidence: whether the modelling choices, assumptions and construction are
defensible before any outcome is scored. Four kinds of evidence carry it here,
and all four are executable rather than asserted.

*Known answers from the literature.* Black-Scholes reproduces Hull's textbook
example, put-call parity holds to 1e-12, the par-bond identity holds exactly
(coupon = yield ⇒ price = face), DV01 matches the published $817.5 per $1M on a
10Y 4% par bond, and Kupiec reproduces the worked n=250, x=5 case at p ≈ 0.16.
A hand-rolled implementation that agrees with an independent published number is
the cheapest real evidence of construction available to a one-person project.

*Analytic limits that pin the estimator.* Gaussian VaR99 = 2.326σ against
ES97.5 = 2.338σ (§3.3) tells you the two measures are numerically almost
identical under normality, so any material gap between them on this book is
tail shape rather than arithmetic — a sanity anchor on the ES path that no
amount of backtesting would supply.

*Boundary equivalence between the champion and the challenger.* EWMA is exactly
the IGARCH boundary case, ω=0, α=1−λ, β=λ, and that identity is asserted
bit-for-bit in a test rather than argued in prose. It makes the champion and
challenger provably the same family, which is what licenses comparing them on
outcomes alone.

*Design properties verified as invariants.* The key-rate DV01 matrix comes out
exactly diagonal for the node par bonds at the anchor — not a coincidence but a
consequence of those bonds being the bootstrap's own calibration instruments —
and the test asserts the diagonality so the property cannot silently break. The
off-node and off-par cases are tested to split across neighbouring nodes, which
is the evidence that the diagonal case is structural rather than a bug.

The guidance notes that benchmarking to other models may be more practical than
evaluating theoretical construction. §3.2's champion/challenger run is that
benchmark, and §6 records that it currently returns HOLD.

### 4.2 Outcomes analysis

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
| HS | 6 | 7.5 | 0.57 | 0.81 | GREEN |
| FHS | 10 | 7.5 | 0.38 | 0.60 | GREEN |

Both models pass everything — the difference is *where* the risk shows. Going
into the April-2025 vol spike the lagging HS window under-covers at desk
level: every desk takes exceptions in the first days of April, and only the
~40% diversification benefit keeps the firm aggregate clean (its six HS
exceptions sit elsewhere — two in late 2023, two in 2024, two in
mid-2026). After the spike's scenarios enter the window, firm HS VaR runs
conservatively wide for months of calm. FHS spends its near-nominal-rate
exceptions across regimes and re-tightens where HS cannot. Coverage alone
does not separate the two; the exception *timing* chart in the README and
dashboard does. That comparison — champion/challenger on identical scenarios,
differing only in the vol filter — is the model-selection argument in one
picture.

**P&L attribution.** Spearman rank correlation and the two-sample KS
statistic between daily hypothetical P&L (full revaluation) and
risk-theoretical P&L (the linearized path), zoned at the MAR32.41 thresholds
(green requires rho >= 0.80 and KS <= 0.09). Over the trailing 250 paired
days the equity desk sits at rho 0.9999, KS 0.020 — GREEN. The gap
*decomposes*, and knowing the decomposition is the point: the linear legs
alone produce KS 0.016 (their RTPL is `qty*S0*r` against an HPL of
`qty*S0*(exp(r)-1)` — the log-linearization convexity, a mean gap of about
$1.8k/day), and the collar adds its Taylor error on top (about $1.2k/day;
options-only KS 0.024 from gamma, vanna, and the excluded rho). So the
pre-options test was never strictly vacuous — it was nearly degenerate, its
only content being the log-linearization — and the sleeve is what gives the
statistic something structural to see. A desk that failed would lose IMA
eligibility at a real bank; here it would fail the promotion gate the same
way the GARCH challenger did. Served at `/backtest/pla` with the HPL-vs-RTPL
scatter on the dashboard.

### 4.3 Ongoing monitoring

Ongoing monitoring is the nightly cycle itself: every batch writes exceptions
against the prior run's VaR, the dashboard recomputes rolling zones, DQ issues
persist with severities, and CI reruns the known-answer suite (engine math,
Kupiec/Christoffersen worked examples, bond-pricing identities) on every commit.
The day-over-day flash check raises a WARN when either VaR measure moves more
than 25% against the prior run, which is monitoring for model *deterioration*
rather than for coverage — the failure the outcomes tests are least able to see
quickly, because a coverage test needs hundreds of days to say anything.

Scheduled next: the Acerbi–Székely ES backtest with simulated critical values.
Note what its absence means precisely — every statistic in §4.2 tests the VaR
path, and nothing yet tests ES97.5 or the stressed ES, which are the measures
§3 actually leads with.

*Questions an interviewer would ask:* Write the Kupiec statistic from memory
and evaluate the n=250, x=5 case. Why does PLA need BOTH Spearman and KS —
what failure does each see that the other cannot? Why does Christoffersen catch what Kupiec
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
   500-day window, overlapping 10-day revaluation gives $2.87M firm VaR
   against $3.60M scaled — the scaled figure currently *overstates* by ~20%.
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
   benefit inherits that bias. Measured (R2): the benefit is 40.4% on 1-day
   returns and 38.4% on 2-day aggregation — two percentage points of
   co-movement hide in the close-time gaps (SPY leads next-day GBP at +0.23
   vs +0.14 same-day). Named and measured, not fixed — production desks fight
   the same issue with synchronization overlays.
5. **Forward-filled days imprint zero returns** (bounded at 3 days, 7 for
   H.10 FX), damping vol for affected factors. Measured (R3): fills are 3.6%
   of factor-days in the window; correcting each factor's EWMA forecast to
   print-days-only raises firm FHS VaR by 3.0% (~$38k), concentrated in the
   H.10 FX factors (forecast ratios ~1.15). The fill count per factor per run
   is recorded; beyond-cap gaps block the run.
6. **The attribution test is only as sharp as the book's nonlinearity.** The
   collar's gamma is modest, so today's GREEN PLA discriminates weakly — a
   larger options book would stress the Taylor harder — and the statistic's
   decomposition (§4) shows the linear legs' log-linearization still carries
   most of it. The RTPL exclusions (rho, cross terms) are design choices; the
   nearly-degenerate pre-options history is preserved in git, not rewritten.
7. **FRTB scope is the ES piece only.** Stressed calibration follows the FRTB
   idea; there is no liquidity-horizon cascade (a `liquidity_horizon` column
   exists on `risk_factors` as a placeholder for the MAR33.12 mapping, still
   uniform at its 10-day default), no NMRF/RFET, no IMA capital arithmetic.
   The phrase "FRTB-compliant" is deliberately banned from this repository.
8. **The stressed window is fixed (2008-09-12 → 2009-09-11).** A book can
   have its true worst regime elsewhere. Measured (R4): stressed ES on this
   book is $2.61M under the in-force window vs $2.06M for the 2022 hiking
   year and $1.80M for the COVID year — the fixed choice is currently the
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
    37.3% on the top position and 86.6% on the top three (the 30Y/10Y
    duration proxies dominate; NVDA is the largest single-name component).
    Seven liquid mega-caps make the missing idio model tolerable; the
    concentration is monitored via the component table.

12. **The options sleeve prices on a flat smile with proxy inputs.** VIX
    stands in for SPY 1-month implied vol (an index-vs-ETF, 30d-vs-1M basis),
    a single vol level covers every strike (no skew risk — precisely where a
    95% put lives), q = 0 leans on the total-return spot series, and the
    constant-moneyness re-strike removes theta, pin risk, and early exercise.
    Each is the options sibling of a limitation already carried by the bond
    proxies, and the parametric-IV VaR on the roadmap addresses the vol-risk
    piece first.

13. **The backtest values today's book on every historical date.** `read_book`
    takes no date argument, so the 750-day backfill freezes one composition and
    walks history past it. The book actually held in 2023 never existed — there
    is no position history to reconstruct — so this is not a bias against a
    knowable truth but a dependence of the published verdict on a chosen
    composition. Re-backfilling across ten defensible compositions leaves
    coverage robust (HS 5–8 exceptions against 6 published, Kupiec never
    rejects) and the traffic-light zone fragile: the published FHS run sits one
    exception short of amber, and three variants cross — including the peer risk-
    class mix that §1 cites as the book's own realism reference. At a bank that
    is a capital multiplier moving 3.00 → 3.40, so R8 carries it as Material.
    The fix is a bitemporal position store, which needs a trade feed this
    project has no source for; it is disclosed rather than repaired, and the
    honest reading of §4.2 is that it tests the model on a fixed book rather
    than the book that was held.

### 5.1 Quantified impacts (risks-not-in-VaR inventory)

Summary of [docs/rniv.md](rniv.md), regenerated by `python -m risk.jobs.rniv`
(as of 2026-08-06; Material ≥ 5% of the base measure, Monitor ≥ 1%):

| ID | Limitation | Measured impact | Class |
|---|---|---|---|
| R1 | sqrt(10) scaling vs overlapping 10-day reval | −$729k on the 10-day VaR (−20.3%) | Material |
| R2 | Asynchronous closes hide co-movement | diversification 40.4% (1d) → 38.4% (2d) | Monitor |
| R3 | Forward-fill vol damping | +$38k on FHS VaR (+3.0%) | Monitor |
| R4 | Fixed stressed window | in-force $2.61M is the max of candidates ($1.80M–$2.06M) | Immaterial |
| R5 | Position concentration of firm ES | top position 37.3%, top 3 86.6% | Monitor |
| R6 | 1bp post-shock yield floor | unbinding; 230bp of headroom | Immaterial |
| R7 | One-factor ytm proxy vs bootstrapped curve | rates VaR +1.9% curve-priced; KRDs diagonal at anchor by construction | Monitor |
| R8 | Backtest values today's book on every date | FHS 1 exception from amber (4/250); 3 of 10 compositions cross, multiplier 3.00 → 3.40 (+13.3%) | Material |

The point of the exercise is the discipline, not the add-ons: every §5 claim
that could be a number *is* a number, with the measurement code in the
repository and the classification rule stated up front.

## 6. Governance and controls

Every tunable lives in one frozen dataclass (`RiskConfig`) — lookback 500,
lambda 0.94, confidence pair (0.99, 0.975), forward-fill caps, the stressed
window. Each batch run records the git SHA and a hash of that config on its
`risk_runs` row, so any number in the results tables traces to exact code and
parameters. The committed snapshot makes the whole history a fixture: on every
push, CI rebuilds the database from scratch and asserts the firm 1-day VaR99
equals $1,137,118.30 to the cent, runs the known-answer suite (worked examples
from the literature encoded as tests), and executes the one-command demo path
end-to-end. Data
quality is enforced in-pipeline with persisted, severity-graded findings, and
batch runs are idempotent under an advisory-lock claim — a re-run can replace
a failed run but never silently duplicate a successful one.

Model change control exists in miniature: a candidate model runs in parallel
with the champion over a regulatory window and is judged by pre-registered
promotion criteria — fit health first, then outcomes, then adoption stability
— with the change pack carrying the candidate's parameter hash and the git
SHA (§3.2's GARCH challenger is the live example, currently HOLD on the
fit-health gate).

**Effective challenge, and the one place this project cannot supply it.** The
2026 guidance keeps "effective challenge" and defines it as critical analysis by
objective experts with the expertise to challenge, "sufficient independence to
maintain objectivity," and the standing to force a change. Two of those three
are demonstrable here and one is not. The expertise and the standing are
evidenced by the harness overruling its author: the challenger gate returned
HOLD on a candidate the author wanted to promote, the sandbox refuses to report
a VaR it could trivially have printed, and the flash tier refuses a quote rather
than trusting it. The independence is not: development and validation are the
same person, and no amount of tooling changes that. It is worth being precise
about what the guidance now says on this, because it is the one clause that
moves in this project's favour — "the quality of validation process depends on
the rigor and effectiveness of the review rather than on organizational
structure of the banking organization's risk management function." Rigor is
demonstrable by a single author; the organizational half is not, and is recorded
in §5 as a limitation rather than papered over.

**Model materiality.** The guidance ties the depth of oversight to materiality,
which it derives from model exposure and purpose. `docs/rniv.md` already applies
that logic to limitations rather than to models, with a coded rule stated up
front: Material at ≥5% of the base measure, Monitor at ≥1%, otherwise
Immaterial. Every entry carries a measured number and a class, so the register
sorts by consequence instead of by how easy the item was to notice.

**Mapped to the 2026 guidance:** §§1–3 are model development and use (§1 the
purpose and perimeter, §2 the inputs and data risk, §3 the development and
methodology); §4.1 is the conceptual-soundness evidence, §4.2 the outcomes
analysis — the 750-day out-of-sample backtest, the coverage and independence
tests, the traffic light, and the attribution test — and §4.3 the ongoing
monitoring, which the nightly exception writes, DQ gate, flash check and
revision log implement; this section is governance and controls; §5 is the
limitations inventory, maintained with the same seriousness as the code. The
guidance's vendor and third-party section has no analogue here and does not need
one: every model in this repository is hand-rolled, with only optimizers and
statistical distributions imported, so there is no third-party model whose
conceptual soundness would have to be taken on trust. That is a scope fact, not
a control.
