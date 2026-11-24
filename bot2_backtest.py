"""
bot2_backtest.py: backtests bot 2's registered candidates B0, B2, B3, B4 and
B5 (SPEC.md, "Bot 2: registered candidates"), sizes each one by the cap rule
and picks a winner by the registered rules.

Usage:  python bot2_backtest.py

Writes summary.md, equity.csv, leverage.csv and equity.png to results/bot2/,
and adds trials 12 to 16 to trials.csv.
"""
import os
from itertools import combinations
from math import sqrt

import numpy as np
import pandas as pd
from arch.bootstrap import SPA, optimal_block_length

import backtest as bt
import strategy as s

START = "2007-02-12"
END = "2026-11-21"               # yfinance end is exclusive: the last close is Nov 20, 2026
SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY", "XLRE", "XLC"]
SECTORS_CORE = SECTORS[:9]       # XLRE (2015) and XLC (2018) join once they have 252 days
SECTOR_HOLD = 3
MOM_FAR, MOM_NEAR = 252, 21      # momentum: return from 252 to 21 trading days ago
SMA_DAYS = 200
MARGIN_SPREAD = 0.0238           # Alpaca's 6.50% margin rate less the 4.12% T-bill
MAX_EXPOSURE = 2.0               # Alpaca's overnight limit, as a multiple of equity
FLOOR = 0.75                     # floor at 75% of the running peak
CAP_DRAWDOWN = -0.25
CAP_SHARE = 0.10                 # at most 1 path in 10 may fall 25% or more
L_GRID = np.round(np.arange(1.0, 3.0001, 0.1), 1)
N_PATHS = 10_000
PATH_DAYS = 2520                 # ten years
SEED = 0
CHANGE_COST = bt.COST_BPS / 1e4
REVERSAL_WEIGHTS = np.array([4.0, 3.0, 2.0, 1.0])   # Baltussen, van Bekkum and Da
DSR_BAR = 0.95
OUT = os.path.join(bt.RESULTS_DIR, "bot2")
CANDIDATES = ["B0", "B2", "B3", "B4", "B5"]
TRIAL_IDS = {"B0": 12, "B2": 13, "B3": 14, "B4": 15, "B5": 16}
LABEL = {"B0": "B0: version 2, levered", "B2": "B2: trend-timed S&P 500",
         "B3": "B3: sector momentum", "B4": "B4: B0 plus index reversal",
         "B5": "B5: equal mix of B0, B2 and B3"}


# ---------------------------------------------------------------------------
# Base rules (close rows: row d uses closes up to and including d)
# ---------------------------------------------------------------------------
def b2_targets(closes):
    """Trend-timed S&P 500: SPY at min(100%, 10% / its 126-day volatility)
    while it closes above its 200-day average, otherwise cash."""
    spy = closes["SPY"]
    sma = spy.rolling(SMA_DAYS).mean()
    vol = spy.pct_change().rolling(s.VOL_WINDOW).std() * sqrt(s.TRADING_DAYS)
    on = spy > sma
    weight = (s.TARGET_VOL / vol).clip(upper=1.0).where(on, 0.0)
    valid = sma.notna() & vol.notna()
    targets = pd.DataFrame({"SPY": weight})[valid]
    scores = pd.DataFrame({"SPY": on.astype(float)})[valid]
    return targets, scores


def b3_targets(closes, tbill):
    """Sector momentum: the three sectors with the highest 12-1 month return
    that beat T-bills over the same window, each at k/3 (k <= 1 scales the
    held sectors at 1/3 each to 10% volatility or less)."""
    px = closes[SECTORS]
    mom = px.shift(MOM_NEAR) / px.shift(MOM_FAR) - 1
    hurdle = (1 + s.tbill_growth(tbill, MOM_FAR)) / (1 + s.tbill_growth(tbill, MOM_NEAR)) - 1
    rets = px.pct_change().values
    M, H = mom.values, hurdle.reindex(px.index).values
    core = [SECTORS.index(t) for t in SECTORS_CORE]
    weights = np.full(M.shape, np.nan)
    held = np.full(M.shape, np.nan)
    for t in range(s.VOL_WINDOW, len(px)):
        if np.isnan(H[t]) or np.isnan(M[t, core]).any():
            continue
        m = M[t]
        eligible = [j for j in range(len(SECTORS)) if not np.isnan(m[j]) and m[j] > H[t]]
        top = sorted(eligible, key=lambda j: m[j], reverse=True)[:SECTOR_HOLD]
        w = np.zeros(len(SECTORS))
        if top:
            window = rets[t - s.VOL_WINDOW + 1: t + 1][:, top]
            if np.isnan(window).any():
                continue
            mix = np.full(len(top), 1.0 / SECTOR_HOLD)
            cov = np.cov(window, rowvar=False, ddof=1).reshape(len(top), len(top)) * s.TRADING_DAYS
            k = min(1.0, s.TARGET_VOL / sqrt(mix @ cov @ mix))
            w[top] = k / SECTOR_HOLD
        weights[t] = w
        held[t] = 0.0
        held[t, top] = 1.0
    targets = pd.DataFrame(weights, index=px.index, columns=SECTORS).dropna()
    scores = pd.DataFrame(held, index=px.index, columns=SECTORS).dropna()
    return targets, scores


def reversal_multiplier(spy_close):
    """m = 1 - z, limited to [0, 2], with z = (4r1 + 3r2 + 2r3 + r4) / (sigma sqrt 30)
    from closes up to and including each date."""
    r = spy_close.pct_change()
    signal = sum(w * r.shift(k) for k, w in enumerate(REVERSAL_WEIGHTS))
    sigma = r.rolling(s.VOL_WINDOW).std()
    z = signal / (sigma * sqrt((REVERSAL_WEIGHTS ** 2).sum()))
    return (1 - z).clip(lower=0.0, upper=2.0)


# ---------------------------------------------------------------------------
# "Used" rows: row i is what the portfolio holds from the open of day i,
# built only from closes up to day i - 1
# ---------------------------------------------------------------------------
def held_from(rows, trade_days, index):
    """Rows held from each trade day's open until the next one: on a trade day
    i the row is the close row of day i - 1, carried forward until the next."""
    prev = rows.reindex(index).shift(1)
    return prev.where(trade_days.reindex(index).fillna(False).astype(bool), axis=0).ffill()


def b4_used(b0_rows, b0_scores, spy_close, weekly):
    idx = spy_close.index
    w = held_from(b0_rows, weekly, idx)
    sc = held_from(b0_scores, weekly, idx)
    m = reversal_multiplier(spy_close).shift(1).reindex(idx)
    w["SPY"] = w["SPY"] * m
    total = w.sum(axis=1)
    w = w.div(total.where(total > 1.0, 1.0), axis=0)
    valid = w.notna().all(axis=1) & m.notna()
    return w[valid], sc[valid]


def b5_used(sleeves, index):
    """Average of the sleeves' held rows, each divided by the number of sleeves
    and summed per ETF. `sleeves` is a list of (rows, trade_days)."""
    parts = [held_from(rows, days, index) for rows, days in sleeves]
    cols = sorted(set().union(*[p.columns for p in parts]))
    valid = pd.concat([p.notna().all(axis=1) for p in parts], axis=1).all(axis=1)
    total = sum(p.reindex(columns=cols).fillna(0.0) for p in parts) / len(parts)
    return total[valid]


def as_close_rows(used):
    """simulate() shifts targets by one day; give it rows that shift back to `used`."""
    return used.shift(-1)


# ---------------------------------------------------------------------------
# Bot 2 overlay: leverage, floor rule, financing
# ---------------------------------------------------------------------------
def floor_factor(equity, peak):
    """Grossman and Zhou: 4 x (1 - 0.75 x peak / equity), limited to [0, 1]."""
    return np.clip((1 - FLOOR * peak / equity) / (1 - FLOOR), 0.0, 1.0)


def spread_of(used):
    """Financing spread per day: 2x-fund cost where one exists, margin otherwise,
    averaged by weight."""
    sp = pd.Series({t: s.TWO_X_COST.get(s.TWO_X.get(t, ""), MARGIN_SPREAD) for t in used.columns})
    w = used.clip(lower=0.0)
    total = w.sum(axis=1)
    return (w @ sp / total.where(total > 0)).fillna(MARGIN_SPREAD)


def base_rows(daily, tbill, used):
    """Per-day inputs to the overlay from a base backtest."""
    eq = daily["equity"]
    r = eq / eq.shift(1).fillna(bt.START_VALUE) - 1          # day 1 counts from the $100,000 start
    rf = tbill.shift(1).reindex(eq.index) / s.TRADING_DAYS
    rf.iloc[0] = 0.0                                          # simulate() pays no interest on day 1
    frame = pd.DataFrame({
        "excess": r - rf,
        "invested": (1 - daily["cash"] / eq).clip(lower=0.0),
        "rf": rf,
        "spread": spread_of(used).reindex(eq.index).ffill().fillna(MARGIN_SPREAD),
    })
    return frame


def overlay(data, leverage, idx=None, floor=True):
    """Run the levered portfolio over the base rows `data` = (excess, invested,
    rf, spread), each a 1-D array over days.

    With idx=None it walks the history once and returns the daily returns and
    the exposure multiplier c for each day. With idx (days x paths of row
    numbers) it walks every bootstrap path at once; `leverage` can then be an
    array shaped (settings, 1) and the result is each path's final wealth and
    worst drop from peak, shaped (settings, paths).
    """
    excess, invested, rf, spread = data
    lev = np.asarray(leverage, dtype=float)
    days = len(excess) if idx is None else idx.shape[0]
    shape = lev.shape if idx is None else np.broadcast(lev, idx[0]).shape
    wealth, peak = np.ones(shape), np.ones(shape)
    worst = np.zeros(shape)
    c_prev = np.zeros(shape)
    if idx is None:
        out, mult = np.empty(days), np.empty(days)
    for t in range(days):
        j = t if idx is None else idx[t]
        f = floor_factor(wealth, peak) if floor else 1.0
        S = invested[j]
        cap = MAX_EXPOSURE / np.maximum(S, 1e-12)
        c = np.minimum(lev * f, cap)
        R = (rf[j] + c * excess[j]
             - np.maximum(c * S - 1.0, 0.0) * spread[j] / s.TRADING_DAYS
             - np.abs(c - c_prev) * S * CHANGE_COST)
        wealth = wealth * (1 + R)
        peak = np.maximum(peak, wealth)
        worst = np.minimum(worst, wealth / peak - 1)
        c_prev = c
        if idx is None:
            out[t], mult[t] = R, c
    if idx is None:
        return out, mult
    return wealth, worst


def stationary_indices(n, length, paths, block, rng):
    """Index paths for a circular stationary bootstrap (Politis and Romano 1994)."""
    p = 1.0 / block
    idx = np.empty((length, paths), dtype=np.int32)
    idx[0] = rng.integers(0, n, paths)
    for t in range(1, length):
        jump = rng.random(paths) < p
        idx[t] = np.where(jump, rng.integers(0, n, paths), (idx[t - 1] + 1) % n)
    return idx


def size_by_cap(rows_df, idx):
    """For each L in the grid: share of ten-year bootstrap paths that fall 25% or
    more, and the median ten-year annual return."""
    data = [rows_df[c].values for c in ("excess", "invested", "rf", "spread")]
    wealth, worst = overlay(data, L_GRID[:, None], idx=idx)
    cagr = wealth ** (s.TRADING_DAYS / PATH_DAYS) - 1
    return pd.DataFrame({
        "leverage": L_GRID,
        "breach_share": (worst <= CAP_DRAWDOWN).mean(axis=1),
        "median_return": np.median(cagr, axis=1),
        "p10_return": np.quantile(cagr, 0.10, axis=1),
        "median_worst_drop": np.median(worst, axis=1),
    })


def choose_leverage(table):
    allowed = table[table["breach_share"] <= CAP_SHARE]
    if allowed.empty:
        return None
    return allowed.loc[allowed["median_return"].idxmax()]


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------
def stats(returns, rf):
    eq = (1 + returns).cumprod() * bt.START_VALUE
    years = len(returns) / s.TRADING_DAYS
    excess = returns - rf
    year_end = eq.groupby(eq.index.year).last()
    yearly = year_end.pct_change()
    yearly.iloc[0] = year_end.iloc[0] / bt.START_VALUE - 1
    return {
        "ending": eq.iloc[-1],
        "annual_return": (eq.iloc[-1] / bt.START_VALUE) ** (1 / years) - 1,
        "annual_vol": returns.std() * sqrt(s.TRADING_DAYS),
        "sharpe": excess.mean() / excess.std() * sqrt(s.TRADING_DAYS),
        "max_drawdown": (eq / eq.cummax() - 1).min(),
        "worst_year": yearly.min(), "worst_year_label": int(yearly.idxmin()),
        "excess": excess, "equity": eq,
    }


def pbo_cscv(excess, blocks=16):
    """Probability of backtest overfitting (Bailey, Borwein, Lopez de Prado and
    Zhu): share of half/half splits where the best in-sample configuration ranks
    in the bottom half out of sample."""
    X = excess.values
    parts = np.array_split(np.arange(len(X)), blocks)
    n = X.shape[1]
    logits = []
    for chosen in combinations(range(blocks), blocks // 2):
        ins = np.concatenate([parts[i] for i in chosen])
        oos = np.concatenate([parts[i] for i in range(blocks) if i not in chosen])
        sr_in = X[ins].mean(0) / X[ins].std(0)
        sr_out = X[oos].mean(0) / X[oos].std(0)
        best = int(np.argmax(sr_in))
        rank = (sr_out < sr_out[best]).sum() + 1                      # 1 = worst
        omega = rank / (n + 1)
        logits.append(np.log(omega / (1 - omega)))
    return float((np.array(logits) <= 0).mean())


def pct(x, d=1):
    return f"{x * 100:.{d}f}%"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_bases():
    tickers = list(dict.fromkeys(s.TICKERS + SECTORS))
    opens, closes = s.load_bars(end=END, tickers=tickers, require=s.TICKERS + SECTORS_CORE)
    tbill = s.load_tbill(closes.index, end=END)
    weekly = bt.first_day_of_week(closes.index)
    monthly = bt.first_day_of_month(closes.index)
    every_day = pd.Series(True, index=closes.index)
    o9, c9 = opens[s.TICKERS], closes[s.TICKERS]
    oS, cS = opens[SECTORS].bfill(), closes[SECTORS].bfill()   # flat, untradeable line before launch

    b0_rows, b0_scores = s.target_weights_v2(c9, tbill), s.trend_scores(c9, tbill)
    b2_rows, b2_scores = b2_targets(closes)
    b3_rows, b3_scores = b3_targets(closes, tbill)
    b4_w, b4_sc = b4_used(b0_rows, b0_scores, closes["SPY"], weekly)
    b5_w = b5_used([(b0_rows, weekly), (b2_rows, weekly), (b3_rows, monthly)], closes.index)
    all_cols = list(b5_w.columns)
    oA, cA = opens[all_cols].bfill(), closes[all_cols].bfill()

    def used_of(rows, days):
        return held_from(rows, days, closes.index).dropna()

    runs = {
        "B0": (b0_rows, b0_scores, o9, c9, weekly, used_of(b0_rows, weekly)),
        "B2": (b2_rows, b2_scores, opens[["SPY"]], closes[["SPY"]], weekly, used_of(b2_rows, weekly)),
        "B3": (b3_rows, b3_scores, oS, cS, monthly, used_of(b3_rows, monthly)),
        "B4": (as_close_rows(b4_w), as_close_rows(b4_sc), o9, c9, every_day, b4_w),
        "B5": (as_close_rows(b5_w), None, oA, cA, weekly, b5_w),
    }
    base = {}
    for key, (rows, scores, o, c, days, used) in runs.items():
        daily, trades, _ = bt.simulate(rows, scores, o, c, tbill, days, START)
        base[key] = {"daily": daily, "trades": trades, "used": used}
    spy_hold = bt.simulate(pd.DataFrame({"SPY": 1.0}, index=closes.index), None,
                           opens[["SPY"]], closes[["SPY"]], tbill,
                           pd.Series(closes.index == pd.Timestamp(START), index=closes.index), START,
                           buffer=0.0)[0]
    return base, spy_hold, tbill


def main():
    os.makedirs(OUT, exist_ok=True)
    base, spy_hold, tbill = run_bases()

    rows = {k: base_rows(v["daily"], tbill, v["used"]) for k, v in base.items()}
    dates = rows["B0"].index
    for k in CANDIDATES:
        assert rows[k].index.equals(dates), f"{k} is on a different calendar"
        assert not rows[k].isna().any().any(), f"{k} has blank rows"

    # One set of bootstrap paths shared by every candidate
    block = float(optimal_block_length(rows["B0"]["excess"].values)["stationary"].iloc[0])
    rng = np.random.default_rng(SEED)
    idx = stationary_indices(len(dates), PATH_DAYS, N_PATHS, block, rng)

    tables, chosen, results = {}, {}, {}
    rf = rows["B0"]["rf"]
    for k in CANDIDATES:
        tables[k] = size_by_cap(rows[k], idx)
        pick = choose_leverage(tables[k])
        chosen[k] = pick
        L = 1.0 if pick is None else float(pick["leverage"])
        arrays = [rows[k][c].values for c in ("excess", "invested", "rf", "spread")]
        R, mult = overlay(arrays, L)
        results[k] = stats(pd.Series(R, index=dates), rf)
        results[k]["leverage"] = L
        results[k]["avg_exposure"] = float(np.mean(mult * rows[k]["invested"].values))
        results[k]["time_floored"] = float(np.mean(mult < L - 1e-9))

    # Benchmarks on the same dates
    def daily_returns(eq):
        return (eq / eq.shift(1).fillna(bt.START_VALUE) - 1).reindex(dates)

    v2_plain = stats(daily_returns(base["B0"]["daily"]["equity"]), rf)
    spy = stats(daily_returns(spy_hold["equity"]), rf)

    # Deflated Sharpe ratio counting trials 1 to 16
    trials = pd.read_csv("trials.csv")
    earlier = trials[trials["trial_id"] <= 11]
    trial_sharpes = list(earlier["sharpe_excess"] / sqrt(s.TRADING_DAYS))
    trial_sharpes += [results[k]["excess"].mean() / results[k]["excess"].std() for k in CANDIDATES]
    for k in CANDIDATES:
        results[k]["dsr"], sr0 = bt.deflated_sharpe(results[k]["excess"], trial_sharpes)

    # Winner by the registered rules
    ranked = sorted(CANDIDATES, key=lambda k: -1 if chosen[k] is None else chosen[k]["median_return"],
                    reverse=True)
    b0_median = chosen["B0"]["median_return"] if chosen["B0"] is not None else -np.inf
    passing = [k for k in ranked if chosen[k] is not None and results[k]["dsr"] >= DSR_BAR
               and (k == "B0" or chosen[k]["median_return"] > b0_median)]
    top = ranked[0]
    winner = top if top in passing else "B0"

    excess = pd.DataFrame({k: results[k]["excess"] for k in CANDIDATES})
    pbo = pbo_cscv(excess)
    spa_p = None
    if top != "B0":
        spa = SPA(-(results["B0"]["excess"].values), -(results[top]["excess"].values)[:, None],
                  block_size=int(round(block)), reps=2000, bootstrap="stationary", seed=SEED)
        spa.compute()
        spa_p = float(spa.pvalues["consistent"])

    write_outputs(base, tables, chosen, results, v2_plain, spy, ranked, winner, top,
                  block, pbo, spa_p, sr0, trials)
    return results, chosen, winner


def write_outputs(base, tables, chosen, results, v2_plain, spy, ranked, winner, top,
                  block, pbo, spa_p, sr0, trials):
    # equity and leverage tables
    eq = pd.DataFrame({k: results[k]["equity"] for k in CANDIDATES})
    eq["version_2"] = v2_plain["equity"]
    eq["SPY"] = spy["equity"]
    eq.round(2).to_csv(os.path.join(OUT, "equity.csv"), index_label="date")
    lev = pd.concat([t.assign(candidate=k) for k, t in tables.items()])
    lev[["candidate", "leverage", "breach_share", "median_return", "p10_return",
         "median_worst_drop"]].round(5).to_csv(os.path.join(OUT, "leverage.csv"), index=False)

    # trials 12 to 16
    keep = trials[trials["trial_id"] <= 11]
    new = []
    for k in CANDIDATES:
        r = results[k]
        new.append({"trial_id": TRIAL_IDS[k], "date_run": "2026-11-23", "spec_version": "bot2",
                    "description": LABEL[k],
                    "settings": f"leverage {r['leverage']:.1f} by the cap rule; floor at 75% of peak; "
                                f"exposure at most 2x",
                    "sharpe_excess": round(r["sharpe"], 3), "max_drawdown": round(r["max_drawdown"], 4),
                    "notes": "registered in SPEC.md before testing"})
    pd.concat([keep, pd.DataFrame(new)]).to_csv("trials.csv", index=False)

    # chart
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 5))
        for col in eq.columns:
            ax.plot(eq.index, eq[col], label=LABEL.get(col, col.replace("_", " ")), linewidth=1.2)
        ax.set_yscale("log")
        ax.set_ylabel("Value of $100,000 (log scale)")
        ax.set_title("Bot 2 candidates at their chosen leverage, Feb 2007 to Nov 2026")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(OUT, "equity.png"), dpi=130)
        plt.close(fig)
    except ImportError:
        pass

    L = []
    L.append("# Bot 2 candidates: results\n")
    L.append(f"Rules: SPEC.md, \"Bot 2: registered candidates\", committed before this run. "
             f"Period: {START} to {eq.index[-1].date()}. Each candidate starts with $100,000.\n")
    L.append(f"Leverage is chosen from {len(L_GRID)} settings (1.0 to 3.0) using {N_PATHS:,} "
             f"ten-year stationary-bootstrap paths (mean block {block:.1f} days, seed {SEED}), "
             f"the same paths for every candidate.\n")
    L.append("## Ranking by the registered rule\n")
    L.append("| Rank | Candidate | Leverage | Median 10-year return | 10th percentile | "
             "Paths falling 25% | Deflated Sharpe | Passes |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, k in enumerate(ranked, 1):
        c = chosen[k]
        if c is None:
            L.append(f"| {i} | {LABEL[k]} | none allowed | | | | | no |")
            continue
        ok = results[k]["dsr"] >= DSR_BAR and (k == "B0" or c["median_return"] > chosen["B0"]["median_return"])
        L.append(f"| {i} | {LABEL[k]} | {c['leverage']:.1f} | {pct(c['median_return'])} | "
                 f"{pct(c['p10_return'])} | {pct(c['breach_share'])} | {results[k]['dsr']:.3f} | "
                 f"{'yes' if ok else 'no'} |")
    L.append("")
    L.append(f"Winner under the registered rules: **{LABEL[winner]}**"
             + ("" if winner == top else f" ({LABEL[top]} ranked first but did not pass)") + ".\n")
    L.append("## Full backtest at the chosen leverage\n")
    L.append("| Portfolio | Ending value | Annual return | Volatility | Sharpe | Max drawdown | "
             "Worst year | Average exposure | Days below full leverage |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for k in CANDIDATES:
        r = results[k]
        L.append(f"| {LABEL[k]} | ${r['ending']:,.0f} | {pct(r['annual_return'])} | {pct(r['annual_vol'])} | "
                 f"{r['sharpe']:.2f} | {pct(r['max_drawdown'])} | {pct(r['worst_year'])} "
                 f"({r['worst_year_label']}) | {pct(r['avg_exposure'], 0)} | {pct(r['time_floored'], 0)} |")
    for name, r in (("Version 2 (live bot, no leverage)", v2_plain), ("SPY", spy)):
        L.append(f"| {name} | ${r['ending']:,.0f} | {pct(r['annual_return'])} | {pct(r['annual_vol'])} | "
                 f"{r['sharpe']:.2f} | {pct(r['max_drawdown'])} | {pct(r['worst_year'])} "
                 f"({r['worst_year_label']}) | | |")
    L.append("")
    L.append(f"Probability of backtest overfitting across the five (CSCV, 16 blocks): {pbo:.2f}.")
    if spa_p is not None:
        L.append(f"Hansen's SPA test of {top} against B0: p = {spa_p:.3f} "
                 f"(below 0.05 would mean {top} beats B0 after allowing for luck).")
    L.append(f"The deflated Sharpe ratio counts all 16 trials; the best of 16 unskilled trials would be "
             f"expected to reach an annual Sharpe ratio of about {sr0:.2f} by luck.\n")
    L.append("Notes: levered returns scale each base backtest's daily return above T-bills (SPEC.md, "
             "bot 2 rule 4). The extra 5 bp is charged on each change in the leverage multiplier c, "
             "times the invested share. Leverage is fitted on the same history it is tested on. "
             "Returns here count from the $100,000 start on Feb 12, 2007, so version 2 shows 4.9% a year "
             "and a 0.44 Sharpe ratio; the version tables, which count from the first close, show 5.0% and 0.45. "
             "tools/bot2_diagnostics.py writes diagnostics.md: the base rules without leverage, the cost of "
             "the floor rule, and longer bootstrap blocks.\n")
    L.append("![Growth of $100,000](equity.png)\n")
    with open(os.path.join(OUT, "summary.md"), "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    res, chosen, winner = main()
    print(open(os.path.join(OUT, "summary.md")).read())
