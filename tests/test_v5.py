"""Version 5 sizing: the same as version 2 when every trend is on, smaller when scores are low."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import strategy as s  # noqa: E402


def prices_with(drifts, n=700, seed=3):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end="2026-11-20", periods=n)
    common = rng.normal(0, 0.008, (n, 1))               # a shared market move, so the ETFs are correlated
    r = common + rng.normal(0, 0.003, (n, len(s.TICKERS))) + np.array(drifts)
    return pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=days, columns=s.TICKERS)


def test_matches_v2_when_every_trend_is_on():
    prices = prices_with([0.003] * 9)                     # steady climbs: every score is 1
    tbill = pd.Series(0.01, index=prices.index)
    scores = s.trend_scores(prices, tbill)
    all_on = (scores == 1).all(axis=1)
    v2, v5 = s.target_weights_v2(prices, tbill), s.target_weights_v5(prices, tbill)
    dates = v5.index[all_on.reindex(v5.index)]
    assert len(dates) > 100
    assert np.allclose(v2.loc[dates], v5.loc[dates])


def test_weak_scores_stay_small():
    prices = prices_with([0.003] * 9)
    tbill = pd.Series(0.01, index=prices.index)
    prices.iloc[-21:] = prices.iloc[-22].values * np.exp(np.cumsum(np.full((21, 9), -0.004), axis=0))
    scores = s.trend_scores(prices, tbill).iloc[-1]
    assert (scores < 1).all()                            # the 1-month test now fails everywhere
    v5 = s.target_weights_v5(prices, tbill).iloc[-1]
    v2 = s.target_weights_v2(prices, tbill).iloc[-1]
    assert v5.sum() < v2.sum()
    n = len(s.TICKERS)
    cov = prices.pct_change().iloc[-s.VOL_WINDOW:].cov().values * s.TRADING_DAYS
    base = np.full(n, 1 / n)
    expected = np.minimum(scores.values * base * s.TARGET_VOL / np.sqrt(base @ cov @ base), s.MAX_WEIGHT)
    expected = expected / expected.sum() if expected.sum() > 1 else expected
    assert np.allclose(v5.values, expected)


def test_all_scores_zero_means_all_cash():
    prices = prices_with([-0.003] * 9)
    tbill = pd.Series(0.01, index=prices.index)
    v5 = s.target_weights_v5(prices, tbill)
    zero_days = (s.trend_scores(prices, tbill).reindex(v5.index) == 0).all(axis=1)
    assert zero_days.any() and (v5[zero_days] == 0).all().all()


def test_v6_is_v5_at_a_15_percent_target():
    prices = prices_with([0.003] * 9)
    tbill = pd.Series(0.01, index=prices.index)
    prices.iloc[-21:] = prices.iloc[-22].values * np.exp(np.cumsum(np.full((21, 9), -0.004), axis=0))
    v6 = s.target_weights_v6(prices, tbill)
    assert np.allclose(v6, s.target_weights_v5(prices, tbill, target_vol=0.15))
    assert (v6.sum(axis=1) <= 1 + 1e-9).all() and (v6 <= s.MAX_WEIGHT + 1e-12).all().all()
    assert v6.iloc[-1].sum() > s.target_weights_v5(prices, tbill).iloc[-1].sum()
