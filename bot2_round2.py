"""
bot2_round2.py: bot 2, round 2 (SPEC.md, "Bot 2, round 2: a 30% ceiling").
B6 and B7 are B5's base rule with two floor rules for a 30% ceiling.

Usage:  python bot2_round2.py   (after bot2_backtest.py)

Writes summary.md, equity.csv and leverage.csv to results/bot2_round2/ and
adds trials 17 and 18 to trials.csv.
"""
import os
from math import sqrt

import numpy as np
import pandas as pd

import backtest as bt
import bot2_backtest as b2
import strategy as s

CEILING = -0.30
OUT = os.path.join(bt.RESULTS_DIR, "bot2_round2")
TRIAL_IDS = {"B6": 17, "B7": 18}
LABEL = {"B6": "B6: B5 with the floor at 70% of peak",
         "B7": "B7: B5 with cuts from a 15% to a 30% drawdown"}


def floor_gz70(equity, peak):
    """Grossman and Zhou with the floor at 70% of the running peak."""
    return np.clip((1 - 0.70 * peak / equity) / 0.30, 0.0, 1.0)


def floor_half_cap(equity, peak):
    """Full exposure until a 15% drawdown, then straight down to 0 at 30%."""
    drawdown = equity / peak - 1
    return np.clip((0.30 + drawdown) / 0.15, 0.0, 1.0)


FLOORS = {"B6": floor_gz70, "B7": floor_half_cap}


def main():
    from arch.bootstrap import optimal_block_length   # research only; the live bot never needs arch

    os.makedirs(OUT, exist_ok=True)
    base, spy_hold, tbill = b2.run_bases()
    rows = {k: b2.base_rows(base[k]["daily"], tbill, base[k]["used"]) for k in ("B0", "B5")}
    dates = rows["B0"].index
    rf = rows["B0"]["rf"]

    # The same bootstrap paths as round 1
    block = float(optimal_block_length(rows["B0"]["excess"].values)["stationary"].iloc[0])
    idx = b2.stationary_indices(len(dates), b2.PATH_DAYS, b2.N_PATHS, block,
                                np.random.default_rng(b2.SEED))

    data = {k: [rows[k][c].values for c in ("excess", "invested", "rf", "spread")] for k in rows}
    v2_wealth, _ = b2.overlay(data["B0"], np.array([[1.0]]), idx=idx, floor=False)
    v2_median = float(np.median(v2_wealth ** (s.TRADING_DAYS / b2.PATH_DAYS) - 1))

    tables, chosen, results = {}, {}, {}
    for k, fn in FLOORS.items():
        tables[k] = b2.size_by_cap(rows["B5"], idx, floor=fn, cap=CEILING)
        allowed = tables[k][tables[k]["breach_share"] <= b2.CAP_SHARE]
        chosen[k] = None if allowed.empty else allowed.loc[allowed["median_return"].idxmax()]
        L = 1.0 if chosen[k] is None else float(chosen[k]["leverage"])
        R, mult = b2.overlay(data["B5"], L, floor=fn)
        results[k] = b2.stats(pd.Series(R, index=dates), rf)
        results[k]["leverage"] = L
        results[k]["avg_exposure"] = float(np.mean(mult * rows["B5"]["invested"].values))
        results[k]["time_cut"] = float(np.mean(mult < L - 1e-9))

    trials = pd.read_csv("trials.csv")
    earlier = trials[trials["trial_id"] <= 16]
    sharpes = list(earlier["sharpe_excess"] / sqrt(s.TRADING_DAYS))
    sharpes += [results[k]["excess"].mean() / results[k]["excess"].std() for k in FLOORS]
    for k in FLOORS:
        results[k]["dsr"], sr0 = bt.deflated_sharpe(results[k]["excess"], sharpes)

    top = max(FLOORS, key=lambda k: -1 if chosen[k] is None else chosen[k]["median_return"])
    checks = {
        "deflated Sharpe at least 0.95": results[top]["dsr"] >= b2.DSR_BAR,
        "median beats version 2": chosen[top] is not None and chosen[top]["median_return"] > v2_median,
        "worst drop smaller than 30%": results[top]["max_drawdown"] > CEILING,
    }
    passed = all(checks.values())

    # Reference only, not eligible: B5 with no floor, sized by the 1-in-10 rule
    ref_table = b2.size_by_cap(rows["B5"], idx, floor=False, cap=CEILING)
    ref_allowed = ref_table[ref_table["breach_share"] <= b2.CAP_SHARE]
    ref = None if ref_allowed.empty else ref_allowed.loc[ref_allowed["median_return"].idxmax()]
    ref_stats = None
    if ref is not None:
        ref_stats = b2.stats(pd.Series(b2.overlay(data["B5"], float(ref["leverage"]), floor=False)[0],
                                       index=dates), rf)
    v2 = b2.stats((base["B0"]["daily"]["equity"] / base["B0"]["daily"]["equity"].shift(1)
                   .fillna(bt.START_VALUE) - 1).reindex(dates), rf)

    # Files
    eq = pd.DataFrame({k: results[k]["equity"] for k in FLOORS})
    eq["version_2"] = v2["equity"]
    eq.round(2).to_csv(os.path.join(OUT, "equity.csv"), index_label="date")
    pd.concat([t.assign(candidate=k) for k, t in tables.items()] +
              [ref_table.assign(candidate="B5 no floor (reference)")])[
        ["candidate", "leverage", "breach_share", "median_return", "p10_return", "median_worst_drop"]
    ].round(5).to_csv(os.path.join(OUT, "leverage.csv"), index=False)

    keep = trials[~trials["trial_id"].isin(list(TRIAL_IDS.values()))]   # keep every other trial, including later ones
    new = [{"trial_id": TRIAL_IDS[k], "date_run": "2026-11-23", "spec_version": "bot2 round 2",
            "description": LABEL[k],
            "settings": f"leverage {results[k]['leverage']:.1f} by the 30% rule; exposure at most 2x",
            "sharpe_excess": round(results[k]["sharpe"], 3),
            "max_drawdown": round(results[k]["max_drawdown"], 4),
            "notes": "registered before testing; chosen after seeing round 1"} for k in FLOORS]
    pd.concat([keep, pd.DataFrame(new)]).sort_values("trial_id").to_csv("trials.csv", index=False)

    pct = b2.pct
    L = ["# Bot 2, round 2: a 30% ceiling\n",
         "Rules: SPEC.md, \"Bot 2, round 2\", committed before this run. Both candidates were chosen "
         "after seeing round 1. Same period, data and bootstrap paths as round 1 "
         f"(mean block {block:.1f} days, seed {b2.SEED}).\n",
         "| Candidate | Leverage | Median 10-year return | 10th percentile | Paths falling 30% | "
         "Backtest return | Volatility | Sharpe | Max drawdown | Worst year | Days with exposure cut | "
         "Deflated Sharpe |",
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for k in FLOORS:
        c, r = chosen[k], results[k]
        L.append(f"| {LABEL[k]} | {r['leverage']:.1f} | {pct(c['median_return'])} | {pct(c['p10_return'])} | "
                 f"{pct(c['breach_share'])} | {pct(r['annual_return'])} | {pct(r['annual_vol'])} | "
                 f"{r['sharpe']:.2f} | {pct(r['max_drawdown'])} | {pct(r['worst_year'])} "
                 f"({r['worst_year_label']}) | {pct(r['time_cut'], 0)} | {r['dsr']:.3f} |")
    L.append(f"| Version 2, no leverage | 1.0 | {pct(v2_median)} | | | {pct(v2['annual_return'])} | "
             f"{pct(v2['annual_vol'])} | {v2['sharpe']:.2f} | {pct(v2['max_drawdown'])} | "
             f"{pct(v2['worst_year'])} ({v2['worst_year_label']}) | | |")
    L.append("")
    L.append(f"Candidate by the registered rule: {LABEL[top]}.\n")
    for name, ok in checks.items():
        L.append(f"- {name}: {'yes' if ok else 'no'}")
    L.append("")
    L.append(f"**Result: {'passes' if passed else 'does not pass'}.** "
             + ("Bot 2 can be set up to trade it." if passed else "Under the registered rule, bot 2 is not launched.")
             + "\n")
    L.append(f"The deflated Sharpe ratio counts all 18 trials; the best of 18 unskilled trials would be expected "
             f"to reach an annual Sharpe ratio of about {sr0:.2f} by luck.\n")
    if ref is not None:
        L.append("Reference only, not eligible (no floor, so the ceiling is not firm): B5 with no floor, sized "
                 f"so no more than 1 path in 10 falls 30%, uses {ref['leverage']:.1f}x: median ten-year return "
                 f"{pct(ref['median_return'])}, {pct(ref['breach_share'])} of paths falling 30% or more; full "
                 f"backtest {pct(ref_stats['annual_return'])} a year, worst drop {pct(ref_stats['max_drawdown'])}.\n")
    with open(os.path.join(OUT, "summary.md"), "w") as fh:
        fh.write("\n".join(L))
    return results, chosen, passed


if __name__ == "__main__":
    main()
    print(open(os.path.join(OUT, "summary.md")).read())
