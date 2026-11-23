# Trend Bot

A paper-trading bot that follows price trends across nine ETFs. It holds an ETF only while that ETF's trend beats the return on Treasury bills, keeps expected portfolio volatility at or below 10% a year, and never shorts or borrows. Orders go to an Alpaca paper account, which uses simulated money and real market prices.

Built by Miles Atkinson. The rules were written down in a spec before any backtest results existed, and every backtest run gets a row in `trials.csv`, so the results can be checked for overfitting.

## Status

| Dates | Work | State |
| --- | --- | --- |
| Nov 23 to 29, 2026 | Trading rules and data loader | Done |
| Nov 30 to Dec 6 | Backtest, benchmarks and validation | Done (passed) |
| Dec 7 to 13 | Live bot in dry-run mode, spec locked | Not started |
| Dec 14 | Live paper trading starts | Not started |

## Backtest

From February 2007 to November 2026, after a 5 basis point cost on every trade, the bot returned 3.8% a year with 5.1% volatility and a worst drawdown of 7.2%. Holding the same nine ETFs at the same risk with no trend signal returned 4.7% a year but fell 26.8% at its worst. The bot cleared all three bars set in the spec before the test: a higher Sharpe ratio than the always-invested version (0.46 against 0.38), a smaller maximum drawdown, and a deflated Sharpe ratio of 0.955 across six trials.

It earned less than the stock market (SPY returned 10.3% a year) with about a quarter of the volatility. Full results are in [results/summary.md](results/summary.md).

![Equity curves](results/equity.png)

## The rules

1. The bot trades nine ETFs: SPY, EFA, EEM, VNQ, IEF, TLT, TIP, GLD and DBC. Together they cover US and foreign stocks, real estate, Treasuries, gold and commodities.
2. For lookbacks of 21, 63, 126 and 252 trading days, it checks whether each ETF's total return beat T-bills over the same window. The trend score is the share of the four checks that pass: 0, 0.25, 0.5, 0.75 or 1.
3. Each ETF starts at 1/9 of the portfolio. If the equal-weight mix has run above 10% yearly volatility over the last 126 days, all nine shrink by the same factor. Each weight is then multiplied by its trend score, and the rest stays in cash.
4. It trades once a week. An ETF trades when its trend score has changed since its last trade, or when its weight has drifted more than 2 percentage points from target.

The full rules are in [SPEC.md](SPEC.md).

## Files

| File | What it does |
| --- | --- |
| `SPEC.md` | The strategy spec, committed before any backtest results |
| `strategy.py` | The trading rules, shared by the backtest and the live bot |
| `backtest.py` | Runs the strategy, benchmarks and variants over 2007 to 2026 and writes `results/` |
| `results/` | Summary, daily equity, every trade, every position from entry to exit, and the chart |
| `tests/` | Fail if any rule uses future prices, if the portfolio shorts or borrows, or if the backtest's dollars don't reconcile |
| `trials.csv` | One row per backtest run, used for the deflated Sharpe ratio |

## Data

Prices come from Yahoo Finance through the `yfinance` library, adjusted for splits and dividends. The T-bill yield is Yahoo's `^IRX` series. Trades go through Alpaca's paper trading API.

Paper trading only. Nothing here is investment advice.
