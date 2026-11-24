"""
v2_diagnostics.py: describes where version 2's backtest return and trading
come from. Descriptive only: no rule is changed or chosen here, so nothing
in this file is a trial.

  1. return contribution of each ETF (weekly target weights times daily returns)
  2. how often the 100% and 25% limits bind, and realized vs target volatility
  3. where the turnover comes from: score changes vs resizing, and how often
     each lookback's signal flips

Usage:  python tools/v2_diagnostics.py      Writes results/v2_diagnostics.md.
"""
import os
import sys
from math import sqrt

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backtest as bt  # noqa: E402
import strategy as s  # noqa: E402

END = "2026-11-21"
opens, closes = s.load_bars(end=END)
tbill = s.load_tbill(closes.index, end=END)
weekly = bt.first_day_of_week(closes.index)
targets = s.target_weights_v2(closes, tbill)
scores = s.trend_scores(closes, tbill)
daily, trades, _ = bt.simulate(targets, scores, opens, closes, tbill, weekly, "2007-02-12")
idx = daily.index
years = len(idx) / s.TRADING_DAYS

# Weights held: the target row of the close before each weekly trade day, carried through the week
held = targets.reindex(closes.index).shift(1).where(weekly, axis=0).ffill().reindex(idx).fillna(0.0)
rets = closes.pct_change().reindex(idx).fillna(0.0)
rf = (tbill.shift(1) / s.TRADING_DAYS).reindex(idx).fillna(0.0)

out = ["# Version 2 diagnostics\n",
       "Descriptive only: version 2's own backtest (Feb 12, 2007 to Nov 20, 2026), nothing changed or chosen. "
       "Contributions use the weekly target weights, so they ignore drift within the week and trading costs.\n",
       "## 1. Where the return came from\n",
       "| ETF | Average weight | Share of days held | Return contribution a year | Above T-bills a year |",
       "| --- | --- | --- | --- | --- |"]
contrib = (held * rets).sum() / years
excess_contrib = (held.mul(1, axis=0) * rets.sub(rf, axis=0)).sum() / years
for t in s.TICKERS:
    out.append(f"| {t} | {held[t].mean():.1%} | {(held[t] > 1e-9).mean():.0%} | {contrib[t]:.2%} | {excess_contrib[t]:.2%} |")
cash_w = 1 - held.sum(axis=1)
cash_contrib = (cash_w * rf).sum() / years
out.append(f"| Cash (T-bills) | {cash_w.mean():.1%} | | {cash_contrib:.2%} | |")
total = contrib.sum() + cash_contrib
actual = (daily['equity'].iloc[-1] / bt.START_VALUE) ** (1 / years) - 1
out.append(f"\nSum of contributions: {total:.2%} a year (simple sum); the backtest's compound return: {actual:.2%} a year, "
           f"after {bt.COST_BPS} bp trading costs.\n")

# 2. Limits and volatility
total_w = targets.sum(axis=1).reindex(idx).ffill()
at_cap = (total_w >= 0.999).mean()
any_25 = (targets.reindex(idx).ffill() >= s.MAX_WEIGHT - 1e-9).any(axis=1).mean()
eq_r = daily["equity"].pct_change().dropna()
yearly_vol = eq_r.groupby(eq_r.index.year).std() * sqrt(s.TRADING_DAYS)
out += ["## 2. Limits and volatility\n",
        f"- The 100% limit on the total was binding on {at_cap:.0%} of days, and at least one ETF sat at the 25% cap on {any_25:.0%} of days.",
        f"- Average invested: {total_w.mean():.0%}. Realized volatility: {eq_r.std() * sqrt(s.TRADING_DAYS):.1%} a year against the 10% target "
        f"(lowest year {yearly_vol.min():.1%} in {yearly_vol.idxmin()}, highest {yearly_vol.max():.1%} in {yearly_vol.idxmax()}).\n"]

# 3. Turnover
tr = trades.copy()
tr["date"] = pd.to_datetime(tr["date"])
prev_close = {d: closes.index[closes.index.get_loc(d) - 1] for d in tr["date"].unique()}
last_score, kind = {}, []
for _, row in tr.sort_values("date").iterrows():
    sc = scores.loc[prev_close[row["date"]], row["ticker"]]
    changed = row["ticker"] not in last_score or last_score[row["ticker"]] != sc
    kind.append("score change" if changed else "resize (drift or volatility)")
    last_score[row["ticker"]] = sc
tr = tr.sort_values("date").assign(kind=kind)
mean_eq = daily["equity"].mean()
by_kind = tr.groupby("kind")["dollars"].sum() / mean_eq / years
out += ["## 3. Where the trading came from\n",
        f"Turnover {tr['dollars'].sum() / mean_eq / years:.0%} of average equity a year "
        f"(about {tr['dollars'].sum() / mean_eq / years * bt.COST_BPS / 1e4:.2%} a year at {bt.COST_BPS} bp).\n",
        "| Trades triggered by | Turnover a year | Cost a year at 5 bp |", "| --- | --- | --- |"]
for k, v in by_kind.items():
    out.append(f"| {k} | {v:.0%} | {v * bt.COST_BPS / 1e4:.2%} |")
out += ["", "How often each lookback's signal flips (per ETF, per year, measured at weekly trade days):\n",
        "| Lookback | Flips per ETF a year |", "| --- | --- |"]
wk = closes.index[weekly.values]
for L in s.LOOKBACKS:
    sig = (closes / closes.shift(L) - 1).gt(s.tbill_growth(tbill, L), axis=0).astype(int).loc[wk]
    sig = sig[sig.index >= pd.Timestamp("2007-02-12")]
    flips = (sig.diff().abs() > 0).sum().mean() / years
    out.append(f"| {L} days | {flips:.1f} |")
os.makedirs("results", exist_ok=True)
text = "\n".join(out) + "\n"
open("results/v2_diagnostics.md", "w").write(text)
print(text)
