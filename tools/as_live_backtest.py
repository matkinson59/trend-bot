"""Version 2 backtested the way the live bot trades it.

The regular backtest (backtest.py) uses a simplified trader. This one drives
the live bot's own code, bot.plan_trades and bot.execute, against an
in-memory broker, one week at a time from Feb 2007 to Nov 2026. The trading
rule is version 2's and does not change; only how it is carried out does.
Each step adds one live detail so its effect can be seen on its own:

  A  engine check: original ETFs, cash earning the T-bill rate, no buffer,
     no price limit (should come close to the regular backtest)
  B  + the cheaper funds the account holds (VOO, IEFA, IEMG, SCHP, GLDM);
     before a fund existed, its original ETF's returns are used
  C  + idle cash in BOXX (T-bill rate minus its 0.19% fee, 5 bp per trade),
     and a 0.5% cash buffer that earns nothing, as on Alpaca paper
  D  + every buy sized at its 1% limit price (the live bot as it stands)

Fills are at the open, like the regular backtest; the live bot trades at
10:17 a.m., which daily prices cannot show. Dividends are in the adjusted
prices. Not a trial: no rule changes and nothing is chosen from it.

    python tools/as_live_backtest.py
"""
import os
import sys
from contextlib import contextmanager
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest as bt  # noqa: E402
import bot  # noqa: E402
import strategy as s  # noqa: E402
from broker import SimBroker  # noqa: E402

BOXX_FEE = 0.0019
OUT = "results/v2_live"


def chained(orig_o, orig_c, twin_o, twin_c):
    """Prices that follow the original ETF until the twin fund has two days
    of prices, then the twin. Returns (opens, closes) on the original's dates."""
    twin_o, twin_c = twin_o.reindex(orig_c.index), twin_c.reindex(orig_c.index)
    have = twin_c.notna() & twin_c.shift().notna()
    r = twin_c.pct_change().where(have, orig_c.pct_change()).fillna(0.0)
    closes = orig_c.iloc[0] * (1 + r).cumprod()
    gap = (twin_o / twin_c.shift()).where(have & twin_o.notna(), orig_o / orig_c.shift())
    opens = closes.shift() * gap
    opens.iloc[0] = orig_o.iloc[0]
    return opens, closes


def synthetic_boxx(index, tbill, fee):
    """A T-bill fund: grows each day by the previous day's T-bill rate minus its fee."""
    daily = (tbill.shift(1).fillna(tbill.iloc[0]) - fee) / s.TRADING_DAYS
    closes = 100 * (1 + daily).cumprod()
    return closes.shift().fillna(100.0), closes          # opens: no move overnight


@contextmanager
def settings(**values):
    old = {k: getattr(bot, k) for k in values}
    for k, v in values.items():
        setattr(bot, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(bot, k, v)


def run_live(targets, scores, opens, closes, boxx_o, boxx_c, trade_days, start,
             cost_bps=bt.COST_BPS, boxx_cost=True):
    """Weekly: plan with bot.plan_trades, trade with bot.execute at the open.
    Daily: value everything at the close. Returns daily equity and turnover."""
    idx = closes.loc[start:].index
    T = targets.reindex(closes.index).shift(1)
    SC = scores.reindex(closes.index).shift(1)
    held = {t: bot.FUND[t] for t in s.TICKERS}
    broker = SimBroker(bt.START_VALUE, {}, [d.date() for d in idx])
    last_scores, equity_path, traded_total = {}, [], 0.0

    def set_prices(row_etf, boxx):
        broker.prices = {held[t]: float(row_etf[t]) for t in s.TICKERS}
        broker.prices[s.CASH_ETF] = float(boxx)

    for d in idx:
        set_prices(opens.loc[d], boxx_o.loc[d])
        if trade_days.loc[d] and T.loc[d].notna().all():
            tgt, sc = T.loc[d], SC.loc[d]
            account, positions = broker.account(), broker.positions()
            equity = account["equity"]
            current = pd.Series({t: positions.get(held[t], {}).get("market_value", 0.0) / equity
                                 for t in s.TICKERS})
            dollars, chosen = bot.plan_trades(tgt, sc, current, last_scores, equity)
            if dollars:
                broker.history, broker.sent = [], []
                broker.now = pd.Timestamp(d).tz_localize("America/New_York")
                finished, _ = bot.execute(broker, dollars, tgt, positions, equity,
                                          bot.week_id(d.date()), timeout=0)
                for o in finished:
                    value = float(o.get("filled_qty") or 0) * float(o.get("filled_avg_price") or 0)
                    if o["symbol"] != s.CASH_ETF or boxx_cost:
                        broker.cash -= value * cost_bps / 1e4
                    traded_total += value
            for t in chosen:
                last_scores[t] = float(sc[t])
        set_prices(closes.loc[d], boxx_c.loc[d])
        equity_path.append(broker.account()["equity"])
    eq = pd.Series(equity_path, index=idx)
    return eq, traded_total


def stats(eq, tbill):
    r = eq.pct_change().dropna()
    rf = tbill.shift(1).reindex(r.index) / s.TRADING_DAYS
    ex = r - rf
    years = len(r) / s.TRADING_DAYS
    return {"ending": eq.iloc[-1], "annual_return": (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1,
            "vol": r.std() * np.sqrt(s.TRADING_DAYS), "sharpe": ex.mean() / ex.std() * np.sqrt(s.TRADING_DAYS),
            "max_dd": (eq / eq.cummax() - 1).min()}


def main():
    run_time = datetime.now(s.NEW_YORK)
    opens, closes = s.load_bars()
    tbill = s.load_tbill(closes.index)
    twins = list(s.LIVE_FUNDS.values())
    t_open, t_close = s.load_bars(tickers=twins, require=[])
    weekly = bt.first_day_of_week(closes.index)
    targets = s.target_weights_v2(closes, tbill)
    scores = s.trend_scores(closes, tbill)
    start = weekly[weekly & (weekly.index > targets.index[0])].index[0]

    h_open, h_close = opens.copy(), closes.copy()
    for orig, twin in s.LIVE_FUNDS.items():
        h_open[orig], h_close[orig] = chained(opens[orig], closes[orig], t_open[twin], t_close[twin])

    cash_like_tbill = synthetic_boxx(closes.index, tbill, 0.0)
    boxx = synthetic_boxx(closes.index, tbill, BOXX_FEE)
    at_price = dict(limit_for=lambda side, price: price)   # no limit: buys sized and filled at the open
    no_live = dict(CASH_BUFFER=0.0, MIN_BOXX_ORDER=0.0, **at_price)
    steps = [
        ("A", "Engine check: original ETFs, cash at the T-bill rate, no buffer or limit",
         (opens, closes, *cash_like_tbill), no_live, False),
        ("B", "+ the cheaper funds the account holds",
         (h_open, h_close, *cash_like_tbill), no_live, False),
        ("C", "+ idle cash in BOXX (0.19% fee, 5 bp a trade) and a 0.5% buffer earning nothing",
         (h_open, h_close, *boxx), at_price, True),
        ("D", "+ buys sized at the 1% limit price (the live bot)",
         (h_open, h_close, *boxx), {}, True),
    ]
    results = {}
    for key, label, (o, c, bo, bc), overrides, boxx_cost in steps:
        with settings(**overrides):
            eq, traded = run_live(targets, scores, o, c, bo, bc, weekly, start, boxx_cost=boxx_cost)
        results[key] = (label, eq, stats(eq, tbill), traded)
        print(key, {k: round(v, 4) for k, v in results[key][2].items()})

    reg = bt.simulate(targets, scores, opens, closes, tbill, weekly, start)[0]["equity"]
    reg_stats = stats(reg, tbill)

    real = s.load_bars(start="2022-12-01", tickers=[s.CASH_ETF], require=[])[1][s.CASH_ETF].dropna()
    syn = boxx[1].reindex(real.index)
    span = (real.index[0].date(), real.index[-1].date())
    yrs = (len(real) - 1) / s.TRADING_DAYS
    boxx_check = ((real.iloc[-1] / real.iloc[0]) ** (1 / yrs) - 1, (syn.iloc[-1] / syn.iloc[0]) ** (1 / yrs) - 1)

    os.makedirs(OUT, exist_ok=True)
    pd.DataFrame({"regular_backtest": reg, **{f"step_{k}": v[1] for k, v in results.items()}}).round(2) \
        .to_csv(f"{OUT}/equity.csv", index_label="date")
    pct = bt.pct
    years = len(reg) / s.TRADING_DAYS
    L = ["# Version 2 backtested the way the live bot trades it\n",
         f"Run on {run_time:%Y-%m-%d %H:%M} ET by `tools/as_live_backtest.py`. {start.date()} to {closes.index[-1].date()}, "
         f"starting at ${bt.START_VALUE:,.0f}, 5 bp cost per dollar traded, fills at the open. The trading rule is version 2's; "
         "the steps add the live bot's details one at a time, using the live bot's own planning and order code. "
         "Not a trial: no rule changes and nothing is chosen from it.\n",
         "| Step | What is added | Ending value | Annual return | Volatility | Sharpe | Max drawdown | Turnover per year |",
         "| --- | --- | --- | --- | --- | --- | --- | --- |",
         f"| | Regular backtest (backtest.py) | ${reg_stats['ending']:,.0f} | {pct(reg_stats['annual_return'])} | "
         f"{pct(reg_stats['vol'])} | {reg_stats['sharpe']:.3f} | {pct(reg_stats['max_dd'])} | |"]
    for key, (label, eq, st, traded) in results.items():
        L.append(f"| {key} | {label} | ${st['ending']:,.0f} | {pct(st['annual_return'])} | {pct(st['vol'])} | "
                 f"{st['sharpe']:.3f} | {pct(st['max_dd'])} | {pct(traded / eq.mean() / years, 0)} |")
    L.append(f"\nTurnover here counts BOXX trades too, so it is higher than the regular backtest's 925%.\n")
    L.append(f"BOXX check: the real fund grew {pct(boxx_check[0], 2)} a year from {span[0]} to {span[1]}; "
             f"the synthetic BOXX used above grew {pct(boxx_check[1], 2)} over the same days.\n")
    L.append("Not modeled: the live bot fills at about 10:17 a.m., not the open; a limit order that does not fill "
             "(a glitch price) leaves that position off target for a week; spreads on the twin funds may differ from "
             "the 5 bp assumed for every fund.\n")
    with open(f"{OUT}/summary.md", "w") as f:
        f.write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
