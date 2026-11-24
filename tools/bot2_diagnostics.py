"""
bot2_diagnostics.py: explains bot 2's registered results. Run after
bot2_backtest.py. Nothing here is a trial or can change the winner: these
checks were chosen after seeing the results, and are reported as diagnostics.

  1. each candidate's base rule with no leverage and no floor
  2. each candidate at its chosen leverage without the floor rule
  3. the bootstrap with longer blocks (21 and 63 days), which keep more of
     the clustering of bad days that a 2.9-day block breaks up

Usage:  python tools/bot2_diagnostics.py
Writes results/bot2/diagnostics.md.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bot2_backtest as b2  # noqa: E402

base, spy_hold, tbill = b2.run_bases()
rows = {k: b2.base_rows(v["daily"], tbill, v["used"]) for k, v in base.items()}
dates = rows["B0"].index
rf = rows["B0"]["rf"]
chosen = pd.read_csv(os.path.join(b2.OUT, "leverage.csv"))
lev_table = pd.read_csv("trials.csv").set_index("trial_id")
L_of = {k: float(lev_table.loc[b2.TRIAL_IDS[k], "settings"].split()[1]) for k in b2.CANDIDATES}

lines = ["# Bot 2 diagnostics\n",
         "Chosen after seeing the registered results, so none of these is a trial and none can change "
         "the winner. They explain where the returns went.\n",
         "## 1. Base rules: no leverage, no floor\n",
         "| Candidate | Annual return | Volatility | Sharpe | Max drawdown | Average invested |",
         "| --- | --- | --- | --- | --- | --- |"]
for k in b2.CANDIDATES:
    data = [rows[k][c].values for c in ("excess", "invested", "rf", "spread")]
    R, _ = b2.overlay(data, 1.0, floor=False)
    st = b2.stats(pd.Series(R, index=dates), rf)
    lines.append(f"| {b2.LABEL[k]} | {b2.pct(st['annual_return'])} | {b2.pct(st['annual_vol'])} | "
                 f"{st['sharpe']:.2f} | {b2.pct(st['max_drawdown'])} | {b2.pct(rows[k]['invested'].mean(), 0)} |")

lines += ["", "## 2. Chosen leverage with and without the floor rule\n",
          "| Candidate | Leverage | With floor: return, max drawdown | Without floor: return, max drawdown |",
          "| --- | --- | --- | --- |"]
for k in b2.CANDIDATES:
    data = [rows[k][c].values for c in ("excess", "invested", "rf", "spread")]
    with_f = b2.stats(pd.Series(b2.overlay(data, L_of[k])[0], index=dates), rf)
    no_f = b2.stats(pd.Series(b2.overlay(data, L_of[k], floor=False)[0], index=dates), rf)
    lines.append(f"| {b2.LABEL[k]} | {L_of[k]:.1f} | {b2.pct(with_f['annual_return'])}, "
                 f"{b2.pct(with_f['max_drawdown'])} | {b2.pct(no_f['annual_return'])}, "
                 f"{b2.pct(no_f['max_drawdown'])} |")

lines += ["", "## 3. Share of ten-year paths falling 25% or more, by bootstrap block length\n",
          "Each cell: with the floor rule / without it, at the candidate's chosen leverage. "
          "10,000 paths, seed 0.\n",
          "| Candidate | Leverage | 2.9-day blocks (registered) | 21-day blocks | 63-day blocks |",
          "| --- | --- | --- | --- | --- |"]
block_reg = 2.9
for k in b2.CANDIDATES:
    data = [rows[k][c].values for c in ("excess", "invested", "rf", "spread")]
    cells = []
    for block in (block_reg, 21.0, 63.0):
        rng = np.random.default_rng(b2.SEED)
        idx = b2.stationary_indices(len(dates), b2.PATH_DAYS, b2.N_PATHS, block, rng)
        _, worst_f = b2.overlay(data, np.array([[L_of[k]]]), idx=idx)
        _, worst_n = b2.overlay(data, np.array([[L_of[k]]]), idx=idx, floor=False)
        cells.append(f"{b2.pct((worst_f <= b2.CAP_DRAWDOWN).mean())} / "
                     f"{b2.pct((worst_n <= b2.CAP_DRAWDOWN).mean())}")
    lines.append(f"| {b2.LABEL[k]} | {L_of[k]:.1f} | " + " | ".join(cells) + " |")

text = "\n".join(lines) + "\n"
with open(os.path.join(b2.OUT, "diagnostics.md"), "w") as fh:
    fh.write(text)
print(text)
