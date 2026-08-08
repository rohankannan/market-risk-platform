# Model change pack — challenger vol filter for FHS VaR

*Champion: EWMA (lambda = 0.94). Candidate: GARCH(1,1), Gaussian QMLE per factor, parameters frozen at 2025-08-19 (no look-ahead into the 250-day evaluation window ending 2026-08-06). Candidate hash `1976d3184878`, code `57e075f-dirty`. Regenerate: `python -m risk.jobs.challenger`.*

## Promotion criteria (pre-registered)

Fixed before results are computed; the verdict is mechanical:

1. Fit health: every factor converges with persistence <= 0.999
   (a boundary-stuck fit has a meaningless unconditional level - promoting
   it would be adopting IGARCH by accident).
2. Challenger 250-day Basel zone is GREEN.
3. Coverage error |exceptions - expected| does not worsen vs champion.
4. Christoffersen conditional coverage does not reject (p >= 0.05).
5. Mean daily |VaR delta| <= 25% (adoption stability).

## Candidate parameters

| Factor | alpha | beta | persistence | half-life (d) | uncond vol | sample vol | fit health |
|---|---|---|---|---|---|---|---|
| EQ.AAPL | 0.093 | 0.873 | 0.9655 | 20 | 0.01933 | 0.01981 | ok |
| EQ.JNJ | 0.092 | 0.860 | 0.9520 | 14 | 0.01065 | 0.01107 | ok |
| EQ.JPM | 0.100 | 0.880 | 0.9799 | 34 | 0.01973 | 0.02347 | ok |
| EQ.MSFT | 0.098 | 0.863 | 0.9610 | 17 | 0.01742 | 0.01749 | ok |
| EQ.NVDA | 0.082 | 0.898 | 0.9800 | 34 | 0.03482 | 0.03105 | ok |
| EQ.SPY | 0.137 | 0.841 | 0.9775 | 30 | 0.01169 | 0.01247 | ok |
| EQ.XOM | 0.081 | 0.911 | 0.9921 | 87 | 0.01743 | 0.01696 | ok |
| FX.EURUSD | 0.078 | 0.922 | 0.9998 | 3856 | 0.04164 | 0.005632 | BOUNDARY |
| FX.GBPUSD | 0.058 | 0.933 | 0.9914 | 80 | 0.006291 | 0.006097 | ok |
| FX.JPYUSD | 0.051 | 0.944 | 0.9947 | 131 | 0.00728 | 0.006303 | ok |
| FX.MXNUSD | 0.101 | 0.893 | 0.9942 | 120 | 0.01049 | 0.007723 | ok |
| IR.UST.10Y | 0.047 | 0.946 | 0.9932 | 101 | 5.855 | 5.796 | ok |
| IR.UST.2Y | 0.154 | 0.846 | 0.9997 | 2529 | 30.59 | 5.207 | no convergence |
| IR.UST.30Y | 0.053 | 0.936 | 0.9892 | 64 | 5.513 | 5.503 | ok |
| IR.UST.3M | 0.121 | 0.879 | 0.9997 | 2153 | 11.52 | 4.67 | BOUNDARY |
| IR.UST.5Y | 0.112 | 0.888 | 0.9999 | 4679 | 49.02 | 5.983 | BOUNDARY |
| VOL.SPX.IV30 | 0.368 | 0.620 | 0.9882 | 58 | 4.665 | 1.994 | ok |

EWMA is the IGARCH boundary (omega 0, alpha 1-lambda = 0.06, beta = lambda): every fitted persistence below 1 is the candidate disagreeing with the champion's infinite-memory assumption.

## Outcomes (firm, out-of-sample)

| | Champion (EWMA-FHS) | Challenger (GARCH-FHS) |
|---|---|---|
| Exceptions / expected | 4 / 2.5 | 4 / 2.5 |
| Kupiec p | 0.380 | 0.380 |
| Conditional coverage p | 0.638 | 0.638 |
| Basel zone, 250d (multiplier) | GREEN (3.00) | GREEN (3.00) |
| Average firm VaR | $955,479 | $1,007,376 |

Exception days: 4 shared, 0 champion-only, 0 challenger-only.

## Impact analysis

Daily VaR delta (challenger vs champion): mean +6.4%, mean absolute 8.0%, worst 28.6% on 2026-01-12.

## Verdict: HOLD

Failed criteria:

- fit health: non-converged or boundary-persistence factors: FX.EURUSD, IR.UST.2Y, IR.UST.3M, IR.UST.5Y

## Caveats

- Parameters are frozen at the window start; production would refit on a
  schedule (monthly) with the same pack regenerated per refit.
- Both models see the same forward-fill-imprinted returns (RNIV R3), so
  the comparison is fair but inherits that damping.
- One window, one book: a promotion decision would want this pack over
  several windows (the 750-day chart covers the champion only).
