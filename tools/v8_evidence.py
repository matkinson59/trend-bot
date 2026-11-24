"""
v8_evidence.py: descriptive checks on series that already exist, written for
the version 2 improvement sweep. Nothing here is a trial or changes a rule.

It looks at bot 2's B5 mix (version 2, trend-timed S&P 500 and sector
momentum, a third each) with no leverage and no floor, because the bot 2
diagnostics showed it earned more than version 2 at about the same
volatility. That result was seen before this script was written, so these
numbers describe it; they do not test it.

  1. how closely the three sleeves move together (daily returns above T-bills)
  2. the mix against version 2 in three sub-periods
  3. the mix's deflated Sharpe ratio, counting the 18 logged trials plus itself
  4. how much the mix holds in SPY and in cash

Usage:  python tools/v8_evidence.py
Writes results/v8_evidence.md.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backtest as bt  # noqa: E402
import bot2_backtest as b2  # noqa: E402
import strategy as s  # noqa: E402

base, _, tbill = b2.run_bases()
rows = {k: b2.base_rows(v["daily"], tbill, v["used"]) for k, v in base.items()}
dates = rows["B0"].index
rf = rows["B0"]["rf"]


def unlevered(key):
    data = [rows[key][c].values for c in ("excess", "invested", "rf", "spread")]
    R, _ = b2.overlay(data, 1.0, floor=False)
    return pd.Series(R, index=dates)


ret = {k: unlevered(k) for k in ("B0", "B2", "B3", "B5")}
excess = {k: ret[k] - rf for k in ret}
label = {"B0": "Version 2", "B2": "Trend-timed S&P 500", "B3": "Sector momentum",
         "B5": "Mix of the three, no leverage"}

lines = ["# Evidence for a version 8 built on the bot 2 mix\n",
         "Descriptive only. Every series here was already run for bot 2 (trials 12 to 16 and "
         "`results/bot2/diagnostics.md`), and the mix's better result was seen before this file was "
         "written, so nothing here is a test. Period: Feb 12, 2007 to Nov 20, 2026, counted from the "
         "$100,000 start, so version 2 shows 4.9% a year.\n",
         "## 1. How closely the sleeves move together\n",
         "Correlation of daily returns above T-bills.\n",
         "| | Version 2 | Trend-timed S&P 500 | Sector momentum |",
         "| --- | --- | --- | --- |"]
corr = pd.DataFrame({k: excess[k] for k in ("B0", "B2", "B3")}).corr()
for k in ("B0", "B2", "B3"):
    lines.append(f"| {label[k]} | " + " | ".join(f"{corr.loc[k, j]:.2f}" for j in ("B0", "B2", "B3")) + " |")

lines += ["", "## 2. Sub-periods\n",
          "| Period | Portfolio | Annual return | Volatility | Sharpe | Max drawdown |",
          "| --- | --- | --- | --- | --- | --- |"]
for lo, hi in (("2007", "2012"), ("2013", "2019"), ("2020", "2026")):
    for k in ("B0", "B5"):
        r = ret[k].loc[lo:hi]
        st = b2.stats(r, rf.loc[lo:hi])
        lines.append(f"| {lo}-{hi} | {label[k]} | {b2.pct(st['annual_return'])} | "
                     f"{b2.pct(st['annual_vol'])} | {st['sharpe']:.2f} | {b2.pct(st['max_drawdown'])} |")
lines.append("")
full = {k: b2.stats(ret[k], rf) for k in ("B0", "B5")}
for k in ("B0", "B5"):
    st = full[k]
    lines.append(f"Full period, {label[k]}: {b2.pct(st['annual_return'])} a year, "
                 f"{b2.pct(st['annual_vol'])} volatility, Sharpe {st['sharpe']:.2f}, "
                 f"worst drop {b2.pct(st['max_drawdown'])}.")

trials = pd.read_csv("trials.csv")
trial_daily = list(trials["sharpe_excess"] / np.sqrt(s.TRADING_DAYS))
own = excess["B5"].mean() / excess["B5"].std()
dsr, sr0 = bt.deflated_sharpe(excess["B5"], trial_daily + [own])
dsr_v2, _ = bt.deflated_sharpe(excess["B0"], trial_daily + [own])
lines += ["", "## 3. Deflated Sharpe ratio\n",
          f"Counting the {len(trials)} logged trials plus the mix itself ({len(trials) + 1}): "
          f"the mix {dsr:.3f}, version 2 {dsr_v2:.3f} (bar 0.95). The best of "
          f"{len(trials) + 1} unskilled trials would be expected to reach a Sharpe ratio of about "
          f"{sr0:.2f} by luck. The mix was picked after seeing it, which this count cannot fully "
          "correct for.\n"]

used = base["B5"]["used"].reindex(dates).ffill()
spy = used["SPY"]
invested = rows["B5"]["invested"]
lines += ["## 4. What the mix holds\n",
          f"- SPY weight: average {b2.pct(spy.mean())}, highest {b2.pct(spy.max())}, above 25% on "
          f"{b2.pct((spy > 0.25).mean(), 0)} of days.",
          f"- Invested share: average {b2.pct(invested.mean(), 0)}; below 80% on "
          f"{b2.pct((invested < 0.80).mean(), 0)} of days; at 99% or more on "
          f"{b2.pct((invested >= 0.99).mean(), 0)} of days.",
          f"- Realized volatility by calendar year: lowest "
          f"{b2.pct(ret['B5'].groupby(ret['B5'].index.year).std().min() * np.sqrt(252))}, highest "
          f"{b2.pct(ret['B5'].groupby(ret['B5'].index.year).std().max() * np.sqrt(252))}.",
          ""]

path = os.path.join(bt.RESULTS_DIR, "v8_evidence.md")
with open(path, "w") as fh:
    fh.write("\n".join(lines))
print("\n".join(lines))
