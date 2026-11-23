# Version 2 backtested the way the live bot trades it

Run on 2026-11-23 13:08 ET by `tools/as_live_backtest.py`. 2007-02-12 to 2026-11-20, starting at $100,000, 5 bp cost per dollar traded, fills at the open. The trading rule is version 2's; the steps add the live bot's details one at a time, using the live bot's own planning and order code. Not a trial: no rule changes and nothing is chosen from it.

| Step | What is added | Ending value | Annual return | Volatility | Sharpe | Max drawdown | Turnover per year |
| --- | --- | --- | --- | --- | --- | --- | --- |
| | Regular backtest (backtest.py) | $259,331 | 5.0% | 8.1% | 0.449 | -15.5% | |
| A | Engine check: original ETFs, cash at the T-bill rate, no buffer or limit | $259,320 | 5.0% | 8.1% | 0.449 | -15.5% | 1155% |
| B | + the cheaper funds the account holds | $266,221 | 5.1% | 8.1% | 0.468 | -15.5% | 1156% |
| C | + idle cash in BOXX (0.19% fee, 5 bp a trade) and a 0.5% buffer earning nothing | $259,656 | 5.0% | 8.1% | 0.453 | -15.7% | 1143% |
| D | + buys sized at the 1% limit price (the live bot) | $259,198 | 5.0% | 8.1% | 0.452 | -15.7% | 1142% |
| E | D, with BOXX trades at 1 bp (its quoted spread is about 1 cent on $117) instead of 5 bp | $263,799 | 5.1% | 8.1% | 0.463 | -15.5% | 1142% |

Turnover here counts BOXX trades too, so it is higher than the regular backtest's 925%.

BOXX check: the real fund grew 4.68% a year from 2022-12-28 to 2026-11-20; the synthetic BOXX used above grew 4.37% over the same days.

Not modeled: the live bot fills at about 10:17 a.m., not the open; a limit order that does not fill (a glitch price) leaves that position off target for a week; spreads on the twin funds may differ from the 5 bp assumed for every fund.
