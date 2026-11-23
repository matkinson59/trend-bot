"""Checks for spec version 3 (13 ETFs that start trading on different dates).

These test the rules and the bookkeeping only. They do not print or check
any performance numbers.

Run from the repo folder with:  python -m pytest tests
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest as bt  # noqa: E402
import strategy as s  # noqa: E402

opens, closes = s.load_bars(tickers=s.TICKERS_V3, require=s.TICKERS)
tbill = s.load_tbill(closes.index)
scores = s.trend_scores(closes, tbill)
weights = s.target_weights_v3(closes, tbill)


def test_matches_v2_when_every_etf_is_present():
    core = closes[s.TICKERS]
    v2 = s.target_weights_v2(core, tbill)
    v3 = s.target_weights_v3(core, tbill).reindex(v2.index)
    assert np.allclose(v2.values, v3.values, rtol=0, atol=1e-10)


def test_no_lookahead_v3():
    checked, mismatches = s.lookahead_test(closes, tbill, n_dates=30,
                                           weights_fn=s.target_weights_v3)
    assert checked == 30
    assert mismatches == [], f"v3 rules used future data on {mismatches}"


def test_weights_stay_in_bounds():
    assert (weights >= 0).all().all(), "a negative weight would be a short"
    assert (weights.sum(axis=1) <= 1.0 + 1e-9).all(), "weights above 100% would mean borrowing"
    assert (weights <= s.MAX_WEIGHT + 1e-12).all().all(), "an ETF above 25%"


def test_new_etfs_wait_for_a_full_year():
    for ticker in ["UUP", "FXY", "DBB", "DBA"]:
        first_price = closes[ticker].first_valid_index()
        days_before_score = closes.loc[first_price:].index.get_loc(scores[ticker].first_valid_index())
        assert days_before_score == max(s.LOOKBACKS)
        held_early = weights.loc[:scores[ticker].first_valid_index(), ticker].iloc[:-1]
        assert (held_early == 0).all(), f"{ticker} held before it had a full year of prices"


def test_engine_books_balance_with_late_starters():
    weekly = bt.first_day_of_week(closes.index)
    start = weekly[weekly & (weekly.index > s.target_weights(closes[s.TICKERS], tbill).index[0])].index[0]
    daily, trades, episodes = bt.simulate(weights, scores, opens.bfill(), closes.bfill(),
                                          tbill, weekly, start)
    assert daily["cash"].min() >= -1e-6
    assert ((1 - daily["cash"] / daily["equity"]) <= 1 + 1e-9).all()
    interest = (daily["cash"].shift(1) * tbill.shift(1).reindex(daily.index) / s.TRADING_DAYS).sum()
    gain = daily["equity"].iloc[-1] - bt.START_VALUE
    assert np.isclose(gain, episodes["pnl"].sum() + interest, atol=0.01)
    for ticker in ["UUP", "FXY", "DBB", "DBA"]:
        first_trade = trades.loc[trades["ticker"] == ticker, "date"].min()
        assert first_trade > scores[ticker].first_valid_index().date()
