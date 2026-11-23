"""Checks for spec version 4 (version 3 at 1.5 times the size, through 2x funds).

These test the rules and the bookkeeping only. They do not print or check
any performance numbers.

Run from the repo folder with:  python -m pytest tests
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest as bt  # noqa: E402
import strategy as s  # noqa: E402

opens, closes = s.load_bars(tickers=s.TICKERS_V3, require=s.TICKERS)
tbill = s.load_tbill(closes.index)
v3 = s.target_weights_v3(closes, tbill)
holdings = s.target_weights_v4(closes, tbill)
exposure = s.exposure_of(holdings)


def test_holdings_never_borrow_or_short():
    assert (holdings >= -1e-12).all().all()
    assert (holdings.sum(axis=1) <= 1.0 + 1e-9).all()


def test_exposure_is_v3_times_one_and_a_half_or_scaled_down_evenly():
    target = v3 * s.LEVERAGE_V4
    ratio = (exposure / target).where(target > 1e-9)
    assert (ratio.max(axis=1) <= 1 + 1e-9).all()
    spread = (ratio.max(axis=1) - ratio.min(axis=1)).fillna(0)
    assert (spread < 1e-9).all(), "every ETF must be scaled by the same factor"
    fits = (target[[t for t in target.columns if t not in s.TWO_X]].sum(axis=1)
            + target[list(s.TWO_X)].sum(axis=1) / 2) <= 1.0
    assert np.allclose(ratio[fits].fillna(1.0).values, 1.0), "scaled down when it would have fit"


def test_two_x_funds_only_used_above_100_percent():
    two = holdings[list(s.TWO_X.values())].sum(axis=1)
    below = exposure.sum(axis=1) <= 1.0 + 1e-12
    assert (two[below] < 1e-12).all()
    above = exposure.sum(axis=1) > 1.0 + 1e-6
    assert np.allclose(holdings[above].sum(axis=1), 1.0)


def test_no_lookahead_v4():
    checked, mismatches = s.lookahead_test(closes, tbill, n_dates=30,
                                           weights_fn=s.target_weights_v4)
    assert checked == 30
    assert mismatches == [], f"v4 rules used future data on {mismatches}"


def test_simulated_two_x_follows_its_formula():
    o2, c2 = s.two_x_bars(opens, closes, tbill)
    r1 = closes["SPY"].pct_change()
    r2 = c2["SSO"].pct_change()
    y = tbill.shift(1).reindex(closes.index).bfill()
    expected = 2 * r1 - (y + s.TWO_X_COST["SSO"]) / s.TRADING_DAYS
    assert np.allclose(r2.iloc[2:].values, expected.iloc[2:].values, atol=1e-12)


def test_engine_books_balance_with_two_x_funds():
    o2, c2 = s.two_x_bars(opens, closes, tbill)
    o_all = pd.concat([opens.bfill(), o2], axis=1)
    c_all = pd.concat([closes.bfill(), c2], axis=1)
    sc = s.trend_scores(closes, tbill)
    sc_all = pd.concat([sc, sc[list(s.TWO_X)].rename(columns=s.TWO_X)], axis=1)
    weekly = bt.first_day_of_week(closes.index)
    start = weekly[weekly & (weekly.index > s.target_weights(closes[s.TICKERS], tbill).index[0])].index[0]
    daily, trades, episodes = bt.simulate(holdings, sc_all, o_all, c_all, tbill, weekly, start)
    assert daily["cash"].min() >= -1e-6
    interest = (daily["cash"].shift(1) * tbill.shift(1).reindex(daily.index) / s.TRADING_DAYS).sum()
    gain = daily["equity"].iloc[-1] - bt.START_VALUE
    assert np.isclose(gain, episodes["pnl"].sum() + interest, atol=0.01)
