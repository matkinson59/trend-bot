# Backtest results, spec version 4

Run on 2026-11-23 00:32 ET with Yahoo Finance data downloaded at run time. Simulation from 2007-02-12 to 2026-11-20, starting at $100,000. Trades fill at the next day's open with a 5 bp cost; cash earns the 13-week T-bill rate.

**Result: FAIL** on the three bars set in SPEC.md before this run.

| Bar | Result |
| --- | --- |
| Sharpe ratio beats B1 (13 ETFs at 1.5x) | 0.31 vs 0.29: yes |
| Max drawdown smaller than B1 (13 ETFs at 1.5x) | -20.9% vs -33.8%: yes |
| Deflated Sharpe ratio at least 0.95 | 0.810 (9 trials): no |

Counting all 9 trials, version 1's deflated Sharpe ratio is now 0.936.

Counting all 9 trials, version 2's deflated Sharpe ratio is now 0.932.

Counting all 9 trials, version 3's deflated Sharpe ratio is now 0.854.

## Full period

| Portfolio | Annual return | Annual volatility | Sharpe (vs T-bills) | Max drawdown | Worst calendar year | Turnover per year | Average invested |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Trend bot (v4) | 4.4% | 10.8% | 0.31 | -20.9% | -10.6% (2022) | 1211% | 98% |
| Trend bot (v3) | 4.0% | 7.7% | 0.35 | -15.2% | -6.9% (2022) | 1021% | 94% |
| Trend bot (v2) | 5.0% | 8.1% | 0.45 | -15.5% | -8.8% (2022) | 925% | 89% |
| Trend bot (v1) | 3.8% | 5.1% | 0.46 | -7.2% | -2.9% (2015) | 539% | 56% |
| B1: same 13 ETFs, always invested at 1.5x | 4.2% | 10.8% | 0.29 | -33.8% | -21.4% (2008) | 39% | 97% |
| B2: 60% SPY / 40% IEF | 7.8% | 11.0% | 0.60 | -32.5% | -17.9% (2008) | 23% | 100% |
| B3: SPY buy and hold | 10.3% | 19.6% | 0.52 | -55.2% | -36.8% (2008) | 2% | 100% |

First and last calendar years are partial (2007 starts in February, 2026 ends in November).

Average invested counts dollars held. Version 4's average exposure, counting each 2x fund twice, was 136%, and its highest was 150%.

![Equity curves](equity.png)

## Sub-periods

| Period | Portfolio | Annual return | Sharpe | Max drawdown |
| --- | --- | --- | --- | --- |
| 2007-2012 | Trend bot (v4) | 6.3% | 0.48 | -20.0% |
| 2007-2012 | Trend bot (v3) | 5.3% | 0.54 | -14.2% |
| 2007-2012 | Trend bot (v2) | 5.8% | 0.60 | -15.4% |
| 2007-2012 | Trend bot (v1) | 4.2% | 0.58 | -5.4% |
| 2007-2012 | B1: same 13 ETFs, always invested at 1.5x | 4.5% | 0.32 | -33.8% |
| 2007-2012 | B2: 60% SPY / 40% IEF | 5.1% | 0.37 | -32.5% |
| 2007-2012 | B3: SPY buy and hold | 2.1% | 0.17 | -55.2% |
| 2013-2019 | Trend bot (v4) | 1.3% | 0.11 | -17.5% |
| 2013-2019 | Trend bot (v3) | 1.6% | 0.16 | -11.9% |
| 2013-2019 | Trend bot (v2) | 3.7% | 0.44 | -10.2% |
| 2013-2019 | Trend bot (v1) | 2.3% | 0.40 | -6.0% |
| 2013-2019 | B1: same 13 ETFs, always invested at 1.5x | 1.8% | 0.18 | -18.1% |
| 2013-2019 | B2: 60% SPY / 40% IEF | 9.6% | 1.21 | -10.5% |
| 2013-2019 | B3: SPY buy and hold | 14.2% | 1.04 | -19.3% |
| 2020-2026 | Trend bot (v4) | 5.6% | 0.28 | -20.9% |
| 2020-2026 | Trend bot (v3) | 5.1% | 0.30 | -15.2% |
| 2020-2026 | Trend bot (v2) | 5.4% | 0.31 | -15.5% |
| 2020-2026 | Trend bot (v1) | 4.8% | 0.37 | -7.2% |
| 2020-2026 | B1: same 13 ETFs, always invested at 1.5x | 6.1% | 0.33 | -21.0% |
| 2020-2026 | B2: 60% SPY / 40% IEF | 8.0% | 0.46 | -21.2% |
| 2020-2026 | B3: SPY buy and hold | 13.1% | 0.57 | -33.7% |

## Expectancy per trade

A trade is one stretch from opening a position in an ETF to fully exiting it, including resizes in between. Results are a share of total portfolio equity at entry.

| Closed trades | Win rate | Average win | Average loss | Expectancy per trade | Still open |
| --- | --- | --- | --- | --- | --- |
| 956 | 35.3% | 0.65% | -0.22% | 0.082% | 8 |

## Trials

The deflated Sharpe ratio counts all 9 trials below. Given their spread, the best of 9 unskilled trials would be expected to reach an annual Sharpe ratio of about 0.11 by luck.

| # | Trial | Annual return | Sharpe | Max drawdown |
| --- | --- | --- | --- | --- |
| 1 | Spec v1 as written | 3.8% | 0.46 | -7.2% |
| 2 | Shorter lookbacks | 3.7% | 0.44 | -8.1% |
| 3 | Longer lookbacks | 3.4% | 0.38 | -8.5% |
| 4 | 63-day volatility window | 3.9% | 0.47 | -6.7% |
| 5 | 0% hurdle | 3.9% | 0.46 | -7.9% |
| 6 | Inverse-volatility weights | 3.8% | 0.55 | -6.4% |
| 7 | Spec v2: 10% volatility as a target | 5.0% | 0.45 | -15.5% |
| 8 | Spec v3: 13 ETFs | 4.0% | 0.35 | -15.2% |
| 9 | Spec v4: v3 at 1.5x through 2x funds | 4.4% | 0.31 | -20.9% |

## Cost sensitivity (version 4)

| Cost per dollar traded | Annual return | Sharpe |
| --- | --- | --- |
| 5 bp | 4.4% | 0.31 |
| 10 bp | 3.8% | 0.25 |
| 20 bp | 2.5% | 0.14 |

## Expected range for the live run

Over every 60-trading-day window in the backtest, the bot's return ran from -6.4% (5th percentile) to 8.6% (95th percentile), with a median of 1.4%. 63% of those windows ended positive. The live paper run is judged against this band.
