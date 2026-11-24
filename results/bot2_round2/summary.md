# Bot 2, round 2: a 30% ceiling

Rules: SPEC.md, "Bot 2, round 2", committed before this run. Both candidates were chosen after seeing round 1. Same period, data and bootstrap paths as round 1 (mean block 2.9 days, seed 0).

| Candidate | Leverage | Median 10-year return | 10th percentile | Paths falling 30% | Backtest return | Volatility | Sharpe | Max drawdown | Worst year | Days with exposure cut | Deflated Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B6: B5 with the floor at 70% of peak | 2.2 | 6.5% | 0.7% | 0.0% | 5.5% | 14.1% | 0.34 | -25.6% | -12.0% (2018) | 98% | 0.785 |
| B7: B5 with cuts from a 15% to a 30% drawdown | 1.7 | 7.0% | 0.9% | 0.0% | 7.1% | 13.8% | 0.45 | -26.3% | -13.1% (2018) | 11% | 0.901 |
| Version 2, no leverage | 1.0 | 5.0% | | | 4.9% | 8.1% | 0.44 | -15.5% | -8.8% (2022) | | |

Candidate by the registered rule: B7: B5 with cuts from a 15% to a 30% drawdown.

- deflated Sharpe at least 0.95: no
- median beats version 2: yes
- worst drop smaller than 30%: yes

**Result: does not pass.** Under the registered rule, bot 2 is not launched.

The deflated Sharpe ratio counts all 18 trials; the best of 18 unskilled trials would be expected to reach an annual Sharpe ratio of about 0.16 by luck.

Reference only, not eligible (no floor, so the ceiling is not firm): B5 with no floor, sized so no more than 1 path in 10 falls 30%, uses 1.2x: median ten-year return 6.6%, 7.8% of paths falling 30% or more; full backtest 6.6% a year, worst drop -20.2%.
