"""
backtest.py: runs the strategy in SPEC.md over past prices, along with the
benchmarks, variants and pass/fail bars the spec sets out.

Usage:  python backtest.py 1    (spec version 1)
        python backtest.py 2    (spec version 2)
        python backtest.py 3    (spec version 3)
        python backtest.py 4    (spec version 4)
        python backtest.py compare   (table and chart of all versions, after the version 4 run)

Writes summary.md, equity.csv, trades.csv, episodes.csv and equity.png to
results/v1/, results/v2/ or results/v3/, and rewrites trials.csv with one
row per trial run so far.
"""
import os
from datetime import datetime
from math import e, sqrt
from statistics import NormalDist

import numpy as np
import pandas as pd

import strategy as s

COST_BPS = 5               # cost charged on every dollar traded, in basis points
BUFFER = 0.02              # drift allowed before a position is resized
START_VALUE = 100_000.0
RESULTS_DIR = "results"
NORMAL = NormalDist()
EULER_GAMMA = 0.5772156649


# ---------------------------------------------------------------------------
# Trade calendars
# ---------------------------------------------------------------------------
def first_day_of_week(index):
    """True on the first trading day of each calendar week."""
    iso = index.isocalendar()
    key = iso["year"].astype(int) * 100 + iso["week"].astype(int)
    return key.ne(key.shift())


def first_day_of_month(index):
    """True on the first trading day of each month."""
    key = pd.Series(index.year * 100 + index.month, index=index)
    return key.ne(key.shift())


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def simulate(targets, scores, opens, closes, tbill, trade_days, start,
             cost_bps=COST_BPS, buffer=BUFFER):
    """Run one portfolio day by day from `start`.

    targets and scores are rows as of each close. Each row is traded at the
    NEXT trading day's open, and only on trade days. An ETF is traded when its
    score changed since its last trade or its weight drifted more than
    `buffer` from target. With scores=None (fixed-weight benchmarks) the first
    trade day buys everything and later trades follow the buffer only.

    Returns (daily DataFrame, trades DataFrame, episodes DataFrame).
    """
    cost = cost_bps / 1e4
    tickers = list(closes.columns)
    idx = closes.loc[start:].index
    O = opens.loc[idx].values
    C = closes.loc[idx].values
    TGT = targets.reindex(closes.index).shift(1).loc[idx].values
    SC = None if scores is None else scores.reindex(closes.index).shift(1).loc[idx].values
    TD = trade_days.reindex(idx).fillna(False).values.astype(bool)
    Y = tbill.reindex(idx).values

    n = len(tickers)
    shares = np.zeros(n)
    last_score = np.full(n, np.nan)
    cash = START_VALUE
    traded_once = False
    daily, trades, episodes, open_ep = [], [], [], {}

    for i, d in enumerate(idx):
        o, c = O[i], C[i]
        if i > 0:
            cash *= 1 + Y[i - 1] / s.TRADING_DAYS           # T-bill interest on cash
        pnl = np.zeros(n)
        if i > 0:
            pnl += shares * (o - C[i - 1])                   # overnight move
        before = shares.copy()
        traded = 0.0
        value_open = cash + shares @ o

        if TD[i] and not np.isnan(TGT[i]).any():
            tgt = TGT[i]
            current_w = shares * o / value_open
            do = np.abs(tgt - current_w) > buffer
            if SC is not None:
                do |= ~(SC[i] == last_score)                 # score changed (NaN counts as changed)
            elif not traded_once:
                do[:] = True
            new = shares.copy()
            new[do] = tgt[do] * value_open / o[do]
            delta = new - shares
            sell_val = -np.minimum(delta, 0) * o
            buy_val = np.maximum(delta, 0) * o
            cash += sell_val.sum() * (1 - cost)              # sells first
            need = buy_val.sum() * (1 + cost)
            if need > cash and need > 0:                     # never borrow
                scale = max(cash, 0.0) / need
                buy_val *= scale
                new = shares + np.minimum(delta, 0) + np.maximum(delta, 0) * scale
            cash -= buy_val.sum() * (1 + cost)
            fees = (sell_val + buy_val) * cost
            pnl -= fees
            for j in np.nonzero(sell_val + buy_val)[0]:
                trades.append({
                    "date": d.date(), "ticker": tickers[j],
                    "side": "buy" if buy_val[j] > 0 else "sell",
                    "dollars": round(float(buy_val[j] or sell_val[j]), 2),
                    "price": round(float(o[j]), 4),
                    "target_weight": round(float(tgt[j]), 4),
                })
            shares = new
            if SC is not None:
                last_score[do] = SC[i][do]
            traded_once = True
            traded = float((sell_val + buy_val).sum())

        pnl += shares * (c - o)                              # open-to-close move

        held_before, held_now = before > 1e-12, shares > 1e-12
        for j in np.nonzero(~held_before & held_now)[0]:
            open_ep[j] = {"ticker": tickers[j], "entry": d.date(),
                          "entry_equity": value_open, "pnl": 0.0}
        for j, ep in open_ep.items():
            ep["pnl"] += pnl[j]
        for j in np.nonzero(held_before & ~held_now)[0]:
            ep = open_ep.pop(j)
            ep["exit"] = d.date()
            episodes.append(ep)

        equity = cash + shares @ c
        daily.append({"date": d, "equity": equity, "cash": cash, "traded": traded})

    for ep in open_ep.values():
        ep["exit"] = None                                    # still open at the end
        episodes.append(ep)

    daily = pd.DataFrame(daily).set_index("date")
    episodes = pd.DataFrame(episodes)
    if len(episodes):
        episodes["return_on_equity"] = episodes["pnl"] / episodes["entry_equity"]
    return daily, pd.DataFrame(trades), episodes


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------
def metrics(daily, tbill):
    equity = daily["equity"]
    r = equity.pct_change().dropna()
    rf = (tbill.shift(1).reindex(r.index) / s.TRADING_DAYS)
    excess = r - rf
    years = len(r) / s.TRADING_DAYS
    year_end = equity.groupby(equity.index.year).last()
    yearly = year_end.pct_change()
    yearly.iloc[0] = year_end.iloc[0] / equity.iloc[0] - 1
    return {
        "annual_return": (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1,
        "annual_vol": r.std() * sqrt(s.TRADING_DAYS),
        "sharpe": excess.mean() / excess.std() * sqrt(s.TRADING_DAYS),
        "max_drawdown": (equity / equity.cummax() - 1).min(),
        "worst_year": yearly.min(),
        "worst_year_label": int(yearly.idxmin()),
        "turnover": daily["traded"].sum() / equity.mean() / years,
        "invested": (1 - daily["cash"] / daily["equity"]).mean(),
        "excess_daily": excess,
    }


def episode_stats(episodes):
    closed = episodes[episodes["exit"].notna()]
    wins = closed[closed["return_on_equity"] > 0]["return_on_equity"]
    losses = closed[closed["return_on_equity"] <= 0]["return_on_equity"]
    return {
        "trades": len(closed),
        "still_open": int(episodes["exit"].isna().sum()),
        "win_rate": len(wins) / len(closed),
        "avg_win": wins.mean(),
        "avg_loss": losses.mean(),
        "expectancy": closed["return_on_equity"].mean(),
    }


def deflated_sharpe(excess_daily, trial_daily_sharpes):
    """Bailey and Lopez de Prado (2014). Probability that the true Sharpe
    ratio is above zero after correcting for the number of trials and for
    non-normal returns. Inputs are daily (not annualized)."""
    x = excess_daily.dropna()
    T = len(x)
    sr = x.mean() / x.std()
    skew = x.skew()
    kurt = x.kurt() + 3                                      # pandas gives excess kurtosis
    N = len(trial_daily_sharpes)
    if N > 1:
        sd = np.std(trial_daily_sharpes, ddof=1)
        sr0 = sd * ((1 - EULER_GAMMA) * NORMAL.inv_cdf(1 - 1 / N)
                    + EULER_GAMMA * NORMAL.inv_cdf(1 - 1 / (N * e)))
    else:
        sr0 = 0.0
    z = (sr - sr0) * sqrt(T - 1) / sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
    return NORMAL.cdf(z), sr0 * sqrt(s.TRADING_DAYS)


# ---------------------------------------------------------------------------
# Target builders for the main version, variants and benchmarks
# ---------------------------------------------------------------------------
def trend_targets(closes, tbill, lookbacks=s.LOOKBACKS, vol_window=s.VOL_WINDOW):
    scores = s.trend_scores(closes, tbill, lookbacks)
    k = (s.TARGET_VOL / s.portfolio_vol(closes, vol_window)).clip(upper=1.0)
    return scores.mul(k / len(closes.columns), axis=0), scores


def inverse_vol_targets(closes, scores):
    """The first draft's sizing: weights by 1/volatility (63 days), the mix
    scaled to 10% using a 126-day covariance, each ETF capped at 25%."""
    r = closes.pct_change()
    inv = 1 / (r.rolling(63).std() * sqrt(s.TRADING_DAYS))
    base = inv.div(inv.sum(axis=1), axis=0)
    cov = r.rolling(s.VOL_WINDOW).cov()
    sigma = pd.Series(np.nan, index=closes.index)
    for d in base.dropna().index:
        S = cov.loc[d].values * s.TRADING_DAYS
        if not np.isnan(S).any():
            w = base.loc[d].values
            sigma[d] = sqrt(w @ S @ w)
    k = (s.TARGET_VOL / sigma).clip(upper=1.0)
    return (base.mul(k, axis=0) * scores).clip(upper=0.25)


def constant_targets(closes, weights):
    return pd.DataFrame([weights] * len(closes), index=closes.index)[list(closes.columns)]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def pct(x, digits=1):
    return f"{x * 100:.{digits}f}%"


TRIAL_SPECS = [
    ("main", "Spec v1 as written", "lookbacks 21/63/126/252; vol window 126; T-bill hurdle; equal weights, 10% vol ceiling"),
    ("lookbacks_short", "Shorter lookbacks", "lookbacks 15/50/100/200"),
    ("lookbacks_long", "Longer lookbacks", "lookbacks 30/90/180/300"),
    ("vol_window_63", "63-day volatility window", "vol window 63"),
    ("zero_hurdle", "0% hurdle", "trend test against 0% instead of T-bills"),
    ("inverse_vol", "Inverse-volatility weights", "1/vol (63d) base weights, 25% cap"),
    ("v2", "Spec v2: 10% volatility as a target", "v1 signals; weights scaled to 10% vol, 25% per ETF, 100% max invested"),
    ("v3", "Spec v3: 13 ETFs", "v2 sizing; adds UUP, FXY, DBB, DBA, each joining after 252 days of prices"),
    ("v4", "Spec v4: v3 at 1.5x through 2x funds", "v3 limits x1.5 (15% vol, 37.5% per ETF, 150% exposure); 2x funds above 100%; simulated 2x prices with measured costs"),
]
TRIALS_BY_VERSION = {1: 6, 2: 7, 3: 8, 4: 9}
CANDIDATE = {1: "main", 2: "v2", 3: "v3", 4: "v4"}
SPEC_OF_TRIAL = {"v2": "2", "v3": "3", "v4": "4"}
LABEL = {"main": "Trend bot (v1)", "v2": "Trend bot (v2)", "v3": "Trend bot (v3)", "v4": "Trend bot (v4)",
         "B1": "B1: same 9 ETFs, always invested", "B1_13": "B1: same 13 ETFs, always invested",
         "B1_v4": "B1: same 13 ETFs, always invested at 1.5x",
         "B2": "B2: 60% SPY / 40% IEF", "B3": "B3: SPY buy and hold"}
COLUMN = {"main": "trend_bot_v1", "v2": "trend_bot_v2", "v3": "trend_bot_v3", "v4": "trend_bot_v4",
          "B1": "B1_always_invested", "B1_13": "B1_13_always_invested", "B1_v4": "B1_v4_always_invested",
          "B2": "B2_60_40", "B3": "B3_SPY"}


def main(version=1, end=None, out_root=RESULTS_DIR, trials_path="trials.csv", quiet=False):
    out = f"{out_root}/v{version}"
    os.makedirs(out, exist_ok=True)
    run_time = datetime.now(s.NEW_YORK)

    # Data. Version 3 adds four ETFs that started in 2007; dates are kept
    # wherever the original nine have prices, so every version shares one calendar.
    if version >= 3:
        opens13, closes13 = s.load_bars(end=end, tickers=s.TICKERS_V3, require=s.TICKERS)
        opens, closes = opens13[s.TICKERS], closes13[s.TICKERS]
    else:
        opens, closes = s.load_bars(end=end)
    tbill = s.load_tbill(closes.index, end=end)

    weekly = first_day_of_week(closes.index)
    monthly = first_day_of_month(closes.index)

    main_targets = s.target_weights(closes, tbill)
    main_scores = s.trend_scores(closes, tbill)
    first_valid = main_targets.index[0]
    start = weekly[weekly & (weekly.index > first_valid)].index[0]

    # Each trial: (targets, scores, opens, closes) for the simulator
    if version >= 4:
        # 2x funds: simulated prices from their 1x ETFs; each takes its 1x ETF's trend score
        o2, c2 = s.two_x_bars(opens13.bfill(), closes13.bfill(), tbill)
        bars_v4 = (pd.concat([opens13.bfill(), o2], axis=1), pd.concat([closes13.bfill(), c2], axis=1))

        def with_two_x(scores):
            return pd.concat([scores, scores[list(s.TWO_X)].rename(columns=s.TWO_X)], axis=1)

    trial_specs = TRIAL_SPECS[:TRIALS_BY_VERSION[version]]
    candidate = CANDIDATE[version]
    bars9 = (opens, closes)
    builders = {
        "main": lambda: (main_targets, main_scores, *bars9),
        "lookbacks_short": lambda: (*trend_targets(closes, tbill, (15, 50, 100, 200)), *bars9),
        "lookbacks_long": lambda: (*trend_targets(closes, tbill, (30, 90, 180, 300)), *bars9),
        "vol_window_63": lambda: (*trend_targets(closes, tbill, vol_window=63), *bars9),
        "zero_hurdle": lambda: (*trend_targets(closes, tbill * 0), *bars9),
        "inverse_vol": lambda: (inverse_vol_targets(closes, main_scores), main_scores, *bars9),
        "v2": lambda: (s.target_weights_v2(closes, tbill), main_scores, *bars9),
        # Prices before an ETF's first trading day are filled backward so the
        # simulator sees a flat, untradeable line; the ETF's weight is 0 then.
        "v3": lambda: (s.target_weights_v3(closes13, tbill), s.trend_scores(closes13, tbill),
                       opens13.bfill(), closes13.bfill()),
        "v4": lambda: (s.target_weights_v4(closes13, tbill), with_two_x(s.trend_scores(closes13, tbill)),
                       *bars_v4),
    }
    trial_inputs = {key: builders[key]() for key, _, _ in trial_specs}
    results = {key: simulate(t, sc, o, c, tbill, weekly, start)
               for key, (t, sc, o, c) in trial_inputs.items()}

    # Benchmarks
    k = s.shrink_factor(closes)
    ones = pd.DataFrame(1.0, index=closes.index, columns=closes.columns).where(k.notna(), axis=0)
    b1_targets = ones.mul(k / len(closes.columns), axis=0)
    only_start = pd.Series(closes.index == start, index=closes.index)
    bench = {
        "B1": simulate(b1_targets, ones, opens, closes, tbill, weekly, start),
        "B2": simulate(constant_targets(closes, {**{t: 0.0 for t in closes.columns}, "SPY": 0.6, "IEF": 0.4}),
                       None, opens, closes, tbill, monthly | only_start, start, buffer=0.0),
        "B3": simulate(constant_targets(closes, {**{t: 0.0 for t in closes.columns}, "SPY": 1.0}),
                       None, opens, closes, tbill, only_start, start, buffer=0.0),
    }
    if version >= 3:
        # Version 3's own control: the same 13 ETFs always held (score 1 once
        # each has a year of prices), sized by the version 3 rule.
        scores13 = s.trend_scores(closes13, tbill)
        ones13 = scores13.notna().astype(float).where(scores13.notna())
        bench["B1_13"] = simulate(s.target_weights_v3(closes13, tbill, scores=ones13), ones13,
                                  opens13.bfill(), closes13.bfill(), tbill, weekly, start)
    if version >= 4:
        # Version 4's control: the same 13 ETFs always held, at 1.5x through 2x funds
        bench["B1_v4"] = simulate(s.target_weights_v4(closes13, tbill, scores=ones13), with_two_x(ones13),
                                  *bars_v4, tbill, weekly, start)
    control = {1: "B1", 2: "B1", 3: "B1_13", 4: "B1_v4"}[version]

    # Cost sensitivity for the candidate (not trials)
    t_c, sc_c, o_c, c_c = trial_inputs[candidate]
    costs = {bps: metrics(simulate(t_c, sc_c, o_c, c_c, tbill, weekly, start, cost_bps=bps)[0], tbill)
             for bps in (5, 10, 20)}

    # Measurements
    m = {key: metrics(results[key][0], tbill) for key in results}
    mb = {key: metrics(bench[key][0], tbill) for key in bench}
    daily_sharpes = [m[key]["excess_daily"].mean() / m[key]["excess_daily"].std() for key, _, _ in trial_specs]
    dsr, sr0_annual = deflated_sharpe(m[candidate]["excess_daily"], daily_sharpes)
    earlier = [key for key in ("main", "v2", "v3") if key in m and key != candidate]
    dsr_earlier = {key: deflated_sharpe(m[key]["excess_daily"], daily_sharpes)[0] for key in earlier}
    ep = episode_stats(results[candidate][2])
    mc = m[candidate]

    bars = {
        "sharpe": mc["sharpe"] > mb[control]["sharpe"],
        "drawdown": mc["max_drawdown"] > mb[control]["max_drawdown"],
        "dsr": dsr >= 0.95,
    }
    passed = all(bars.values())

    versions_shown = [candidate] + [key for key in ("v3", "v2", "main") if key in m and key != candidate]
    bench_shown = [control] + (["B1"] if control == "B1_13" else []) + ["B2", "B3"]
    series = {**{key: results[key][0] for key in versions_shown}, **{key: bench[key][0] for key in bench_shown}}

    periods = [("2007-2012", "2007", "2012"), ("2013-2019", "2013", "2019"), ("2020-2026", "2020", "2026")]
    sub = {}
    for label, a, b in periods:
        for key in versions_shown + [control, "B2", "B3"]:
            if len(series[key].loc[a:b]) > 1:
                sub[(label, key)] = metrics(series[key].loc[a:b], tbill)

    eq = results[candidate][0]["equity"]
    r60 = eq.pct_change(60).dropna()
    band = r60.quantile([0.05, 0.5, 0.95])

    # Files
    equity_table = pd.DataFrame({COLUMN[key]: series[key]["equity"] for key in series}).round(2)
    equity_table.to_csv(f"{out}/equity.csv", index_label="date")
    results[candidate][1].to_csv(f"{out}/trades.csv", index=False)
    episodes = results[candidate][2].copy()
    episodes[["entry_equity", "pnl"]] = episodes[["entry_equity", "pnl"]].round(2)
    episodes["return_on_equity"] = episodes["return_on_equity"].round(6)
    episodes.to_csv(f"{out}/episodes.csv", index=False)

    trial_rows = []
    for n_, (key, desc, settings) in enumerate(trial_specs, start=1):
        trial_rows.append({"trial_id": n_, "date_run": run_time.date(),
                           "spec_version": SPEC_OF_TRIAL.get(key, "1"),
                           "description": desc, "settings": settings,
                           "sharpe_excess": round(m[key]["sharpe"], 3),
                           "max_drawdown": round(m[key]["max_drawdown"], 4),
                           "notes": "planned in SPEC.md"})
    pd.DataFrame(trial_rows).to_csv(trials_path, index=False)

    make_chart(equity_table, out, version)

    # summary.md
    L = [f"# Backtest results, spec version {version}\n"]
    L.append(f"Run on {run_time:%Y-%m-%d %H:%M} ET with Yahoo Finance data downloaded at run time. "
             f"Simulation from {start.date()} to {closes.index[-1].date()}, starting at ${START_VALUE:,.0f}. "
             f"Trades fill at the next day's open with a {COST_BPS} bp cost; cash earns the 13-week T-bill rate.\n")
    L.append(f"**Result: {'PASS' if passed else 'FAIL'}** on the three bars set in SPEC.md before this run.\n")
    L.append("| Bar | Result |\n| --- | --- |")
    short = {"B1": "B1", "B1_13": "B1 (13 ETFs)", "B1_v4": "B1 (13 ETFs at 1.5x)"}[control]
    L.append(f"| Sharpe ratio beats {short} | {mc['sharpe']:.2f} vs {mb[control]['sharpe']:.2f}: {'yes' if bars['sharpe'] else 'no'} |")
    L.append(f"| Max drawdown smaller than {short} | {pct(mc['max_drawdown'])} vs {pct(mb[control]['max_drawdown'])}: {'yes' if bars['drawdown'] else 'no'} |")
    L.append(f"| Deflated Sharpe ratio at least 0.95 | {dsr:.3f} ({len(trial_specs)} trials): {'yes' if bars['dsr'] else 'no'} |\n")
    for key, value in dsr_earlier.items():
        name = {"main": "version 1", "v2": "version 2", "v3": "version 3"}[key]
        L.append(f"Counting all {len(trial_specs)} trials, {name}'s deflated Sharpe ratio is now {value:.3f}.\n")

    L.append("## Full period\n")
    L.append("| Portfolio | Annual return | Annual volatility | Sharpe (vs T-bills) | Max drawdown | Worst calendar year | Turnover per year | Average invested |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for key in series:
        x = m[key] if key in m else mb[key]
        L.append(f"| {LABEL[key]} | {pct(x['annual_return'])} | {pct(x['annual_vol'])} | {x['sharpe']:.2f} | "
                 f"{pct(x['max_drawdown'])} | {pct(x['worst_year'])} ({x['worst_year_label']}) | "
                 f"{pct(x['turnover'], 0)} | {pct(x['invested'], 0)} |")
    L.append("\nFirst and last calendar years are partial (2007 starts in February, 2026 ends in November).\n")
    if version >= 4:
        exp_total = s.exposure_of(trial_inputs["v4"][0]).sum(axis=1).loc[start:]
        L.append(f"Average invested counts dollars held. Version 4's average exposure, counting each 2x fund twice, "
                 f"was {pct(exp_total.mean(), 0)}, and its highest was {pct(exp_total.max(), 0)}.\n")
    L.append("![Equity curves](equity.png)\n")

    L.append("## Sub-periods\n")
    L.append("| Period | Portfolio | Annual return | Sharpe | Max drawdown |\n| --- | --- | --- | --- | --- |")
    for (label, key), x in sub.items():
        L.append(f"| {label} | {LABEL[key]} | {pct(x['annual_return'])} | {x['sharpe']:.2f} | {pct(x['max_drawdown'])} |")

    L.append("\n## Expectancy per trade\n")
    L.append("A trade is one stretch from opening a position in an ETF to fully exiting it, including resizes in between. "
             "Results are a share of total portfolio equity at entry.\n")
    L.append("| Closed trades | Win rate | Average win | Average loss | Expectancy per trade | Still open |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    L.append(f"| {ep['trades']} | {pct(ep['win_rate'])} | {pct(ep['avg_win'], 2)} | {pct(ep['avg_loss'], 2)} | "
             f"{pct(ep['expectancy'], 3)} | {ep['still_open']} |\n")

    L.append("## Trials\n")
    L.append(f"The deflated Sharpe ratio counts all {len(trial_specs)} trials below. "
             f"Given their spread, the best of {len(trial_specs)} unskilled trials would be expected to reach an annual Sharpe ratio of about {sr0_annual:.2f} by luck.\n")
    L.append("| # | Trial | Annual return | Sharpe | Max drawdown |\n| --- | --- | --- | --- | --- |")
    for n_, (key, desc, _) in enumerate(trial_specs, start=1):
        L.append(f"| {n_} | {desc} | {pct(m[key]['annual_return'])} | {m[key]['sharpe']:.2f} | {pct(m[key]['max_drawdown'])} |")

    L.append(f"\n## Cost sensitivity (version {version})\n")
    L.append("| Cost per dollar traded | Annual return | Sharpe |\n| --- | --- | --- |")
    for bps, x in costs.items():
        L.append(f"| {bps} bp | {pct(x['annual_return'])} | {x['sharpe']:.2f} |")

    L.append("\n## Expected range for the live run\n")
    L.append(f"Over every 60-trading-day window in the backtest, the bot's return ran from {pct(band.loc[0.05])} "
             f"(5th percentile) to {pct(band.loc[0.95])} (95th percentile), with a median of {pct(band.loc[0.5])}. "
             f"{pct((r60 > 0).mean(), 0)} of those windows ended positive. The live paper run is judged against this band.\n")

    with open(f"{out}/summary.md", "w") as f:
        f.write("\n".join(L))
    if not quiet:
        print("\n".join(L))


def make_chart(equity_table, out, version):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = {COLUMN[key]: LABEL[key] for key in COLUMN}
    lead = COLUMN[CANDIDATE[version]]
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10, 7), sharex=True,
                                      gridspec_kw={"height_ratios": [3, 1.4]})
    for col in equity_table.columns:
        series = equity_table[col] / equity_table[col].iloc[0]
        width = 2.2 if col == lead else 1.1
        top.plot(series.index, series.values, label=labels[col], linewidth=width)
        dd = equity_table[col] / equity_table[col].cummax() - 1
        bottom.plot(dd.index, dd.values * 100, linewidth=width)
    top.set_yscale("log")
    dollars = matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:g}")
    top.yaxis.set_major_formatter(dollars)
    top.yaxis.set_minor_formatter(dollars)
    top.set_ylabel("Growth of $1 (log scale)")
    top.set_title(f"Trend bot v{version} vs benchmarks, after costs")
    top.legend(loc="upper left")
    bottom.set_ylabel("Drawdown (%)")
    fig.tight_layout()
    fig.savefig(f"{out}/equity.png", dpi=130)
    plt.close(fig)


def compare(source=f"{RESULTS_DIR}/v4/equity.csv", out_root=RESULTS_DIR):
    """Side-by-side table and chart of the four versions, SPY and 60/40,
    from the equity curves the version 4 run saved. Writes
    results/comparison.md and results/comparison.png."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cols = {"trend_bot_v1": "Version 1", "trend_bot_v2": "Version 2", "trend_bot_v3": "Version 3",
            "trend_bot_v4": "Version 4", "B2_60_40": "60% SPY / 40% IEF", "B3_SPY": "SPY"}
    eq = pd.read_csv(source, index_col="date", parse_dates=True)[list(cols)]
    tbill = s.load_tbill(eq.index)
    r = eq.pct_change().dropna()
    rf = tbill.shift(1).reindex(r.index) / s.TRADING_DAYS
    years = len(r) / s.TRADING_DAYS
    year_end = eq.groupby(eq.index.year).last()
    yearly = year_end.pct_change()
    yearly.iloc[0] = year_end.iloc[0] / eq.iloc[0] - 1

    L = ["# Comparison: versions 1 to 4, SPY and 60/40\n"]
    L.append(f"Each starts with ${START_VALUE:,.0f} on {eq.index[0].date()} and runs to {eq.index[-1].date()}, "
             f"after a {COST_BPS} bp cost on every trade. Sharpe ratios are measured against T-bills.\n")
    L.append("| Portfolio | Ending value | Annual return | Volatility | Sharpe | Max drawdown | Worst year | Best year |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for col, name in cols.items():
        x = r[col] - rf
        dd = (eq[col] / eq[col].cummax() - 1).min()
        L.append(f"| {name} | ${eq[col].iloc[-1]:,.0f} | {pct((eq[col].iloc[-1] / eq[col].iloc[0]) ** (1 / years) - 1)} | "
                 f"{pct(r[col].std() * sqrt(s.TRADING_DAYS))} | {x.mean() / x.std() * sqrt(s.TRADING_DAYS):.2f} | "
                 f"{pct(dd)} | {pct(yearly[col].min())} ({yearly[col].idxmin()}) | "
                 f"{pct(yearly[col].max())} ({yearly[col].idxmax()}) |")
    L.append("\n![Growth of $100,000](comparison.png)\n")
    L.append("## Calendar-year returns\n")
    L.append("2007 starts on Feb 12 and 2026 ends on Nov 20.\n")
    L.append("| Year | " + " | ".join(cols.values()) + " |")
    L.append("| --- |" + " --- |" * len(cols))
    for year, row in yearly.iterrows():
        L.append(f"| {year} | " + " | ".join(pct(row[c]) for c in cols) + " |")
    with open(f"{out_root}/comparison.md", "w") as f:
        f.write("\n".join(L) + "\n")

    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10, 7.5), sharex=True,
                                      gridspec_kw={"height_ratios": [3, 1.4]})
    for col, name in cols.items():
        width = 2.0 if col.startswith("trend") else 1.4
        style = "--" if not col.startswith("trend") else "-"
        top.plot(eq.index, eq[col], label=name, linewidth=width, linestyle=style)
        bottom.plot(eq.index, (eq[col] / eq[col].cummax() - 1) * 100, linewidth=width, linestyle=style)
    top.set_yscale("log")
    money = matplotlib.ticker.FuncFormatter(lambda v, _: f"${v / 1000:,.0f}k")
    top.yaxis.set_major_formatter(money)
    top.yaxis.set_minor_formatter(money)
    top.set_ylabel("Value of $100,000 (log scale)")
    top.set_title("Trend bot versions 1 to 4 vs SPY and 60/40, after costs")
    top.legend(loc="upper left")
    bottom.set_ylabel("Drawdown (%)")
    fig.tight_layout()
    fig.savefig(f"{out_root}/comparison.png", dpi=130)
    plt.close(fig)
    print("\n".join(L))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "compare":
        compare()
    else:
        main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
