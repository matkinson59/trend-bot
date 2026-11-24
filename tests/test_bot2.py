"""Checks for bot 2's backtest (bot2_backtest.py).

Run from the repo folder with:  python -m pytest tests/test_bot2.py
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest as bt  # noqa: E402
import bot2_backtest as b2  # noqa: E402
import strategy as s  # noqa: E402


@pytest.fixture(scope="module")
def data():
    tickers = list(dict.fromkeys(s.TICKERS + b2.SECTORS))
    opens, closes = s.load_bars(end=b2.END, tickers=tickers, require=s.TICKERS + b2.SECTORS_CORE)
    tbill = s.load_tbill(closes.index, end=b2.END)
    return opens, closes, tbill


def random_rows(n=600, seed=1, scale=0.004):
    rng = np.random.default_rng(seed)
    excess = rng.normal(0.0003, scale, n)
    invested = rng.uniform(0.3, 1.0, n)
    rf = np.full(n, 0.04 / 252)
    spread = np.full(n, 0.0155)
    return [excess, invested, rf, spread]


def test_overlay_at_1x_without_floor_is_the_base():
    rows = random_rows()
    R, mult = b2.overlay(rows, 1.0, floor=False)
    assert np.allclose(mult, 1.0)
    # day 0 also pays the cost of building the position; after that R is the base return
    assert np.allclose(R[1:], rows[2][1:] + rows[0][1:])


def test_floor_factor():
    assert b2.floor_factor(1.0, 1.0) == pytest.approx(1.0)
    assert b2.floor_factor(0.875, 1.0) == pytest.approx(4 * (1 - 0.75 / 0.875))
    assert b2.floor_factor(0.75, 1.0) == pytest.approx(0.0)
    assert b2.floor_factor(0.60, 1.0) == pytest.approx(0.0)
    assert b2.floor_factor(1.2, 1.0) == pytest.approx(1.0)


def test_floor_holds_the_cap_without_large_gaps():
    rows = random_rows(n=3000, seed=2, scale=0.006)
    rows[0] = np.clip(rows[0] - 0.0008, -0.01, 0.01)          # a losing strategy, no big single days
    R, _ = b2.overlay(rows, 3.0)
    wealth = np.cumprod(1 + R)
    worst = (wealth / np.maximum.accumulate(wealth) - 1).min()
    assert worst > -0.25 - 0.02


def test_exposure_never_above_2x():
    rows = random_rows()
    rows[1] = np.full(len(rows[0]), 1.0)
    _, mult = b2.overlay(rows, 3.0)
    assert (mult * rows[1] <= b2.MAX_EXPOSURE + 1e-12).all()


def test_bootstrap_paths_in_range():
    rng = np.random.default_rng(0)
    idx = b2.stationary_indices(500, 2520, 200, 20.0, rng)
    assert idx.min() >= 0 and idx.max() < 500
    starts = (np.diff(idx, axis=0) != 1) & ~((idx[:-1] == 499) & (idx[1:] == 0))
    assert 15 < 1 / starts.mean() < 25                         # mean block near 20


def test_b3_bounds(data):
    _, closes, tbill = data
    w, held = b2.b3_targets(closes, tbill)
    assert (held.sum(axis=1) <= 3).all()
    assert (w >= 0).all().all()
    assert (w <= 1 / 3 + 1e-12).all().all()
    assert (w.sum(axis=1) <= 1 + 1e-12).all()
    assert (w.where(held == 0, 0.0) == 0).all().all()


def _cutoffs(index, n=25, seed=0):
    rng = np.random.default_rng(seed)
    return sorted(rng.choice(index[400:], size=n, replace=False))


def test_no_lookahead_b2_b3(data):
    _, closes, tbill = data
    full2, _ = b2.b2_targets(closes)
    full3, _ = b2.b3_targets(closes, tbill)
    for d in _cutoffs(full3.index):
        cut2, _ = b2.b2_targets(closes.loc[:d])
        cut3, _ = b2.b3_targets(closes.loc[:d], tbill.loc[:d])
        assert np.allclose(cut2.loc[d].values, full2.loc[d].values, atol=1e-12), d
        assert np.allclose(cut3.loc[d].values, full3.loc[d].values, atol=1e-12), d


def test_no_lookahead_b4_b5(data):
    _, closes, tbill = data

    def build(c, t):
        weekly = bt.first_day_of_week(c.index)
        monthly = bt.first_day_of_month(c.index)
        c9 = c[s.TICKERS]
        rows0, sc0 = s.target_weights_v2(c9, t), s.trend_scores(c9, t)
        rows2, _ = b2.b2_targets(c)
        rows3, _ = b2.b3_targets(c, t)
        w4, _ = b2.b4_used(rows0, sc0, c["SPY"], weekly)
        w5 = b2.b5_used([(rows0, weekly), (rows2, weekly), (rows3, monthly)], c.index)
        return w4, w5

    full4, full5 = build(closes, tbill)
    for d in _cutoffs(full5.index, n=12, seed=3):
        cut4, cut5 = build(closes.loc[:d], tbill.loc[:d])
        assert np.allclose(cut4.loc[d].values, full4.loc[d].values, atol=1e-12), d
        assert np.allclose(cut5.loc[d].values, full5.loc[d].reindex(cut5.columns).values, atol=1e-12), d


def test_b4_and_b5_stay_within_100_percent(data):
    _, closes, tbill = data
    weekly = bt.first_day_of_week(closes.index)
    monthly = bt.first_day_of_month(closes.index)
    c9 = closes[s.TICKERS]
    rows0, sc0 = s.target_weights_v2(c9, tbill), s.trend_scores(c9, tbill)
    w4, _ = b2.b4_used(rows0, sc0, closes["SPY"], weekly)
    rows2, _ = b2.b2_targets(closes)
    rows3, _ = b2.b3_targets(closes, tbill)
    w5 = b2.b5_used([(rows0, weekly), (rows2, weekly), (rows3, monthly)], closes.index)
    for w in (w4, w5):
        assert (w >= -1e-12).all().all()
        assert (w.sum(axis=1) <= 1 + 1e-9).all()


def test_b0_base_reproduces_version_2():
    base, _, _ = b2.run_bases()
    end_value = base["B0"]["daily"]["equity"].iloc[-1]
    v2 = pd.read_csv("results/v2/equity.csv", index_col="date")["trend_bot_v2"].iloc[-1]
    assert end_value == pytest.approx(v2, abs=1.0)
