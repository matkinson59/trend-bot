# Trend Bot strategy spec

Version 1 was committed to this repository before any backtest returns were computed. Version 2, at the end of this file, changes position sizing only and was committed before its own backtest. Every rule in `strategy.py` and `backtest.py` comes from this file. Any later change gets a new version number and counts as a new trial in `trials.csv`.

## Summary and hypothesis

The bot owns nine ETFs from different asset classes and holds each one only while its trend beats the return on cash (T-bills). When a trend turns down, that slice of the portfolio moves to cash. Each ETF starts from an equal share, and the whole portfolio is scaled so expected volatility stays at or below 10% a year. It never shorts and never borrows.

The closest published precedent is Faber's equal-weight, long-or-cash timing model. Out of sample, from 2006 through 2016, it returned 8.5% a year against 7.7% for the S&P 500, with about half the volatility (7.4% against 14.7%) ([source](https://www.etf.com/node/93857.md)). That is the kind of result we hope to match: market-like returns with much smaller swings.

The claim being tested: averaging trend signals over several lookback periods earns better risk-adjusted returns and smaller drawdowns than holding the same ETFs at the same risk level. If the backtest does not show that, we report a failed test. We do not tweak the rules to make them pass.

## How the pieces fit

The rules live in one file, `strategy.py`. The backtest runs them over past prices and records the trades they would have made. The live bot runs the same rules each weekday on GitHub Actions and sends orders to an Alpaca paper account. Yahoo Finance supplies prices to both. Writing the rules once keeps the backtest and the live bot from disagreeing.

## Universe and data

| Ticker | Fund | Asset class |
| --- | --- | --- |
| SPY | SPDR S&P 500 ETF Trust | US large-cap stocks |
| EFA | iShares MSCI EAFE ETF | Developed-market stocks outside North America |
| EEM | iShares MSCI Emerging Markets ETF | Emerging-market stocks |
| VNQ | Vanguard Real Estate ETF | US real estate (REITs) |
| IEF | iShares 7-10 Year Treasury Bond ETF | Intermediate US Treasuries |
| TLT | iShares 20+ Year Treasury Bond ETF | Long US Treasuries |
| TIP | iShares TIPS Bond ETF | Inflation-protected Treasuries |
| GLD | SPDR Gold Shares | Gold |
| DBC | Invesco DB Commodity Index Tracking Fund | Broad commodities (futures) |

Backtest data comes from Yahoo Finance through the yfinance library, adjusted for splits and dividends ([source](https://softhints.com/understanding-yfinance-auto_adjust-true-what-changed-and-how-to-fix-it)). Alpaca can't be used for the backtest because its stock history starts on 2016-01-04 ([source](https://forum.alpaca.markets/t/wrong-historical-data-for-past-4-years/14802)). DBC is the youngest fund, launched Feb 3, 2006 ([source](https://stockanalysis.com/stocks/dbc/)), and the signals need 252 trading days of history, so the backtest starts in early 2007.

The live bot computes its signals from the same Yahoo adjusted closes, so live signals match the backtest. Alpaca supplies the account balance, positions, market clock and order handling. If Yahoo fails, the fallback is Alpaca daily bars with the adjustment parameter set to all ([source](https://docs.alpaca.markets/reference/stockbarsingle-1)).

## Signal rules

Every trading day, each ETF gets a trend score from 0 to 1, computed only from adjusted closes up to the previous day's close.

There are four lookbacks: 21, 63, 126 and 252 trading days, roughly 1, 3, 6 and 12 months. For each one, the ETF scores 1 if its total return over that window beat the T-bill return over the same window, and 0 if not. The trend score is the average of the four.

```
R(i, L)  = P(i, t-1) / P(i, t-1-L) - 1
Rf(L)    = product over days d = t-L ... t-1 of (1 + y(d) / 252), minus 1
s(i, L)  = 1 if R(i, L) > Rf(L), else 0
score(i) = average of s(i, L) over L in {21, 63, 126, 252}
```

Here y is the 13-week T-bill yield from Yahoo (ticker ^IRX) as a decimal. The score can only be 0, 0.25, 0.5, 0.75 or 1. Averaging four lookbacks avoids betting on one lucky setting ([source](https://files.advisorperspectives.com/articles/2019/05/06/global-equity-momentum-a-craftsmans-perspective)), and AQR's century study also blends 1-, 3- and 12-month signals ([source](https://www.etf.com/sections/index-investor-corner/swedroe-why-financial-trends-persist)).

During market hours Yahoo may include an unfinished row for the current day. The bot drops any row dated today before computing anything.

## Position sizing

1. Start every ETF at an equal weight of 1/9, about 11.1%.
2. Estimate the volatility of that fully invested mix from the last 126 days of returns. If it is above 10% a year, shrink all nine weights by the same factor k until it equals 10%. k is never above 1, so the total stays at or below 100% and the bot never borrows.
3. Multiply each weight by the ETF's trend score. Whatever is left sits in cash, which earns the T-bill rate in the backtest.

```
k    = min(1, 0.10 / sigma_p)
w(i) = k * (1/9) * score(i)
```

A first draft sized each ETF by 1 divided by its volatility. A risk-only check on 2006 to 2026 data, which computed weights and volatility but no returns, showed that mix put about 52% in the three Treasury funds and ran at a median 6.2% volatility fully invested, so it could not reach 10% without borrowing. Equal weights ran at 11.2%. Equal weights are also what Faber's long-or-cash model uses. The inverse-volatility version stays in the backtest as a logged variant. Volatility scaling is here to control risk; out-of-sample studies find it doesn't reliably add return ([source](https://econpapers.repec.org/RePEc:eee:jfinec:v:138:y:2020:i:1:p:95-117)).

## Rebalancing and orders

| Part | Rule |
| --- | --- |
| Run time | GitHub Actions starts the job at 15:17 UTC every weekday: 10:17 a.m. ET until daylight saving time begins on March 14, 2027, then 11:17 a.m. ET. The odd minute avoids the top of the hour, when GitHub says scheduled runs are most often delayed ([source](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)). |
| Market check | The job asks Alpaca's market clock whether the market is open. On holidays the run logs a skip and sends no orders. |
| Trade day | The first trading day of each week. Other days the bot logs scores and runs the risk checks only. |
| What trades | An ETF is traded when its trend score has changed since its last trade, or when its weight has drifted more than 2 percentage points from target. |
| Orders | Market orders, time in force day, sized in dollars with fractional shares ([source](https://docs.alpaca.markets/us/docs/fractional-trading)). Sells go first; buys follow once the sells fill. |

The backtest fills each trade at that day's opening price.

## Risk limits and safety checks

The code checks every rule below before any order goes out. If one fails, the run sends no orders and writes the reason to the log.

1. Target weights are never negative, and the bot never sells more shares than it holds.
2. Total target weight stays at or below 100% of equity, and cash after all orders stays at or above zero.
3. No single ETF goes above 25% of equity.
4. No single order is larger than 30% of equity.
5. Every ETF's latest price must be from the previous trading day. Stale or missing data means no trades that day.
6. The bot trades at most once a week, and checks Alpaca for orders already sent that week before trading.
7. A repository setting called TRADING_ENABLED is the off switch. When it is false, the bot computes and logs everything but sends no orders.
8. If equity falls 10% below its peak, the log flags it for review. The rules keep running unchanged unless the cause is a code bug.
9. The code only runs against Alpaca's paper address, https://paper-api.alpaca.markets ([source](https://docs.alpaca.markets/us/v1.1/docs/paper-trading)).
10. After trading, the bot compares actual positions with its targets and logs any ETF more than 1 percentage point off.
11. Miles commits the workflow file himself and keeps Actions email notifications on, since GitHub sends notifications for scheduled workflows to the user who created the workflow ([source](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs)).

## Costs and paper-trading gaps

The backtest charges 5 basis points (0.05%) on every dollar traded, to cover spread, slippage and fees. Results are also reported at 10 and 20 basis points.

Alpaca's paper trading does not simulate slippage from latency, queue position, price improvement, regulatory fees or dividends ([source](https://docs.alpaca.markets/docs/trading-on-alpaca/paper-trading/)).

| Gap | Effect | Handling |
| --- | --- | --- |
| No dividends | Paper equity understates real returns, most of all for the bond funds | The log's income column adds back dividends from Yahoo's records |
| No interest on cash (assumed) | Backtest cash earns T-bills; paper cash is assumed to earn nothing | The income column adds T-bill interest on the cash balance |
| No slippage or spread | Paper fills look better than real ones | The live log subtracts the same 5 bp cost |
| No regulatory fees | Small amounts missing on sells | Covered by the 5 bp charge |
| Fill timing | Backtest fills at the open; live orders fill around 10:17 a.m. | Every trade logs both prices |

## Validation plan

The backtest runs from the first date all nine ETFs have 252 days of history (early 2007) through November 2026, with results also shown for 2007 to 2012, 2013 to 2019 and 2020 to 2026.

| Benchmark | What it tells us |
| --- | --- |
| B1: same nine ETFs, same weights and 10% cap, always fully invested (score fixed at 1), traded weekly with the same rules | Whether the trend signal adds anything beyond owning assets that tend to rise ([source](https://econpapers.repec.org/RePEc:eee:jfinec:v:135:y:2020:i:3:p:774-794)) |
| B2: 60% SPY and 40% IEF, rebalanced on the first trading day of each month | Comparison with a standard balanced portfolio |
| B3: SPY bought and held | Comparison with the stock market |

B2 and B3 run at their own volatility, so they are compared by Sharpe ratio and drawdown, not raw return.

Each result reports annual return, annual volatility, Sharpe ratio on returns above T-bills, maximum drawdown, worst calendar year, yearly turnover and the average share of equity invested. It also reports expectancy: each stretch from opening a position in an ETF to fully exiting it counts as one trade, with win rate, average win, average loss and expectancy per trade, as a share of portfolio equity at entry.

The strategy passes only if it clears all three bars:

1. Its Sharpe ratio beats B1's.
2. Its maximum drawdown is smaller than B1's.
3. Its deflated Sharpe ratio is at least 0.95 ([source](https://papers.ssrn.com/abstract=2460551)), counting every variant in `trials.csv`.

Planned variants, each logged as a trial: lookbacks of 15, 50, 100 and 200 days; lookbacks of 30, 90, 180 and 300 days; a 63-day portfolio volatility window; a 0% hurdle in place of the T-bill hurdle; and inverse-volatility base weights. If the main version works and its neighbors fail, that is a warning sign of luck. Cost levels of 10 and 20 basis points are sensitivity checks, not trials.

The settings are fixed in advance and never fitted, so the sub-period results and the variants do the job a walk-forward test would.

Disclosures: the ETF list was picked in 2026 by people who know how these asset classes did, which can flatter any backtest. The risk-only check described under position sizing ran on Nov 22, 2026, before this spec was committed.

The lookahead check reruns the rules with data cut off at 50 random dates. Weights on each cutoff date must match the full-data run exactly.

## Live log

The bot writes one row per trading day to `log/daily.csv` and commits it to this repository.

| Column | What it holds |
| --- | --- |
| date | The trading day |
| status | Traded, no-trade day, holiday skip, or blocked by a safety check (with the reason) |
| equity_alpaca | Account equity as Alpaca reports it |
| equity_with_income | Equity plus the dividends and T-bill interest that paper trading doesn't pay |
| equity_after_costs | Equity minus the 5 bp charge on every trade so far |
| score and weight per ETF | Trend score, target weight and actual weight for each of the nine |
| orders | Symbol, side, dollar amount, fill price and that day's open price |

Each week Miles adds a dated note: what the bot did and why, and one prediction for the next week's trades written before they happen. Before go-live, the backtest gives the 5th to 95th percentile range of 60-trading-day returns, and live equity is judged against that band.

## Timeline

| Dates | Work |
| --- | --- |
| Nov 23 to 29, 2026 | Trading rules and data loader |
| Nov 30 to Dec 6 | Backtest, benchmarks and validation |
| Dec 7 to 13 | Live bot in dry-run mode |
| Dec 14 | Live paper trading starts |
| Dec 21 to Jan 2, 2027 | Bot runs unattended |
| Feb 12, 2027 | GitHub's 60-day activity limit, counted from go-live |
| Mar 15, 2027 | Project write-up complete |

## Open items

1. Alpaca's docs say a paper-only account gets only IEX data ([source](https://docs.alpaca.markets/docs/trading/paper-trading/)), while an Alpaca staff post says free accounts can pull full-market history older than 15 minutes. Signals use Yahoo, so this only affects the fallback.
2. Each ETF's fractionable flag in Alpaca's asset list. If one isn't fractionable, that order rounds to whole shares.
3. Whether the bot's own log commits count as repository activity for GitHub's 60-day rule. Backup: a manual commit before Feb 12, 2027.
4. yfinance is unofficial and has broken when Yahoo changed its site ([source](https://www.pypi.org/project/yfinance/0.2.10b2/)). The Alpaca fallback covers data back to 2016.
5. Backtest fills at the open estimate fills around 10:17 a.m. The live log measures the gap.
6. Whether Alpaca paper cash earns interest. Assumed not.

## Changes made before this spec was committed

| Change | Why |
| --- | --- |
| Base weights switched from 1/volatility to equal weights | The 1/volatility mix ran near 6% volatility fully invested and could not reach 10% without borrowing |
| Trend hurdle switched from 0% to the T-bill return | Matches the AQR excess-return definition; backtest cash now earns T-bills |
| Sharpe ratio measured on returns above T-bills | The standard definition |
| Unfinished current-day prices dropped | Stops a live signal from using a day that hasn't closed |
| Paper-only address check, post-trade position check and failure emails | Catches a wrong account, a partial fill or a crashed run |
| A trend-score change always trades, whatever the buffer | One score step moves a weight by 1/9 x 0.25 x k, at most 2.8% of equity and under 2% whenever k is below 0.72, so a 2-point buffer alone could skip real signal changes |

## Version 2: 10% volatility as a target

Committed on Nov 22, 2026, after the version 1 backtest and before any version 2 results.

Why: version 1 treats 10% volatility as a ceiling, and in its backtest it ran at 5.1% volatility, about half the risk budget. Version 2 tests whether using the full 10% earns more, at the cost of bigger drops. It is one change, made to match a stated risk preference, and it counts as trial 7.

What changes: position sizing only. The universe, signals, trade days, trading rule, costs and safety checks stay as in version 1.

1. Start each ETF at its trend score times 1/9.
2. Estimate the volatility of that trend-weighted mix from the last 126 days of returns.
3. Scale all weights by one factor so the mix's expected volatility equals 10% a year. The factor can be above or below 1.
4. Cap each ETF at 25% of equity. If the total is then above 100%, scale every weight down so the total is 100%.
5. If every score is 0, hold all cash.

```
u(i) = score(i) / 9
w(i) = min(0.25, u(i) * 0.10 / sigma_u)
if sum of w > 1: w(i) = w(i) / sum of w
```

Pass bars: the same three as version 1. B1 is unchanged, because always-invested equal weights scaled toward 10% under a 100% cap is the version 1 benchmark. The deflated Sharpe ratio counts all seven trials.

Decision rule, set now: if version 2 fails any bar, version 1 goes live. If it passes, Miles picks between the two, and the write-up reports both.
