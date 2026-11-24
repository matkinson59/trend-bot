# Version 2 diagnostics

Descriptive only: version 2's own backtest (Feb 12, 2007 to Nov 20, 2026), nothing changed or chosen. Contributions use the weekly target weights, so they ignore drift within the week and trading costs.

## 1. Where the return came from

| ETF | Average weight | Share of days held | Return contribution a year | Above T-bills a year |
| --- | --- | --- | --- | --- |
| SPY | 12.4% | 92% | 1.15% | 0.94% |
| EFA | 10.3% | 87% | 0.39% | 0.21% |
| EEM | 9.2% | 85% | 0.68% | 0.51% |
| VNQ | 10.4% | 89% | 0.44% | 0.31% |
| IEF | 9.4% | 85% | 0.44% | 0.32% |
| TLT | 8.9% | 84% | 0.52% | 0.42% |
| TIP | 9.6% | 86% | 0.22% | 0.10% |
| GLD | 10.3% | 88% | 1.26% | 1.07% |
| DBC | 8.9% | 80% | 0.71% | 0.55% |
| Cash (T-bills) | 10.5% | | 0.15% | |

Sum of contributions: 5.96% a year (simple sum); the backtest's compound return: 4.94% a year, after 5 bp trading costs.

## 2. Limits and volatility

- The 100% limit on the total was binding on 57% of days, and at least one ETF sat at the 25% cap on 10% of days.
- Average invested: 90%. Realized volatility: 8.1% a year against the 10% target (lowest year 5.1% in 2017, highest 10.6% in 2007).

## 3. Where the trading came from

Turnover 925% of average equity a year (about 0.46% a year at 5 bp).

| Trades triggered by | Turnover a year | Cost a year at 5 bp |
| --- | --- | --- |
| resize (drift or volatility) | 190% | 0.09% |
| score change | 735% | 0.37% |

How often each lookback's signal flips (per ETF, per year, measured at weekly trade days):

| Lookback | Flips per ETF a year |
| --- | --- |
| 21 days | 11.4 |
| 63 days | 6.3 |
| 126 days | 4.2 |
| 252 days | 2.8 |
