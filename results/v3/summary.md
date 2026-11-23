# Backtest results, spec version 3

Run on 2026-11-23 00:32 ET with Yahoo Finance data downloaded at run time. Simulation from 2007-02-12 to 2026-11-20, starting at $100,000. Trades fill at the next day's open with a 5 bp cost; cash earns the 13-week T-bill rate.

**Result: FAIL** on the three bars set in SPEC.md before this run.

| Bar | Result |
| --- | --- |
| Sharpe ratio beats B1 (13 ETFs) | 0.35 vs 0.31: yes |
| Max drawdown smaller than B1 (13 ETFs) | -15.2% vs -25.2%: yes |
| Deflated Sharpe ratio at least 0.95 | 0.875 (8 trials): no |

Counting all 8 trials, version 1's deflated Sharpe ratio is now 0.947.

Counting all 8 trials, version 2's deflated Sharpe ratio is now 0.943.

## Full period

| Portfolio | Annual return | Annual volatility | Sharpe (vs T-bills) | Max drawdown | Worst calendar year | Turnover per year | Average invested |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Trend bot (v3) | 4.0% | 7.7% | 0.35 | -15.2% | -6.9% (2022) | 1021% | 94% |
| Trend bot (v2) | 5.0% | 8.1% | 0.45 | -15.5% | -8.8% (2022) | 925% | 89% |
| Trend bot (v1) | 3.8% | 5.1% | 0.46 | -7.2% | -2.9% (2015) | 539% | 56% |
| B1: same 13 ETFs, always invested | 3.7% | 7.9% | 0.31 | -25.2% | -15.5% (2008) | 18% | 92% |
| B1: same 9 ETFs, always invested | 4.7% | 8.9% | 0.38 | -26.8% | -16.6% (2008) | 24% | 90% |
| B2: 60% SPY / 40% IEF | 7.8% | 11.0% | 0.60 | -32.5% | -17.9% (2008) | 23% | 100% |
| B3: SPY buy and hold | 10.3% | 19.6% | 0.52 | -55.2% | -36.8% (2008) | 2% | 100% |

First and last calendar years are partial (2007 starts in February, 2026 ends in November).

![Equity curves](equity.png)

## Sub-periods

| Period | Portfolio | Annual return | Sharpe | Max drawdown |
| --- | --- | --- | --- | --- |
| 2007-2012 | Trend bot (v3) | 5.3% | 0.54 | -14.2% |
| 2007-2012 | Trend bot (v2) | 5.8% | 0.60 | -15.4% |
| 2007-2012 | Trend bot (v1) | 4.2% | 0.58 | -5.4% |
| 2007-2012 | B1: same 13 ETFs, always invested | 3.8% | 0.34 | -25.2% |
| 2007-2012 | B2: 60% SPY / 40% IEF | 5.1% | 0.37 | -32.5% |
| 2007-2012 | B3: SPY buy and hold | 2.1% | 0.17 | -55.2% |
| 2013-2019 | Trend bot (v3) | 1.6% | 0.16 | -11.9% |
| 2013-2019 | Trend bot (v2) | 3.7% | 0.44 | -10.2% |
| 2013-2019 | Trend bot (v1) | 2.3% | 0.40 | -6.0% |
| 2013-2019 | B1: same 13 ETFs, always invested | 1.9% | 0.24 | -13.1% |
| 2013-2019 | B2: 60% SPY / 40% IEF | 9.6% | 1.21 | -10.5% |
| 2013-2019 | B3: SPY buy and hold | 14.2% | 1.04 | -19.3% |
| 2020-2026 | Trend bot (v3) | 5.1% | 0.30 | -15.2% |
| 2020-2026 | Trend bot (v2) | 5.4% | 0.31 | -15.5% |
| 2020-2026 | Trend bot (v1) | 4.8% | 0.37 | -7.2% |
| 2020-2026 | B1: same 13 ETFs, always invested | 5.4% | 0.33 | -16.2% |
| 2020-2026 | B2: 60% SPY / 40% IEF | 8.0% | 0.46 | -21.2% |
| 2020-2026 | B3: SPY buy and hold | 13.1% | 0.57 | -33.7% |

## Expectancy per trade

A trade is one stretch from opening a position in an ETF to fully exiting it, including resizes in between. Results are a share of total portfolio equity at entry.

| Closed trades | Win rate | Average win | Average loss | Expectancy per trade | Still open |
| --- | --- | --- | --- | --- | --- |
| 567 | 25.7% | 1.04% | -0.19% | 0.124% | 8 |

## Trials

The deflated Sharpe ratio counts all 8 trials below. Given their spread, the best of 8 unskilled trials would be expected to reach an annual Sharpe ratio of about 0.09 by luck.

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

## Cost sensitivity (version 3)

| Cost per dollar traded | Annual return | Sharpe |
| --- | --- | --- |
| 5 bp | 4.0% | 0.35 |
| 10 bp | 3.5% | 0.28 |
| 20 bp | 2.5% | 0.16 |

## Expected range for the live run

Over every 60-trading-day window in the backtest, the bot's return ran from -4.4% (5th percentile) to 6.3% (95th percentile), with a median of 1.2%. 66% of those windows ended positive. The live paper run is judged against this band.
