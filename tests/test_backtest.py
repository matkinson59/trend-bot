"""Checks that the backtest engine keeps its books straight.

Run from the repo folder with:  python -m pytest tests
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest as bt  # noqa: E402
import strategy as s  # noqa: E402

opens, closes = s.load_bars()
tbill = s.load_tbill(closes.index)
weekly = bt.first_day_of_week(closes.index)
targets = s.target_weights(closes, tbill)
scores = s.trend_scores(closes, tbill)
start = weekly[weekly & (weekly.index > targets.index[0])].index[0]
daily, trades, episodes = bt.simulate(targets, scores, opens, closes, tbill, weekly, start)


def test_never_borrows():
    assert daily["cash"].min() >= -1e-6
    assert ((1 - daily["cash"] / daily["equity"]) <= 1 + 1e-9).all()


def test_dollars_reconcile():
    """Total gain equals position P&L (after costs) plus interest on cash."""
    interest = (daily["cash"].shift(1) * tbill.shift(1).reindex(daily.index) / s.TRADING_DAYS).sum()
    gain = daily["equity"].iloc[-1] - bt.START_VALUE
    assert np.isclose(gain, episodes["pnl"].sum() + interest, atol=0.01)


def test_buy_and_hold_matches_hand_calculation():
    only_start = pd.Series(closes.index == start, index=closes.index)
    weights = {**{t: 0.0 for t in closes.columns}, "SPY": 1.0}
    b3 = bt.simulate(bt.constant_targets(closes, weights), None, opens, closes, tbill,
                     only_start, start, buffer=0.0)[0]
    hand = bt.START_VALUE * closes["SPY"].iloc[-1] / (opens["SPY"].loc[start] * (1 + bt.COST_BPS / 1e4))
    assert np.isclose(b3["equity"].iloc[-1], hand, rtol=1e-9)


def test_trades_only_on_weekly_trade_days():
    trade_dates = pd.to_datetime(trades["date"].unique())
    assert weekly.loc[trade_dates].all()
