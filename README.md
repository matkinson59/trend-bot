# Trend Bot

A paper-trading bot that follows price trends across nine ETFs. It holds an ETF only while that ETF's trend beats the return on Treasury bills, sizes positions against a 10% yearly volatility limit, and never shorts or borrows. Orders go to an Alpaca paper account, which uses simulated money and real market prices.

Built by Miles Atkinson. The rules were written down in a spec before any backtest results existed, and every backtest run gets a row in `trials.csv`, so the results can be checked for overfitting.

## Status

| Dates | Work | State |
| --- | --- | --- |
| Nov 23 to 29, 2026 | Trading rules and data loader | Done |
| Nov 30 to Dec 6 | Backtest, benchmarks and validation | Done (versions 1 and 2 passed; 3 and 4 failed) |
| Nov 23 | Rules locked at version 2 | Done |
| Nov 23 | Live bot built and tested on a simulated account | Done |
| Dec 7 to 13 | Live bot in dry-run mode | Not started |
| Dec 14 | Live paper trading starts | Not started |

## Backtest

Four versions were written into the spec before each was tested, from February 2007 to November 2026, starting with $100,000 and paying a 5 basis point cost on every trade. Version 1 treats 10% volatility as a ceiling, version 2 as a target, version 3 adds four ETFs to version 2, and version 4 is version 3 at 1.5 times the size through 2x funds.

| Portfolio | Ending value | Annual return | Volatility | Sharpe | Max drawdown | Passed its bars |
| --- | --- | --- | --- | --- | --- | --- |
| Version 1 | $207,997 | 3.8% | 5.1% | 0.46 | -7.2% | Yes |
| Version 2 | $259,331 | 5.0% | 8.1% | 0.45 | -15.5% | Yes |
| Version 3 | $215,498 | 4.0% | 7.7% | 0.35 | -15.2% | No |
| Version 4 | $231,481 | 4.4% | 10.8% | 0.31 | -20.9% | No |
| 60% SPY / 40% IEF | $440,824 | 7.8% | 11.0% | 0.60 | -32.5% | |
| SPY | $686,599 | 10.3% | 19.6% | 0.52 | -55.2% | |

Versions 3 and 4 failed the deflated Sharpe ratio bar (0.875 and 0.810 against 0.95). Counting all nine trials run so far, versions 1 and 2 also fall just below it (0.936 and 0.932). No version earned as much as SPY or a 60/40 portfolio. Version 2 is locked for the live paper run and is the only version the bot runs; the live plan (cheaper equivalent funds, idle cash in a T-bill fund) is in [SPEC.md](SPEC.md#locked-version-2).

Full results: [comparison of all versions](results/comparison.md), [version 1](results/v1/summary.md), [version 2](results/v2/summary.md), [version 3](results/v3/summary.md), [version 4](results/v4/summary.md).

![Growth of $100,000](results/comparison.png)

## The rules

1. The bot trades nine ETFs: SPY, EFA, EEM, VNQ, IEF, TLT, TIP, GLD and DBC. Together they cover US and foreign stocks, real estate, Treasuries, gold and commodities.
2. For lookbacks of 21, 63, 126 and 252 trading days, it checks whether each ETF's total return beat T-bills over the same window. The trend score is the share of the four checks that pass: 0, 0.25, 0.5, 0.75 or 1.
3. Each ETF starts at 1/9 of the portfolio. If the equal-weight mix has run above 10% yearly volatility over the last 126 days, all nine shrink by the same factor. Each weight is then multiplied by its trend score, and the rest stays in cash. Version 2 instead scales the weights up or down to 10% expected volatility, with at most 25% in any ETF and at most 100% invested. Version 3 uses the version 2 sizing on 13 ETFs; each new ETF joins once it has a year of prices.
4. It trades once a week. An ETF trades when its trend score has changed since its last trade, or when its weight has drifted more than 2 percentage points from target.

The full rules are in [SPEC.md](SPEC.md).

## Files

| File | What it does |
| --- | --- |
| `SPEC.md` | The strategy spec, committed before any backtest results |
| `strategy.py` | The trading rules, shared by the backtest and the live bot |
| `bot.py` | The live bot: one run per weekday, trades on the week's first trading day, logs every day (`python bot.py --simulate` runs it against a pretend $100,000 account) |
| `broker.py` | Talks to Alpaca's paper API and refuses any other address; also holds the pretend account the tests use |
| `setup/trend-bot.yml` | The GitHub Actions schedule, to be copied to `.github/workflows/` |
| `backtest.py` | Runs a spec version with its benchmarks and variants over 2007 to 2026 (`python backtest.py 2`) and writes `results/v1/` or `results/v2/` |
| `results/` | Summary, daily equity, every trade, every position from entry to exit, and the chart |
| `tests/` | Fail if any rule uses future prices, if the portfolio shorts or borrows, if the backtest's dollars don't reconcile, if version 3 holds an ETF before it has a year of prices, or if the live bot ever borrows, trades twice in a week, or trades on stale data |
| `tools/two_x_costs.py` | Measures what each 2x fund costs per year beyond twice its 1x ETF (used by version 4) |
| `trials.csv` | One row per backtest run, used for the deflated Sharpe ratio |

## Data

Prices come from Yahoo Finance through the `yfinance` library, adjusted for splits and dividends. The T-bill yield is Yahoo's `^IRX` series. If Yahoo fails, the live bot uses Alpaca's daily bars and FRED's 3-month T-bill rate instead. Trades go through Alpaca's paper trading API.

Paper trading only. Nothing here is investment advice.
