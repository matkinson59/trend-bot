"""
strategy.py: the trading rules for Trend Bot.

These rules are written once and used by both the backtest and the live
bot, so the two can't disagree.

Every number below comes from "Trend Bot Strategy Spec v1". Changing any of
them means a new spec version and a new row in trials.csv.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Settings from the spec
# ---------------------------------------------------------------------------
TICKERS = ["SPY", "EFA", "EEM", "VNQ", "IEF", "TLT", "TIP", "GLD", "DBC"]
# Spec version 3 adds the US dollar, the Japanese yen, industrial metals and
# agriculture. These four started trading in 2007, so each one joins once it
# has a full year of prices.
TICKERS_V3 = TICKERS + ["UUP", "FXY", "DBB", "DBA"]
# Locked for live trading on Nov 23, 2026 (SPEC.md, "Locked: version 2").
LIVE_VERSION = 2                 # the only version the live bot runs
CASH_ETF = "BOXX"                # live bot only: holds idle cash
# Live bot only: signals and sizing still come from the ETFs on the left;
# the account holds the cheaper fund on the right, which tracks the same assets.
LIVE_FUNDS = {"SPY": "VOO", "EFA": "IEFA", "EEM": "IEMG", "TIP": "SCHP", "GLD": "GLDM"}

# Spec version 4: version 3 at 1.5 times the size. Exposure above 100% of
# equity is held through these 2x funds (each one returns about twice its
# 1x ETF's daily move).
LEVERAGE_V4 = 1.5
TWO_X = {"SPY": "SSO", "EFA": "EFO", "EEM": "EET", "VNQ": "URE",
         "IEF": "UST", "TLT": "UBT", "GLD": "UGL"}
# Yearly cost of each 2x fund above "2 x the 1x ETF's return, minus T-bill
# interest on the borrowed half", measured over each fund's full history to
# Nov 20, 2026 by tools/two_x_costs.py. It covers fees and tracking costs.
TWO_X_COST = {"SSO": 0.0155, "EFO": 0.0203, "EET": 0.0165, "URE": 0.0260,
              "UST": 0.0064, "UBT": 0.0062, "UGL": 0.0251}
TBILL_TICKER = "^IRX"            # 13-week T-bill yield in percent (4.12 means 4.12%)
LOOKBACKS = (21, 63, 126, 252)   # trading days: roughly 1, 3, 6 and 12 months
VOL_WINDOW = 126                 # days of returns used to estimate portfolio volatility
TARGET_VOL = 0.10                # 10% a year, the volatility ceiling
TRADING_DAYS = 252               # trading days in a year
MAX_WEIGHT = 0.25                # no single ETF above 25% of equity
NEW_YORK = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def _drop_unfinished_days(data):
    """Remove any row dated today or later (New York time).

    During market hours Yahoo may include a row for a day that has not
    closed yet. The bot only ever uses completed closes.
    """
    today = datetime.now(NEW_YORK).date()
    keep = [stamp.date() < today for stamp in data.index]
    return data[keep]


def load_prices(start="2006-01-01", end=None, tickers=TICKERS):
    """Daily closing prices adjusted for splits and dividends, one column per ETF.

    Only dates where every ETF has a price are kept, so the table starts
    when the youngest fund (DBC) began trading.
    """
    raw = yf.download(list(tickers), start=start, end=end,
                      auto_adjust=True, progress=False)
    prices = raw["Close"][list(tickers)]
    prices = _drop_unfinished_days(prices)
    return prices.dropna()


def load_bars(start="2006-01-01", end=None, tickers=TICKERS, require=None):
    """Adjusted daily opens and closes for the ETFs, on the same dates.

    The backtest needs opens because it fills each trade at the open of
    the day after the signal.

    Dates are kept where every ETF in `require` has a price (default: all of
    `tickers`). Any other ETF stays blank (NaN) before its first trading day.
    After that day, a missing price is filled with the previous one.
    """
    require = list(tickers) if require is None else list(require)
    raw = yf.download(list(tickers), start=start, end=end,
                      auto_adjust=True, progress=False)
    opens = _drop_unfinished_days(raw["Open"][list(tickers)])
    closes = _drop_unfinished_days(raw["Close"][list(tickers)])
    keep = opens[require].notna().all(axis=1) & closes[require].notna().all(axis=1)
    return opens[keep].ffill(), closes[keep].ffill()


def load_tbill(index, start="2006-01-01", end=None):
    """13-week T-bill yield as a decimal (0.0412 means 4.12%), lined up with
    the price dates in `index`.

    The bond market closes on a few days the stock market is open, so a
    missing day takes the previous day's yield.
    """
    raw = yf.download(TBILL_TICKER, start=start, end=end,
                      auto_adjust=True, progress=False)
    yld = raw["Close"].squeeze() / 100.0
    yld = _drop_unfinished_days(yld)
    return yld.reindex(index).ffill()


# ---------------------------------------------------------------------------
# Trend scores
# ---------------------------------------------------------------------------
def tbill_growth(tbill, lookback):
    """Return from holding T-bills over each trailing window of `lookback` days.

    Each day earns that day's yield divided by 252, and the days compound.
    """
    daily_log_growth = np.log1p(tbill / TRADING_DAYS)
    return np.expm1(daily_log_growth.rolling(lookback).sum())


def trend_scores(prices, tbill, lookbacks=LOOKBACKS):
    """Trend score from 0 to 1 for each ETF on each date.

    For each lookback, an ETF gets 1 if its return over the window beat the
    T-bill return over the same window, else 0. The score is the average.

    The row for date d uses closes up to and including d. The bot trades on
    the NEXT trading day, so a backtest must move these rows forward one day.
    Rows without enough history are left blank (NaN).
    """
    signals = []
    for L in lookbacks:
        etf_return = prices / prices.shift(L) - 1          # ETF return over the last L days
        cash_return = tbill_growth(tbill, L)               # T-bill return over the same days
        beat_cash = etf_return.gt(cash_return, axis=0)     # True where the ETF beat T-bills
        has_data = etf_return.notna().mul(cash_return.notna(), axis=0).astype(bool)
        signals.append(beat_cash.astype(float).where(has_data))
    return sum(signals) / len(signals)


def score_breakdown(prices, tbill, ticker, as_of=None):
    """The four lookback tests behind one ETF's trend score on one date.

    Returns a small table (one row per lookback) and the resulting score.
    The live bot can log this to record why a position changed.
    """
    as_of = prices.index[-1] if as_of is None else pd.Timestamp(as_of)
    rows = []
    for L in LOOKBACKS:
        etf_return = (prices[ticker] / prices[ticker].shift(L) - 1).loc[as_of]
        cash_return = tbill_growth(tbill, L).loc[as_of]
        rows.append({
            "lookback_days": L,
            "etf_return": etf_return,
            "tbill_return": cash_return,
            "signal": int(etf_return > cash_return),
        })
    table = pd.DataFrame(rows).set_index("lookback_days")
    return table, table["signal"].mean()


# ---------------------------------------------------------------------------
# Position sizes
# ---------------------------------------------------------------------------
def portfolio_vol(prices, window=VOL_WINDOW):
    """Estimated yearly volatility of the fully invested, equal-weight mix,
    using the last `window` days of returns up to and including each date.

    Holding all nine at 1/9 means the mix's daily return is the plain average
    of the nine daily returns. Its standard deviation, times the square root
    of 252, is the same number the spec's covariance formula gives.
    """
    daily_returns = prices.pct_change()
    equal_weight_return = daily_returns.mean(axis=1)
    return equal_weight_return.rolling(window).std() * np.sqrt(TRADING_DAYS)


def shrink_factor(prices):
    """k from the spec: shrinks all nine weights when the mix is above 10%
    volatility. Never above 1, so the bot never borrows."""
    return (TARGET_VOL / portfolio_vol(prices)).clip(upper=1.0)


def target_weights(prices, tbill):
    """Target weight of each ETF on each date. Whatever is left is cash.

    Spec steps:
      1. every ETF starts at 1/9
      2. k shrinks all nine if the mix is above 10% volatility
      3. each weight is multiplied by that ETF's trend score
    """
    scores = trend_scores(prices, tbill)
    k = shrink_factor(prices)
    base = 1.0 / len(prices.columns)
    weights = scores.mul(k * base, axis=0)
    return weights.dropna()


def target_weights_v2(prices, tbill):
    """Spec version 2: 10% volatility as a target, not a ceiling.

      1. each ETF starts at its trend score times 1/9
      2. estimate the volatility of that mix from the last 126 days
      3. scale every weight by one factor so the mix is at 10%, up or down
      4. cap each ETF at 25%; if the total is above 100%, scale down to 100%
      5. if every score is 0, hold all cash
    """
    scores = trend_scores(prices, tbill)
    n = len(prices.columns)
    raw = (scores / n).values
    cov = (prices.pct_change().rolling(VOL_WINDOW).cov().values
           .reshape(len(prices), n, n) * TRADING_DAYS)
    var = np.einsum("ti,tij,tj->t", raw, cov, raw)
    with np.errstate(divide="ignore", invalid="ignore"):
        w = raw * (TARGET_VOL / np.sqrt(var))[:, None]
    all_off = np.nansum(raw, axis=1) == 0
    w[all_off] = 0.0
    w = np.minimum(w, MAX_WEIGHT)
    total = w.sum(axis=1, keepdims=True)
    w = np.where(total > 1.0, w / total, w)
    valid = scores.notna().all(axis=1).values & np.isfinite(var)
    return pd.DataFrame(w, index=prices.index, columns=prices.columns)[valid]


def target_weights_v5(prices, tbill):
    """Spec version 5: weak scores keep positions small.

      1. find the one factor that scales the all-nine mix (1/9 each) to 10%
         volatility, from the last 126 days
      2. each ETF gets its trend score times 1/9 times that factor
      3. cap each ETF at 25%; if the total is above 100%, scale down to 100%
    With every score at 1 this is exactly version 2. With low scores the
    positions stay small instead of being scaled back up.
    """
    scores = trend_scores(prices, tbill)
    n = len(prices.columns)
    base = np.full(n, 1.0 / n)
    cov = (prices.pct_change().rolling(VOL_WINDOW).cov().values
           .reshape(len(prices), n, n) * TRADING_DAYS)
    var = np.einsum("i,tij,j->t", base, cov, base)
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = TARGET_VOL / np.sqrt(var)
    w = scores.values * base * scale[:, None]
    w = np.minimum(w, MAX_WEIGHT)
    total = w.sum(axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        w = np.where(total > 1.0, w / total, w)
    valid = scores.notna().all(axis=1).values & np.isfinite(var)
    return pd.DataFrame(w, index=prices.index, columns=prices.columns)[valid]


def target_weights_v3(prices, tbill, scores=None, leverage=1.0):
    """Spec version 3: the version 2 sizing rule over a universe whose ETFs
    started trading on different dates.

    An ETF takes part once it has a trend score, which needs 252 days of
    prices. Before that its weight is 0. On each date, using only the ETFs
    taking part:
      1. each ETF starts at its trend score (equal base weights; the 1/N
         factor cancels out in step 3)
      2. estimate the volatility of that mix from the last 126 days
      3. scale every weight by one factor so the mix is at 10%, up or down
      4. cap each ETF at 25%; if the total is above 100%, scale down to 100%
      5. if every score is 0, hold all cash
    With every ETF present from the start, this gives the same weights as
    target_weights_v2.

    `scores` can be passed in (the always-invested benchmark uses 1 for
    every ETF taking part); by default they are the trend scores.

    `leverage` multiplies all three limits (10% volatility, 25% per ETF,
    100% in total), which multiplies every weight by the same number.
    Version 4 uses 1.5. Weights here are exposures: with leverage above 1
    they can add up to more than 100%, and split_two_x decides how to hold them.
    """
    if scores is None:
        scores = trend_scores(prices, tbill)
    target_vol = TARGET_VOL * leverage
    max_weight = MAX_WEIGHT * leverage
    max_total = 1.0 * leverage
    returns = prices.pct_change().values
    S = scores.values
    weights = np.full(S.shape, np.nan)
    for t in range(VOL_WINDOW, len(S)):
        active = ~np.isnan(S[t])
        if not active.any():
            continue
        window = returns[t - VOL_WINDOW + 1: t + 1][:, active]
        if np.isnan(window).any():
            continue
        raw = S[t, active]
        w = np.zeros(S.shape[1])
        if raw.sum() > 0:
            cov = np.cov(window, rowvar=False, ddof=1) * TRADING_DAYS
            mix_vol = np.sqrt(raw @ cov @ raw)
            w_active = np.minimum(raw * target_vol / mix_vol, max_weight)
            if w_active.sum() > max_total:
                w_active = w_active * max_total / w_active.sum()
            w[active] = w_active
        weights[t] = w
    out = pd.DataFrame(weights, index=prices.index, columns=prices.columns)
    return out.dropna(how="all")


def split_two_x(exposures):
    """Turn exposures (which may add up to more than 100%) into holdings
    that fit in 100% of equity, using the 2x funds in TWO_X.

    While total exposure is at most 100%, everything is held in 1x ETFs.
    Above that, the same share of every ETF that has a 2x fund moves into
    that fund, just enough that the holdings add up to 100%. Each dollar
    moved frees half a dollar, because the 2x fund gives twice the exposure.
    If even moving all of them is not enough, every exposure is scaled
    down by the same factor until it fits.

    Returns holdings (share of equity) with one column per 1x ETF and one
    per 2x fund. The account itself never borrows.
    """
    exp = exposures.fillna(0.0)
    lev = [t for t in exp.columns if t in TWO_X]
    total = exp.sum(axis=1)
    levered = exp[lev].sum(axis=1)
    plain = total - levered
    # capital needed if every levered ETF sat fully in its 2x fund
    floor = plain + levered / 2
    shrink = (1.0 / floor).where(floor > 1.0, 1.0)
    exp = exp.mul(shrink, axis=0)
    total, levered = total * shrink, levered * shrink
    with np.errstate(divide="ignore", invalid="ignore"):
        moved = (2 * (total - 1.0) / levered).clip(lower=0.0, upper=1.0).fillna(0.0)
    holdings = exp.copy()
    for t in lev:
        holdings[t] = exp[t] * (1 - moved)
        holdings[TWO_X[t]] = exp[t] * moved / 2
    return holdings


def two_x_bars(opens, closes, tbill):
    """Simulated daily prices for the 2x funds, built from their 1x ETFs.

    Each day a 2x fund moves twice its 1x ETF's move, minus T-bill interest
    on the borrowed half and its measured yearly cost (TWO_X_COST), both
    charged daily. Its open moves twice the 1x ETF's overnight move. The
    backtest uses these for every date, including years before the real
    funds existed; the live bot trades the real funds.
    """
    y = tbill.reindex(closes.index).ffill().bfill().shift(1).bfill()
    out_o, out_c = {}, {}
    for one, two in TWO_X.items():
        c, o = closes[one].values, opens[one].values
        cost = (y.values + TWO_X_COST[two]) / TRADING_DAYS
        c2 = np.empty(len(c))
        o2 = np.empty(len(c))
        c2[0], o2[0] = 100.0, 100.0 * o[0] / c[0]
        for i in range(1, len(c)):
            c2[i] = c2[i - 1] * (1 + 2 * (c[i] / c[i - 1] - 1) - cost[i])
            o2[i] = c2[i - 1] * (1 + 2 * (o[i] / c[i - 1] - 1))
        out_o[two], out_c[two] = o2, c2
    return (pd.DataFrame(out_o, index=closes.index),
            pd.DataFrame(out_c, index=closes.index))


def target_weights_v4(prices, tbill, scores=None):
    """Spec version 4: version 3's weights times 1.5, held through 2x
    funds where they don't fit in 100% of equity (see split_two_x)."""
    exposures = target_weights_v3(prices, tbill, scores=scores, leverage=LEVERAGE_V4)
    return split_two_x(exposures)


def exposure_of(holdings):
    """Exposure to each 1x ETF implied by holdings that include 2x funds."""
    exp = holdings[[c for c in holdings.columns if c not in TWO_X.values()]].copy()
    for one, two in TWO_X.items():
        if two in holdings.columns:
            exp[one] = exp[one] + 2 * holdings[two]
    return exp


def latest_targets(prices, tbill):
    """The newest date's trend scores and target weights, plus the cash share."""
    weights = target_weights(prices, tbill)
    scores = trend_scores(prices, tbill)
    as_of = weights.index[-1]
    table = pd.DataFrame({
        "trend_score": scores.loc[as_of],
        "target_weight": weights.loc[as_of],
    })
    cash = 1.0 - weights.loc[as_of].sum()
    return as_of, table, cash


# ---------------------------------------------------------------------------
# Lookahead check
# ---------------------------------------------------------------------------
def lookahead_test(prices, tbill, n_dates=50, seed=0, weights_fn=None):
    """Check that no rule uses future prices.

    For random cutoff dates, recompute the weights using only data up to the
    cutoff, and compare them with the full-history run on that same date.
    They must match exactly. Returns (dates checked, list of mismatched dates).
    """
    weights_fn = weights_fn or target_weights
    full = weights_fn(prices, tbill)
    rng = np.random.default_rng(seed)
    dates = sorted(rng.choice(full.index, size=n_dates, replace=False))
    mismatches = []
    for d in dates:
        cut = weights_fn(prices.loc[:d], tbill.loc[:d])
        if not np.allclose(cut.loc[d].values, full.loc[d].values, rtol=0, atol=1e-10):
            mismatches.append(pd.Timestamp(d).date())
    return len(dates), mismatches
