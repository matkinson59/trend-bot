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
TBILL_TICKER = "^IRX"            # 13-week T-bill yield in percent (4.12 means 4.12%)
LOOKBACKS = (21, 63, 126, 252)   # trading days: roughly 1, 3, 6 and 12 months
VOL_WINDOW = 126                 # days of returns used to estimate portfolio volatility
TARGET_VOL = 0.10                # 10% a year, the volatility ceiling
TRADING_DAYS = 252               # trading days in a year
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


def load_bars(start="2006-01-01", end=None, tickers=TICKERS):
    """Adjusted daily opens and closes for the ETFs, on the same dates.

    The backtest needs opens because it fills each trade at the open of
    the day after the signal.
    """
    raw = yf.download(list(tickers), start=start, end=end,
                      auto_adjust=True, progress=False)
    opens = _drop_unfinished_days(raw["Open"][list(tickers)])
    closes = _drop_unfinished_days(raw["Close"][list(tickers)])
    keep = opens.notna().all(axis=1) & closes.notna().all(axis=1)
    return opens[keep], closes[keep]


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
def lookahead_test(prices, tbill, n_dates=50, seed=0):
    """Check that no rule uses future prices.

    For random cutoff dates, recompute the weights using only data up to the
    cutoff, and compare them with the full-history run on that same date.
    They must match exactly. Returns (dates checked, list of mismatched dates).
    """
    full = target_weights(prices, tbill)
    rng = np.random.default_rng(seed)
    dates = sorted(rng.choice(full.index, size=n_dates, replace=False))
    mismatches = []
    for d in dates:
        cut = target_weights(prices.loc[:d], tbill.loc[:d])
        if not np.allclose(cut.loc[d].values, full.loc[d].values, rtol=0, atol=1e-12):
            mismatches.append(pd.Timestamp(d).date())
    return len(dates), mismatches
