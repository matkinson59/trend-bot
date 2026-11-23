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

## Version 3: 13 ETFs

Committed on Nov 23, 2026, before any version 3 results.

Why: a review of trend-following research (sources at the end of this section). The findings behind the change:

1. Most of trend following's risk-adjusted return comes from holding many markets whose trends are independent of each other (Moskowitz, Ooi and Pedersen 2012; Hurst, Ooi and Pedersen 2017; Babu et al. 2020). Versions 1 and 2 hold nine ETFs, and three of them are US Treasury funds that move almost together (daily correlation of IEF and TLT: 0.91, 2008 to 2026).
2. The standard trend-following universe covers four groups: stock indices, government bonds, commodities and currencies. Versions 1 and 2 hold no currencies, and every commodity except gold sits inside one fund (DBC holds 14 commodities), so the bot can only hold or drop all of them at once.
3. A long-only bot can't short, so when stocks fall it can only step aside. The Japanese yen has tended to rise against the dollar when US stocks fall (Ranaldo and Söderlind 2010), and the dollar index fund UUP has had a negative daily correlation with SPY (-0.20, 2008 to 2026). Trend signals on these two give the bot something that can rise in a sell-off.

What changes: the universe only. Signals, the version 2 sizing rule, trade days, the trading rule, costs and safety checks stay the same.

| Added ETF | What it holds | First trading day in the data |
| --- | --- | --- |
| UUP | Long US dollar index futures: the dollar against the euro, yen, pound, Canadian dollar, Swedish krona and Swiss franc | Mar 1, 2007 |
| FXY | Japanese yen | Feb 13, 2007 |
| DBB | Industrial metals futures: aluminum, zinc and copper | Jan 5, 2007 |
| DBA | Agricultural commodity futures | Jan 5, 2007 |

How they were picked: one fund for each group the nine ETFs leave out (currencies) or lump together (commodities other than gold), choosing the oldest liquid fund in each case. Their start dates, volatility and correlations with the original nine were checked; their returns were not. Considered and left out: FXE (the euro moves as a near mirror of UUP, correlation -0.95), IWM (US small caps, 0.89 correlated with SPY), SLV (silver, 0.79 correlated with GLD) and LQD (corporate bonds, not part of the standard trend universe).

Late starters: an ETF takes part once it has 252 days of prices, which the 12-month check needs. Until then its weight is 0 and the sizing rule runs on the ETFs that have scores. The four new funds join in early 2008, so every version still runs on the same calendar from Feb 12, 2007.

```
active = ETFs with a trend score today
u(i) = score(i) for i in active        (equal base weights)
w(i) = min(0.25, u(i) * 0.10 / sigma_u)
if sum of w > 1: w(i) = w(i) / sum of w
```

With all ETFs present from the start, this gives exactly the version 2 weights. `tests/test_v3.py` checks that, along with the lookahead test, the weight limits, the one-year wait and the backtest's bookkeeping.

Benchmark: version 3's B1 holds the same 13 ETFs at all times (score 1 once an ETF has a year of prices), sized by the version 3 rule. It isolates the trend signal the same way B1 does for versions 1 and 2.

Pass bars: the same three, measured against version 3's B1. The deflated Sharpe ratio counts all eight trials.

Expectation, written before the run: a modest change. More independent markets should raise the Sharpe ratio, but without borrowing, extra diversification also lowers how much risk the bot can take when the 100% limit is binding, which pulls return down. Best guess: a Sharpe ratio between 0.45 and 0.60 and an annual return between 4.5% and 6.5%. Version 3 is not expected to beat SPY's or the 60/40 portfolio's return over 2007 to 2026.

Decision rule, set now: if version 3 fails any bar, it is dropped. If it passes, Miles picks among the versions that passed, and the write-up reports all of them. Version 3 is the last rule change at the 10% risk level under the long-or-cash rule. Any further version would change the risk level or that rule, and it would count as trial 9.

Result (Nov 23, 2026): failed. Sharpe 0.35 against B1's 0.31 and drawdown -15.2% against -25.2% passed, but the deflated Sharpe ratio was 0.875 over eight trials. Return was 4.0% a year, below the range expected above. Version 3 is dropped. The four new funds earned $3,700 between them, but with the 100% limit binding they took money from SPY, GLD and DBC, whose combined profit fell by $34,800 compared with version 2.

### Live-trading changes in version 3

These do not change the backtest. They make the paper account's results comparable with it.

1. Idle cash goes into BOXX, a fund that tracks 1 to 3 month T-bills and pays no distributions, so its return builds into its price. Alpaca's paper trading does not simulate dividends, and its paper trading page says nothing about paying interest on cash, so uninvested cash would otherwise earn nothing there. The backtest already credits the T-bill rate on cash. BOXX's expense ratio is about 0.19% a year.
2. Because paper trading leaves out dividends, the daily log adds them back from Yahoo's dividend-adjusted prices and reports the total return next to Alpaca's account value. Several of the ETFs pay distributions (the bond funds monthly, and some of the commodity and currency funds once a year).

### Ideas from the research that version 3 does not use

| Idea | Why not |
| --- | --- |
| Risk-parity weights (each ETF sized by 1/volatility) | Raises the Sharpe ratio, but without borrowing it shifts money into low-volatility bonds and lowers return (Asness, Frazzini and Pedersen 2012). Trial 6 matched this: Sharpe 0.55, return 3.8% a year. |
| Optimized (mean-variance) weights | Estimation error outweighs the gain: none of 14 optimization models beat equal weights consistently out of sample (DeMiguel, Garlappi and Uppal 2009). |
| Holding only the strongest-trending ETFs | Clare et al. found that adding this ranking to trend following raised returns along with volatility, without clearly better risk-adjusted results (working paper version, 2012). |
| More volatility timing | Improves the Sharpe ratio for stocks and credit only, with little effect for bonds, currencies and commodities (Harvey et al. 2018). Volatility-managed equity strategies did not systematically beat unmanaged ones out of sample (Cederburg et al. 2020). |
| Different lookbacks or signals | Trials 2 to 5 in this project land between Sharpe 0.38 and 0.47. Nothing points to a better signal. |
| Bands that skip small trades | Cost research favors trading less (Novy-Marx and Velikov 2016; Garleanu and Pedersen 2013). But 79% of version 2's turnover comes from trend-score changes, which are the signal itself, and skipping them means ignoring signals. The other 21% costs about 0.1% a year at 5 bp. |
| Borrowing on margin | Alpaca's margin documentation uses 6.50% a year in its example, and a broker comparison site lists 6.25%; the 13-week T-bill yield was 4.12% on Nov 20, 2026. Each borrowed dollar would cost about 2.1 to 2.4 points more than cash earns, while version 2 earned about 3.6 points over T-bills per dollar invested (Sharpe 0.45 at 8.1% volatility). After the usual discount for backtest optimism (Harvey and Liu 2015), that edge is gone. |
| 2x leveraged ETFs | The strongest return lever in the research: a 2x S&P 500 held only while the index is above its 200-day average had a Sharpe ratio of 0.51, against 0.30 for buy-and-hold, over 1928 to 2015, assuming a 1% yearly cost of leverage and no switching costs (Gayed and Bilello 2016, as summarized by CXO Advisory). Borrowing inside these funds is cheaper than margin, but several the bot would need started after the test begins (2x Treasuries in 2010, 2x gold in 2008), so the full period can't be tested without simulated prices. They would also raise the risk level. Miles's decision. |
| Inverse ETFs (for example SH, which rises when the S&P 500 falls) | Would let the bot gain from falling stocks without shorting, but they move away from the long-or-cash rule, and their daily reset makes them lose value in volatile markets (Avellaneda and Zhang 2010). Miles's decision. |
| Adding the period's winners (for example QQQ) | Choosing assets because they did well in the test period is hindsight, and it would make the backtest meaningless. |

### Sources for version 3

- Moskowitz, Ooi and Pedersen (2012), "Time Series Momentum," Journal of Financial Economics 104(2).
- Hurst, Ooi and Pedersen (2017), "A Century of Evidence on Trend-Following Investing," Journal of Portfolio Management.
- Babu, Levine, Ooi, Pedersen and Stamelos (2020), "Trends Everywhere," Journal of Investment Management 18(1).
- Clare, Seaton, Smith and Thomas, "The Trend Is Our Friend: Risk Parity, Momentum and Trend Following in Global Asset Allocation," University of York discussion paper 12/25 (2012); published in the Journal of Behavioral and Experimental Finance 9 (2016).
- Harvey, Hoyle, Korgaonkar, Rattray, Sargaison and Van Hemert (2018), "The Impact of Volatility Targeting," Journal of Portfolio Management 45(1).
- Cederburg, O'Doherty, Wang and Yan (2020), "On the Performance of Volatility-Managed Portfolios," Journal of Financial Economics 138.
- Asness, Frazzini and Pedersen (2012), "Leverage Aversion and Risk Parity," Financial Analysts Journal 68(1).
- DeMiguel, Garlappi and Uppal (2009), "Optimal Versus Naive Diversification: How Inefficient Is the 1/N Portfolio Strategy?" Review of Financial Studies 22(5).
- Novy-Marx and Velikov (2016), "A Taxonomy of Anomalies and Their Trading Costs," Review of Financial Studies 29(1).
- Garleanu and Pedersen (2013), "Dynamic Trading with Predictable Returns and Transaction Costs," Journal of Finance 68(6).
- Harvey and Liu (2015), "Backtesting," Journal of Portfolio Management.
- Ranaldo and Söderlind (2010), "Safe Haven Currencies," Review of Finance 14(3).
- Avellaneda and Zhang (2010), "Path-Dependence of Leveraged ETF Returns."
- Gayed and Bilello (2016), "Leverage for the Long Run."
- Alpaca documentation: paper trading, and margin and short selling.

## Version 4: version 3 at 1.5 times the size

Committed on Nov 23, 2026, before any version 3 or version 4 results. It counts as trial 9.

Why: Miles asked for a higher-risk version after the version 3 research found that the one large return lever left is risk, which needs leverage. Return above T-bills equals the Sharpe ratio times volatility, so 1.5 times the risk at the same Sharpe ratio gives about 1.5 times the excess return, minus the cost of the borrowing.

Why 1.5: the Kelly rule gives the leverage that maximizes long-run growth as the Sharpe ratio divided by volatility. With version 2's figures (Sharpe 0.45, volatility 8.1%) and the Sharpe ratio cut in half for backtest optimism (Harvey and Liu 2015), that is 0.225 / 0.081, about 2.8 times. Estimation error makes full Kelly risky, so practitioners bet a fraction of it (MacLean, Thorp and Ziemba 2010). Half of 2.8 is 1.4, rounded to 1.5.

```
Kelly leverage = (expected return - T-bill rate) / variance = Sharpe / volatility
```

What changes:

1. Exposures: version 3's rule with every limit times 1.5 (a 15% volatility target, 37.5% per ETF, 150% total exposure). Every exposure is exactly 1.5 times version 3's.
2. Holdings: while total exposure is at most 100%, the bot holds 1x ETFs. Above 100%, it moves the same share of each ETF that has a 2x fund into that fund, just enough that holdings add up to 100% of equity. If moving all of them is not enough, every exposure is scaled down by one factor until it fits. The account never borrows; the borrowing happens inside the 2x funds.
3. TIP, DBC, UUP, FXY, DBB and DBA have no 2x fund here and are always held as 1x ETFs.
4. A 2x fund trades on its 1x ETF's trend score, under the same trading rule, trade days and 5 bp cost.

| 1x ETF | 2x fund | Measured extra cost a year | 2x fund's first day | Average dollars traded a day (60 days to Nov 20, 2026) |
| --- | --- | --- | --- | --- |
| SPY | SSO | 1.55% | Jun 21, 2006 | $290M |
| EFA | EFO | 2.03% | Jun 4, 2009 | $0.4M |
| EEM | EET | 1.65% | Jun 4, 2009 | $0.8M |
| VNQ | URE | 2.60% | Feb 2, 2007 | $0.1M |
| IEF | UST | 0.64% | Feb 2, 2010 | $0.5M |
| TLT | UBT | 0.62% | Jan 21, 2010 | $1.4M |
| GLD | UGL | 2.51% | Dec 3, 2008 | $132M |

"Measured extra cost" is how far each fund fell short of "2 times its 1x ETF's daily return, minus T-bill interest on the borrowed half", averaged over its full history (`tools/two_x_costs.py`). It covers the fund's fee, its borrowing spread and tracking slippage.

Backtest prices for the 2x funds are simulated for every date, including years before the real funds existed: each day the fund moves twice its 1x ETF's move, minus that day's T-bill rate and its measured cost, both divided by 252. Its open moves twice the 1x ETF's overnight move. Checked against the real funds over their histories, simulated growth matched real growth to within 0.01 points a year, with daily correlations of 0.97 to 0.997. EFO is the exception at 0.84, because it trades about $0.4M a day and its prices are often stale.

```
r_2x(t) = 2 * r_1x(t) - (tbill(t-1) + cost) / 252
```

Benchmark: version 4's B1 holds the same 13 ETFs at all times, at 1.5 times through the same 2x funds. Pass bars: the same three, against that B1. The deflated Sharpe ratio counts all nine trials. Version 3's is reported over eight trials, as its section says, and over nine.

Expectation, written before the run: leverage does not raise the Sharpe ratio, and the 2x funds' costs lower it. Expect a Sharpe ratio 0.02 to 0.10 below version 3's, volatility 1.3 to 1.5 times version 3's, a return above T-bills 1.3 to 1.5 times version 3's minus about 0.5 to 1 point a year of fund costs, and a maximum drawdown about 1.5 times version 3's. The deflated Sharpe bar may fail: a lower Sharpe ratio across more trials is harder to tell apart from luck.

Live trading: five of the seven 2x funds trade under $1.5M a day, so real orders would pay wider spreads than the 5 bp assumed; a $100,000 paper account would not notice. The funds reset to 2x every day, which the simulated prices already include.

Decision rule, set now: if version 4 fails any bar, it is dropped. If it passes, Miles picks among the versions that passed, and the write-up reports all of them. Any further version counts as trial 10.

Result (Nov 23, 2026): failed. Sharpe 0.31 against B1's 0.29 and drawdown -20.9% against -33.8% passed, but the deflated Sharpe ratio was 0.810 over nine trials. Return was 4.4% a year at 10.8% volatility, with an average exposure of 136%. The expectation above held relative to version 3. Version 4 is dropped. Counting all nine trials, version 1's deflated Sharpe ratio is 0.936 and version 2's is 0.932.

Sources for version 4:

- MacLean, Thorp and Ziemba (2010), "Long-term capital growth: the good and bad properties of the Kelly and fractional Kelly capital growth criteria," Quantitative Finance 10(7).
- Harvey and Liu (2015), "Backtesting," Journal of Portfolio Management.
- Gayed and Bilello (2016), "Leverage for the Long Run."
- Fund prices: Yahoo Finance through `yfinance`, to Nov 20, 2026.

## Locked: version 2

Locked on Nov 23, 2026. Version 2 trades the live paper account and is the only version the live bot runs or logs. No rule changes until the live run ends on Mar 12, 2027. Version 5 (trial 10) was tested the same day under a rule set before its run and did not replace version 2; any later version counts as trial 11.

Counting all nine trials, version 2's deflated Sharpe ratio is 0.932, just under the 0.95 bar. It passed when it was tested (0.952 over seven trials) and slipped as later trials were added. The write-up reports both numbers.

### Live implementation

None of these change the rules or the backtest. Each makes the paper account's return higher or closer to the backtest.

1. Signals and sizing come from the nine original ETFs' prices, exactly as in the backtest.
2. For five of them, the account holds a cheaper fund that tracks the same assets. Over their shared history, each beat the original by roughly its fee difference.
3. Idle cash goes into BOXX, a T-bill fund that pays no distributions, so its return shows in its price. Alpaca's paper trading does not credit dividends and says nothing about interest on cash.
4. The daily log adds dividends back from Yahoo's adjusted prices, since paper trading leaves them out.
5. Orders stay as version 1 set them: day market orders at 10:17 a.m., sized in dollars with fractional shares, sells first. Opening-auction orders were considered and dropped. Alpaca takes them only in whole shares and only until 9:28 a.m. ET, and a sell that has not filled yet does not free buying power, so buys placed at the same auction could borrow.

| Signal ETF | Fund held | Expense ratio (signal ETF / fund held) | Index | Return gap per year, shared history | Weekly correlation |
| --- | --- | --- | --- | --- | --- |
| SPY | VOO | 0.0945% / 0.03% | S&P 500 for both | +0.06% (since Sep 2010) | 1.000 |
| EFA | IEFA | 0.32% / 0.07% | MSCI EAFE; IEFA adds small caps | +0.24% (since Oct 2012) | 0.998 |
| EEM | IEMG | 0.72% / 0.09% | MSCI Emerging Markets; IEMG adds small caps | +0.63% (since Oct 2012) | 0.998 |
| TIP | SCHP | 0.18% / 0.03% | US TIPS (ICE for TIP, Bloomberg for SCHP) | +0.07% (since Aug 2010) | 0.995 |
| GLD | GLDM | 0.40% / 0.10% | Gold bullion for both | +0.30% (since Jun 2018) | 0.999 |

DBC stays as it is: its lower-fee sibling PDBC trailed it by 0.15% a year since November 2014. VNQ, IEF and TLT already cost 0.15% or less.

Expected effect, using version 2's average weights (EEM 9.2%, EFA 10.3%, GLD 10.3%, SPY 12.5%, TIP 9.6%, cash 10.3%): about 0.13 points a year from the cheaper funds, and about 0.4 points a year from BOXX at the Nov 20, 2026 T-bill rate of 4.12% less its 0.19% fee. That is about 0.5 points a year against holding the original funds with idle cash, or roughly 0.13% over the 13-week live run.

### Live bot (bot.py)

These details settle how the version 1 order and safety rules run in code. None changes a trading rule.

| Part | How it works |
| --- | --- |
| Order of trades | Three steps. (1) Sells, plus enough BOXX to pay for the buys. (2) Buys, scaled down to the cash actually in the account after the sells. (3) Any cash left above the buffer goes into BOXX. Each step waits up to 3 minutes; an order still open then is cancelled and the log flags it. |
| Cash buffer | 0.5% of equity stays as plain cash, so fills a little above the last price cannot push cash below zero. |
| Order limits | Orders under $1 are skipped, and BOXX orders under $50. The 30%-of-equity limit applies to the nine ETFs' orders, not to BOXX, which can hold all the cash. |
| Whole shares | If Alpaca marks a fund as not fractionable, its order rounds down to whole shares. |
| Missed trade day | If the week's first trading day is missed (an outage or a late run), the next run that week trades and the log marks it as a catch-up. |
| Repeat runs | A second run on the same day replaces that day's log row and does not count income twice. Each order's ID is `tb-<week>-<symbol>-<side>`, and Alpaca rejects a repeated ID, so a week's orders cannot go out twice. |
| Data | Yahoo first. If Yahoo fails, adjusted daily bars from Alpaca's market data and the 3-month T-bill rate from FRED (series DTB3). The log records which source was used. |
| Dry run | With TRADING_ENABLED set to false, the bot logs the orders it would send and marks the week as evaluated, but keeps the old scores, so the first live run trades from scratch. The live record (start date, starting equity, peak, income, costs, 60-day return) begins with the first run that has trading enabled; dry runs before it don't count. |
| Failure | A run that is blocked by a safety check or hits an error exits with code 1, so GitHub marks it failed and emails Miles. A run that crashes still writes the error to `log/errors.csv`, and to `log/daily.csv` if that day has no row yet. |
| Files | `log/daily.csv` (one row per day), `log/orders.csv` (every order with its fill), `log/income.csv` (dividends and T-bill interest added back), `log/errors.csv` (runs that crashed), `log/state.json` (last scores, week evaluated, peak equity). |

### Checked and not used

| Idea | Finding |
| --- | --- |
| Trading on a different weekday | Version 2 run with each weekday as the trade day returned 4.6% (Thursday) to 5.3% (Friday) a year; Monday, the rule, gave 5.0%. The result does not depend on a lucky day. Switching to the best day would be fitting the backtest. Splitting the account into five parts that trade on different days ("tranching") reduces this luck without raising expected return (Hoffstein, Faber and Braun 2020), and it would be a rule change. |
| Holding the bot as 20% of a larger portfolio | 80% in 60/40 plus 20% in version 2 returned 7.3% a year against 7.8% for 60/40 alone, with a -28.0% worst drop instead of -32.5% and a Sharpe ratio of 0.61 against 0.60. In this period the bot cut drawdowns but did not raise the larger portfolio's return. |
| Adding a carry signal | Carry predicts returns in stocks, bonds, commodities and currencies and is different information from trend (Koijen, Moskowitz, Pedersen and Vrugt 2018). It is the best-supported next signal, but it would be a new trial and needs yield data the backtest does not have. Possible future work after the live run. |
| PDBC in place of DBC | Lower fee, but it trailed DBC by 0.15% a year over its history. |

Sources for this section: fund expense ratios from the issuers' pages and fund comparisons as of 2026; Alpaca documentation on paper trading, fractional trading and order types; Cboe, "Five Best Practices for Trading ETFs"; Hoffstein, Faber and Braun (2020), "Rebalance Timing Luck: The Dumb (Timing) Luck of Smart Beta"; Koijen, Moskowitz, Pedersen and Vrugt (2018), "Carry," Journal of Financial Economics 127(2). Return gaps and correlations computed from Yahoo Finance prices to Nov 20, 2026.

## Version 5: weak scores keep positions small

Committed on Nov 23, 2026, before any version 5 results. It counts as trial 10.

Why: Miles asked how trend strategies limit damage in markets like the one in November 2026, when most trend scores fell to 0.25 or 0. Version 2 scales the weighted mix to 10% volatility. When every score is low, the mix is small, so the scale-up is large: on Nov 20, 2026 it was 9.7 times, which put the bot 100% invested on five ETFs that passed only one of four trend tests. Standard time-series momentum sizes each position as its signal times a volatility-based size (Moskowitz, Ooi and Pedersen 2012), so a weak signal stays a small position. Research on turning points finds that when a 12-month trend is up but the 1-month trend is down (a "Correction"), average returns fall and volatility rises, and blends of fast and slow signals did best by holding less in those states (Goulding, Harvey and Mazzoleni 2023). The four-lookback score already blends fast and slow signals; version 2's scale-up undoes what the score says.

What changes from version 2 (one change only):

1. The 10% volatility scale is worked out for the mix with every ETF at 1/9, as if every trend were fully on, using the same 126-day covariance.
2. Each ETF's weight is its trend score times 1/9 times that scale. The 25% cap per ETF and the 100% limit on the total stay.
3. Signals, lookbacks, trading rule, trade days, costs and benchmarks are unchanged.

```
k(t)   = 10% / volatility of the all-nine mix at 1/9 each (126-day covariance)
w_i(t) = score_i(t) x (1/9) x k(t), capped at 25%; if the total is above 100%, scale down to 100%
```

When every score is 1, version 5 holds exactly what version 2 holds. When scores are low, it holds less: on Nov 20, 2026 it would hold 3.1% in each of SPY, EFA, VNQ, IEF and DBC (15.3% in total) instead of 20% each.

Benchmark and bars: the same B1 and the same three bars as version 2. The deflated Sharpe ratio counts all ten trials.

Expectation, written before the run: a smaller maximum drawdown than version 2's -15.5%, somewhere between -7% and -12%; an annual return between version 1's 3.8% and version 2's 5.0%; a Sharpe ratio within 0.05 of version 2's 0.45; and a lower average share invested than version 2's 89%. The deflated Sharpe bar will probably fail, as version 2's now does over nine trials.

Decision rule, set now: version 5 replaces version 2 for the live run starting Nov 30, 2026 only if it has both a higher Sharpe ratio and a smaller maximum drawdown than version 2 in this backtest. Otherwise version 2 stays locked and version 5 is reported as a tested alternative. Whichever goes live, the write-up reports both.

Sources for version 5:

- Moskowitz, Ooi and Pedersen (2012), "Time Series Momentum," Journal of Financial Economics 104(2).
- Goulding, Harvey and Mazzoleni (2023), "Momentum Turning Points," Journal of Financial Economics 149(3).
- Harvey, Hoyle, Korgaonkar, Rattray, Sargaison and Van Hemert (2018), "The Impact of Volatility Targeting," Journal of Portfolio Management 45(1).

Result (Nov 23, 2026): failed the deflated Sharpe bar (0.927 over ten trials) and did not replace version 2. Sharpe 0.44 against B1's 0.38 and drawdown -9.5% against -26.8% passed. Against version 2: annual return 4.1% against 5.0%, volatility 5.9% against 8.1%, Sharpe ratio 0.441 against 0.449, maximum drawdown -9.5% against -15.5%, average invested 66% against 89%. Every part of the expectation above held. Version 5 had the smaller drawdown but not the higher Sharpe ratio, so under the decision rule version 2 stays locked for the live run starting Nov 30, 2026.

Where version 5 differed most was slow declines: in 2022 it lost 2.8% against version 2's 8.8%, and from Oct 6 to Nov 20, 2026 it lost 3.7% against 9.2%. In the fast COVID crash (Feb 19 to Mar 23, 2020) the two were close (-7.5% and -7.9%). Over the full period, version 2 held at 73% of its size with the rest in T-bills matches version 5's volatility and returns 4.1% a year at a Sharpe ratio of 0.45 with a -11.3% worst drop, so version 5 mostly takes less risk rather than earning more per unit of risk. Counting all ten trials, version 1's deflated Sharpe ratio is 0.938 and version 2's is 0.933.
